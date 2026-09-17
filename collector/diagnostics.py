"""Bounded local diagnostics: no free-form strings ever enter the report.

The schema is reapplied on disk reads and export, not just at call sites.
Logging is best effort; it must never replace an application's exception.
"""
from functools import wraps
import errno
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import threading
import time

LIMIT = 256 * 1024
MAX_EVENTS = 800
EVENTS = frozenset(('app.start', 'app.close', 'app.init', 'gamelogs.inspect',
    'capture.start', 'capture.poll', 'capture.stop', 'logs.open', 'logs.paths',
    'pair.start', 'pair.poll', 'pair.redeem', 'pair.browser', 'pair.wait', 'pair.failed',
    'credential.load', 'credential.save', 'upload.send', 'upload.ack',
    'upload.retry', 'http.response', 'export', 'http.connect_tls', 'http.headers', 'http.read', 'http.complete',
    'site.click', 'site.redeem', 'site.pending.load', 'site.pending.save',
    'site.pending.archive', 'site.review', 'site.retry'))
SITE_ERRORS = frozenset(('invalid_request', 'not_member', 'scope_consent_required',
    'invalid_grant', 'active_installation_limit', 'expired_token', 'slow_down',
    'membership_unavailable', 'collector_capacity', 'temporarily_unavailable',
    'pairing_recovery_unavailable', 'authority_busy', 'authority_unavailable', 'not_found'))
OUTCOMES = frozenset(('start', 'ok', 'error', 'pending', 'unavailable', 'missing'))
ERRORS = frozenset(('permission', 'missing', 'not_directory', 'disk_full',
    'sharing_violation', 'timeout', 'tls', 'network', 'encoding', 'protocol',
    'queue_full', 'file_limit', 'unsafe_file', 'changed_file', 'os_error', 'internal'))
CLASSES = frozenset(('PermissionError', 'FileNotFoundError', 'NotADirectoryError',
    'TimeoutError', 'SSLError', 'OSError', 'UnicodeDecodeError', 'ProtocolError',
    'HTTPFailure', 'SiteCodeFailure', 'QueueFull', 'ReadFailure', 'HTTPException'))
NUMBERS = frozenset(('errno', 'winerror', 'status', 'count', 'accepted', 'rejected',
    'failures', 'delay', 'exists', 'accessible', 'unattributed', 'windows_major',
    'windows_minor', 'windows_build', 'duration_ms', 'correlation'))
_sink = None


def clean(record):
    if type(record) is not dict or type(record.get('event')) is not str or record['event'] not in EVENTS:
        return None
    stamp = record.get('time')
    if type(stamp) is not int or not 0 <= stamp <= 9999999999:
        return None
    out = {'time': stamp, 'event': record['event']}
    for key, allowed in (('outcome', OUTCOMES), ('error', ERRORS), ('exception', CLASSES), ('site_error', SITE_ERRORS)):
        if type(record.get(key)) is str and record[key] in allowed:
            out[key] = record[key]
    for key in NUMBERS:
        value = record.get(key)
        if type(value) is int and 0 <= value <= 2147483647:
            out[key] = value
    version = record.get('version')
    if type(version) is str and re.fullmatch(r'\d{1,3}\.\d{1,3}\.\d{1,3}', version):
        out['version'] = version
    return out


def exception_fields(exc):
    # Never stringify exceptions, use their dynamic class names, args or traceback.
    name = type(exc).__name__
    category = 'internal'
    number = getattr(exc, 'errno', None)
    winerror = getattr(exc, 'winerror', None)
    # Native file APIs can supply winerror without a useful POSIX errno.
    # Use only numeric codes, never localized messages or private filenames.
    native = {2: 'missing', 3: 'missing', 5: 'permission',
              32: 'sharing_violation', 33: 'sharing_violation',
              39: 'disk_full', 112: 'disk_full', 267: 'not_directory'}
    if isinstance(exc, TimeoutError): category = 'timeout'
    elif type(winerror) is int and winerror in native: category = native[winerror]
    elif number in (errno.EACCES, errno.EPERM): category = 'permission'
    elif number == errno.ENOENT: category = 'missing'
    elif number == errno.ENOTDIR: category = 'not_directory'
    elif number == errno.ENOSPC: category = 'disk_full'
    elif isinstance(exc, UnicodeError): category = 'encoding'
    elif name == 'SSLError': category = 'tls'
    elif name in ('ProtocolError', 'HTTPFailure', 'SiteCodeFailure'): category = 'protocol'
    elif name == 'QueueFull': category = 'queue_full'
    elif name == 'ReadFailure': category = getattr(exc, 'reason', 'os_error')
    elif isinstance(exc, ConnectionError) or name in ('gaierror', 'HTTPException'): category = 'network'
    elif isinstance(exc, OSError): category = 'os_error'
    return {'error': category, 'exception': name if name in CLASSES else 'OSError' if isinstance(exc, OSError) else None,
            'errno': number, 'winerror': winerror, 'status': getattr(exc, 'status', None),
            'site_error': getattr(exc, 'code', None) if name == 'SiteCodeFailure' else None}


def emit(event, outcome='ok', error=None, **fields):
    try:
        if _sink is not None:
            if error is not None:
                fields.update(exception_fields(error))
            _sink.record(event, outcome, **fields)
    except Exception:
        pass


def observed(event, successes=True):
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            if successes: emit(event, 'start')
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                emit(event, 'error', error=exc)
                raise
            if successes: emit(event, 'missing' if result is None and event in ('credential.load', 'site.pending.load') else 'pending' if result is None and event == 'pair.poll' else 'ok')
            return result
        return run
    return decorate


def safe_parent(path):
    for parent in (path, *path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise OSError('Unsafe diagnostic directory')


def atomic(path, raw):
    safe_parent(path.parent)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise OSError('Unsafe diagnostic target')
    fd, temporary = tempfile.mkstemp(prefix='.support-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


class Diagnostics:
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        self.path = self.directory / 'history.json'
        self.lock = threading.RLock()
        self.events = []
        self.available = True
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            safe_parent(self.directory)
            from .core import safe_open
            try:
                with safe_open.__wrapped__(self.path) as stream:
                    info = os.fstat(stream.fileno())
                    if info.st_nlink != 1 or (os.name != 'nt' and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600)):
                        raise OSError('Unsafe diagnostic permissions')
                    raw = stream.read(LIMIT + 1)
                if len(raw) > LIMIT: raise ValueError()
                records = json.loads(raw)
                if type(records) is not list: raise ValueError()
                self.events = [c for r in records[-MAX_EVENTS:] if (c := clean(r))]
            except FileNotFoundError:
                pass
        except Exception:
            self.available = False

    def record(self, event, outcome='ok', **fields):
        with self.lock:
            record = clean(dict(fields, event=event, outcome=outcome, time=int(time.time())))
            if record is None: return
            self.events.append(record)
            while len(self.events) > MAX_EVENTS:
                self.trim()
            raw = json.dumps(self.events, separators=(',', ':'), sort_keys=True).encode()
            while len(raw) > LIMIT:
                self.trim()
                raw = json.dumps(self.events, separators=(',', ':'), sort_keys=True).encode()
            try:
                atomic(self.path, raw)
                self.available = True
            except Exception:
                self.available = False

    def trim(self):
        # Keep the last real ACK even if a long outage rotates older history.
        last = next((i for i in range(len(self.events)-1, -1, -1)
                     if self.events[i]['event'] == 'upload.ack' and self.events[i].get('accepted', 0)), -1)
        self.events.pop(1 if last == 0 else 0)

    def inspect_logs(self, root):
        try:
            count = 0
            with os.scandir(root) as entries:
                for entry in entries:
                    if os.path.normcase(entry.name).endswith('.txt'):
                        count += 1
                        if count >= 513: break
            self.record('gamelogs.inspect', exists=1, accessible=1, count=count)
        except Exception as exc:
            fields = exception_fields(exc)
            self.record('gamelogs.inspect', 'error', exists=0 if fields['error'] == 'missing' else 1,
                        accessible=0, **fields)

    def export(self, target):
        target = Path(target).absolute()
        # Never overwrite queues, credentials, source logs, or diagnostic history.
        if target.suffix.lower() != '.json' or target.resolve().is_relative_to(self.directory.parent.resolve()):
            raise ValueError('Choose a JSON report outside collector state')
        with self.lock:
            from .version import VERSION
            system = {'version': VERSION, 'windows': os.name == 'nt'}
            if hasattr(sys, 'getwindowsversion'):
                windows = getattr(sys, 'getwindowsversion')()
                system.update(windows_major=windows.major, windows_minor=windows.minor, windows_build=windows.build)
            report = {'schema': 1, 'system': system, 'persistent_history_available': self.available,
                      'events': [c for r in self.events if (c := clean(r))]}
            atomic(target, json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True).encode())


def initialize(state):
    global _sink
    _sink = Diagnostics(Path(state) / 'diagnostics')
    from .version import VERSION
    fields = {'version': VERSION}
    if hasattr(sys, 'getwindowsversion'):
        windows = getattr(sys, 'getwindowsversion')()
        fields.update(windows_major=windows.major, windows_minor=windows.minor, windows_build=windows.build)
    emit('app.start', **fields)
    return _sink
