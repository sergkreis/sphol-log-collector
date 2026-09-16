"""Local-only, combat-only collector. No networking or game interaction."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from copy import deepcopy
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid
from .diagnostics import observed


class ReadFailure(OSError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason

MAX_LINE = 8192
LINE = re.compile(r'^\[\s*(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s*\]\s*\(combat\)\s*(.+)$')
TAG = re.compile(r'<[^>]*>')


def parse_line(raw: bytes, listener: str = '') -> dict | None:
    if len(raw) > MAX_LINE:
        return None
    try:
        match = LINE.fullmatch(raw.decode('utf-8-sig').strip())
        if not match:
            return None
        when = datetime.strptime(match[1], '%Y.%m.%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except (UnicodeError, ValueError):
        return None
    text = html.unescape(TAG.sub('', match[2]))
    text = ''.join(c for c in text if c.isprintable()).strip()
    if not text:
        return None
    return {'schema': 1, 'time': when.isoformat(), 'type': 'combat',
            'listener': listener[:128], 'text': text}


@observed('logs.open', successes=False)
def safe_open(path: Path):
    """Reject symlinks, devices and path swaps; never open a game file writable."""
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or path.is_symlink()
            or getattr(before, 'st_file_attributes', 0) & 0x400):
        raise ReadFailure('unsafe_file')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    try:
        after = os.fstat(fd)
        if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ReadFailure('changed_file')
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


PENDING_CAPTURE_WARNING = ('Не удалось восстановить ожидающие события: исходный журнал исчез или изменён. '
                           'Сбор остановлен; очередь и сведения восстановления сохранены. Сохраните отчёт для поддержки.')


class PendingCaptureUnavailable(ReadFailure):
    def __init__(self):
        super().__init__('changed_file')


class QueueFull(Exception):
    pass


class PendingQueue:
    """Persistent IDs, atomic insertion, explicit ACK-only deletion; no transport."""
    def __init__(self, path: Path, max_events=10000, max_bytes=16 * 1024 * 1024):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
            raise OSError('Unsafe queue path')
        self.db = sqlite3.connect(self.path)
        os.chmod(self.path, 0o600)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS pending (id TEXT PRIMARY KEY, payload TEXT NOT NULL, size INTEGER NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS capture_checkpoint (key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS delivery_retry (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, until REAL NOT NULL)')
        self.db.commit()
        self._capturing = False
        self.max_events, self.max_bytes = max_events, max_bytes
        self.inserted_count = 0  # Process-local, successful new commits only.

    def put(self, event_id: str, event: dict):
        payload = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
        size = len(payload.encode('utf-8'))
        if self._capturing:
            self._insert(event_id, payload, size)
        else:
            before = self.inserted_count
            try:
                with self.db:
                    self.db.execute('BEGIN IMMEDIATE')
                    self._insert(event_id, payload, size)
            except BaseException:
                self.inserted_count = before
                raise

    def _insert(self, event_id, payload, size):
        previous = self.db.execute('SELECT payload FROM pending WHERE id=?', (event_id,)).fetchone()
        if previous:
            if previous[0] != payload:
                raise ValueError('Pending event identity conflict; original retained')
            return
        count, used = self.db.execute('SELECT count(*), coalesce(sum(size),0) FROM pending').fetchone()
        if count >= self.max_events or used + size > self.max_bytes:
            raise QueueFull('Queue full; collection paused. Nothing uploaded.')
        self.db.execute('INSERT INTO pending VALUES (?,?,?)', (event_id, payload, size))
        self.inserted_count += 1

    def checkpoint(self, key, payload):
        self.db.execute('INSERT OR REPLACE INTO capture_checkpoint VALUES (?,?)',
                        (key, json.dumps(payload, separators=(',', ':'))))

    def end_capture(self):
        # Stop never reads more bytes. Keep every observed, unparsed interval.
        with self.db:
            for key, payload in self.db.execute('SELECT key,payload FROM capture_checkpoint').fetchall():
                saved = json.loads(payload)
                saved['files'] = [item for item in saved['files'] if item[1][0] < item[2]]
                if saved['files']:
                    self.checkpoint(key, saved)
                else:
                    self.db.execute('DELETE FROM capture_checkpoint WHERE key=?', (key,))

    def batch(self, limit=100, listeners=None):
        result = []
        limit = min(max(limit, 0), 100)
        if not limit:
            return result
        for identity, payload in self.db.execute('SELECT id,payload FROM pending ORDER BY rowid'):
            event = json.loads(payload)
            if listeners is not None and event.get('listener') not in listeners:
                continue
            result.append({**event, 'id': identity})
            if len(result) >= limit:
                break
        return result

    def count(self):
        return self.db.execute('SELECT count(*) FROM pending').fetchone()[0]

    def retry_states(self):
        return {key: (failures, until) for key, failures, until in
                self.db.execute('SELECT key,failures,until FROM delivery_retry')}

    def save_retry(self, key, failures, until):
        self.complete_upload([], {key: (failures, until)})

    def complete_upload(self, ids, retries):
        # ACK deletion and retry metadata commit together on the owning thread.
        with self.db:
            self.db.executemany('DELETE FROM pending WHERE id=?', [(i,) for i in ids])
            self.db.executemany('INSERT OR REPLACE INTO delivery_retry VALUES (?,?,?)',
                                [(key, *state) for key, state in retries.items()])

    def acknowledge(self, ids):
        # Invoke ONLY after authenticated per-ID durable ACK.
        self.complete_upload(ids, {})

    def clear(self):
        with self.db:
            self.db.execute('DELETE FROM pending')
            self.db.execute('DELETE FROM capture_checkpoint')
        self.db.execute('VACUUM')

    def close(self):
        self.db.close()


class Tailer:
    """One foreground capture session. Existing files start at EOF, not history."""
    @observed('capture.start')
    def __init__(self, root: Path, queue: PendingQueue, started=None, parser=parse_line):
        self.root = Path(root).resolve()
        self.queue = queue
        self.parser = parser
        self.started = started or datetime.now(timezone.utc)
        self.run_id = uuid.uuid4().hex
        self.files = {}
        self.rejected = 0
        self.checkpoint_key = hashlib.sha256(
            (str(self.root) + ':' + parser.__module__ + ':' + parser.__name__ + ':1').encode()).hexdigest()
        self.stopped = False
        self.anchors = {}
        self.observed = {}
        self.recovery = {}
        self.recovery_started = {}
        previous = self.queue.db.execute('SELECT payload FROM capture_checkpoint WHERE key=?',
                                         (self.checkpoint_key,)).fetchone()
        saved = json.loads(previous[0]) if previous else None
        if not self.root.is_dir():
            if saved and any(item[1][0] < item[2] for item in saved['files']):
                raise PendingCaptureUnavailable()
            raise NotADirectoryError(20, 'Gamelogs directory is missing')
        self.baselines = {}
        self.fingerprints = {}
        prefixes = {}
        sources = {}
        for p in self.paths():
            with safe_open(p) as f:
                s = os.fstat(f.fileno())
                listener = self.listener(f)
                # Do not capture the continuation of a line begun before Start.
                f.seek(max(0, s.st_size - 1))
                partial = s.st_size > 0 and f.read(1) != b'\n'
                key = (s.st_dev, s.st_ino)
                sources[key] = p
                self.files[key] = [s.st_size, listener, 0, partial]
                self.baselines[key] = list(self.files[key])
                f.seek(0)
                prefix = f.read(min(256, s.st_size))
                prefixes[key] = prefix
                self.fingerprints[key] = [len(prefix), hashlib.sha256(prefix).hexdigest()]
        if saved:
            self.run_id = saved['run_id']
            for item in saved['files']:
                key, state, ceiling = tuple(item[0]), item[1], item[2]
                if key in self.files:
                    self.files[key][2] = state[2] + 1
                    self.baselines[key][2] = state[2] + 1
                if state[0] >= ceiling:
                    continue
                fingerprint = item[3]
                anchor = item[5] if len(item) > 5 else None
                valid_anchor = True
                if anchor and key in sources:
                    with safe_open(sources[key]) as f:
                        valid_anchor = self._matches(f, anchor)
                if (valid_anchor and key in self.files and state[0] <= ceiling <= self.files[key][0]
                        and fingerprint[1] == hashlib.sha256(prefixes[key][:fingerprint[0]]).hexdigest()):
                    self.files[key] = state
                    self.recovery[key] = ceiling
                    self.recovery_started[key] = datetime.fromisoformat(item[4])
                    self.fingerprints[key] = fingerprint
                    if anchor:
                        self.anchors[key] = anchor
                else:
                    raise PendingCaptureUnavailable()
        self.observed = dict(self.recovery)

    @staticmethod
    def _matches(f, anchor):
        offset, length, digest = anchor
        f.seek(offset)
        return hashlib.sha256(f.read(length)).hexdigest() == digest

    def _anchor(self, f, key):
        end = self.files[key][0]
        offset = max(0, end - 256)
        f.seek(offset)
        self.anchors[key] = [offset, end - offset, hashlib.sha256(f.read(end - offset)).hexdigest()]

    def _checkpoint(self, ceilings):
        self.queue.checkpoint(self.checkpoint_key, {
            'run_id': self.run_id, 'started': self.started.isoformat(),
            'files': [[list(key), state, ceilings.get(key, state[0]), self.fingerprints.get(key, [0, hashlib.sha256(b'').hexdigest()]),
                       self.recovery_started.get(key, self.started).isoformat(), self.anchors.get(key)]
                      for key, state in self.files.items()]})

    def stop(self):
        self.stopped = True
        self.queue.end_capture()

    @observed('logs.paths', successes=False)
    def paths(self):
        # No recursion; never enumerate Chatlogs. Bound resource use visibly.
        paths = []
        with os.scandir(self.root) as entries:
            for entry in entries:
                if not os.path.normcase(entry.name).endswith('.txt'):
                    continue
                p = self.root / entry.name
                if p.is_symlink() or p.resolve().parent != self.root:
                    continue
                paths.append(p)
                if len(paths) > 512:
                    raise ReadFailure('file_limit')
        return sorted(paths)

    @staticmethod
    def listener(f):
        f.seek(0)
        # Header only: never mistake a later event's text for an identity.
        names = set()
        for _ in range(256):
            raw = f.readline(MAX_LINE + 1)
            if not raw:
                return next(iter(names)) if len(names) == 1 else ''
            if not raw.endswith(b'\n'):
                return ''  # A partially written header is retried, never cached.
            if len(raw) > MAX_LINE:
                return ''
            try:
                line = raw.decode('utf-8-sig').strip()
            except UnicodeError:
                return ''
            if line.startswith('['):
                return next(iter(names)) if len(names) == 1 else ''
            if line.startswith(('Listener:', 'Слушатель:')):
                name = line.split(':', 1)[1].strip()
                if not name or len(name) > 128 or not all(c.isprintable() for c in name):
                    return ''
                names.add(name)
            # A closing separator proves the header is complete even before events.
            if names and line and set(line) == {'-'}:
                return next(iter(names)) if len(names) == 1 else ''
            if line.startswith(('Session started:', 'Сеанс начат:')):
                return next(iter(names)) if len(names) == 1 else ''

        return ''

    @observed('capture.poll', successes=False)
    def poll(self):
        # Persist the consented read envelope before reading. On restart only
        # this envelope can be replayed; bytes written while closed are skipped.
        if self.stopped:
            return 0
        ceilings = {}
        seen = set()
        for p in self.paths():
            try:
                with safe_open(p) as f:
                    s = os.fstat(f.fileno())
                    key = (s.st_dev, s.st_ino)
                    seen.add(key)
                    if self.recovery and key not in self.recovery:
                        continue
                    if key in self.files:
                        pending = self.observed.get(key, 0) > self.files[key][0]
                        fp = self.fingerprints[key]
                        changed = not self._matches(f, [0, *fp])
                        anchor = self.anchors.get(key)
                        changed = changed or bool(anchor and not self._matches(f, anchor))
                        if (pending and s.st_size < self.observed[key]) or (changed and (pending or s.st_size >= self.files[key][0])):
                            raise PendingCaptureUnavailable()
                    if key not in self.files:
                        if len(self.files) >= 1024:
                            raise ReadFailure('file_limit')
                        self.files[key] = [0, self.listener(f), 0, False]
                        f.seek(0)
                        prefix = f.read(min(256, s.st_size))
                        self.fingerprints[key] = [len(prefix), hashlib.sha256(prefix).hexdigest()]
                    elif s.st_size < self.files[key][0]:
                        self.files[key] = [0, self.listener(f), self.files[key][2] + 1, False]
                        self.anchors.pop(key, None)
                        self.recovery.pop(key, None)
                        self.recovery_started.pop(key, None)
                        f.seek(0)
                        prefix = f.read(min(256, s.st_size))
                        self.fingerprints[key] = [len(prefix), hashlib.sha256(prefix).hexdigest()]
                    ceilings[key] = self.recovery.get(key, s.st_size)
            except FileNotFoundError:
                continue
        if any(key not in seen and ceiling > self.files[key][0] for key, ceiling in self.observed.items()):
            raise PendingCaptureUnavailable()
        with self.queue.db:
            self._checkpoint(ceilings)
        self.observed = dict(ceilings)
        anchors = deepcopy(self.anchors)
        before = deepcopy(self.files)
        recovery = dict(self.recovery)
        recovery_started = dict(self.recovery_started)
        inserted = self.queue.inserted_count
        try:
            with self.queue.db:
                self.queue.db.execute('BEGIN IMMEDIATE')
                self.queue._capturing = True
                full = None
                result = 0
                try:
                    result = self._poll(ceilings)
                except QueueFull as error:
                    full = error
                self._checkpoint(ceilings)
            if full is not None:
                raise full
            return result
        except QueueFull:
            raise  # Successfully committed prefix and its cursor remain valid.
        except BaseException:
            self.files = before
            self.anchors = anchors
            self.recovery = recovery
            self.recovery_started = recovery_started
            self.queue.inserted_count = inserted
            raise
        finally:
            self.queue._capturing = False

    def _poll(self, ceilings):
        accepted = 0
        self.unattributed_files = 0
        for p in self.paths():
            try:
                f = safe_open(p)
            except FileNotFoundError:
                continue  # Normal rotation between directory listing and open.
            with f:
                s = os.fstat(f.fileno())
                key = (s.st_dev, s.st_ino)
                if key not in ceilings:
                    continue  # Discovered after the durable read envelope; next poll.
                state = self.files[key]
                if s.st_size < ceilings[key]:
                    raise PendingCaptureUnavailable()
                state[1] = self.listener(f)
                if not state[1]:
                    # Pre-login files with only a header are normal, not a failure.
                    # Preserve the cursor so a late listener can still attribute events.
                    f.seek(state[0])
                    for _ in range(256):
                        remaining = ceilings[key] - f.tell()
                        if remaining <= 0:
                            break
                        candidate = f.readline(min(MAX_LINE + 1, remaining))
                        if not candidate:
                            break
                        if candidate.endswith(b'\n') and self.parser(candidate):
                            self.unattributed_files += 1
                            break
                    continue  # Preserve cursor for a late header; never enqueue anonymously.
                f.seek(state[0])
                for _ in range(256):
                    offset = f.tell()
                    remaining = ceilings.get(key, s.st_size) - offset
                    if remaining <= 0:
                        break
                    raw = f.readline(min(MAX_LINE + 1, remaining))
                    if not raw:
                        break
                    if state[3]:
                        state[0] = f.tell()
                        state[3] = not raw.endswith(b'\n')
                        continue
                    if len(raw) > MAX_LINE:
                        self.rejected += 1
                        state[0], state[3] = f.tell(), not raw.endswith(b'\n')
                        continue
                    if not raw.endswith(b'\n'):
                        if key in self.recovery and f.tell() >= self.recovery[key]:
                            state[0] = f.tell()
                        break  # Keep incomplete UTF-8 for the next active poll.
                    event = self.parser(raw, state[1])
                    if event and datetime.fromisoformat(event['time']) >= self.recovery_started.get(key, self.started):
                        identity = f'{self.run_id}:{key}:{state[2]}:{offset}'
                        event_id = hashlib.sha256(identity.encode()).hexdigest()
                        try:
                            self.queue.put(event_id, event)  # Cursor only advances after commit.
                        except QueueFull:
                            self._anchor(f, key)
                            raise
                        accepted += 1
                    state[0] = f.tell()
                self._anchor(f, key)
                if key in self.recovery and state[0] >= self.recovery[key]:
                    # Finish the old interval atomically; never read the skipped gap.
                    state[:] = self.baselines[key]
                    self.anchors.pop(key, None)
                    del self.recovery[key]
                    self.recovery_started.pop(key, None)
                    ceilings[key] = state[0]
        return accepted
