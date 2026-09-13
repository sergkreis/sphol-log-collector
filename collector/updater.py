"""Explicit unsigned GitHub release updates. No background checks or shell."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

REPO = 'sergkreis/sphol-log-collector'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
ASSET = 'SPHOLLogCollector.exe'
MAX_EXE = 128 * 1024 * 1024
HOSTS = {'api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})', value):
        raise ValueError('Invalid stable version')
    return tuple(map(int, value.split('.')))


def safe_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None, 443) or p.fragment:
        raise ValueError('Untrusted update URL')
    return url


class Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 4
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, limit):
    request = urllib.request.Request(safe_url(url), headers={'User-Agent': 'SPHOLLogCollector-updater', 'Accept': 'application/vnd.github+json'})
    start = time.monotonic()
    with urllib.request.build_opener(Redirects()).open(request, timeout=10) as response:
        safe_url(response.url)
        length = response.headers.get('Content-Length')
        if length is not None and (not length.isdigit() or int(length) > limit):
            raise ValueError('Oversized response')
        chunks, count = [], 0
        while True:
            if time.monotonic() - start > 90:
                raise TimeoutError('Update download deadline')
            block = response.read1(min(65536, limit + 1 - count))
            if not block:
                break
            count += len(block)
            if count > limit:
                raise ValueError('Oversized response')
            chunks.append(block)
        if length is not None and count != int(length):
            raise ValueError('Truncated response')
        return b''.join(chunks)


def strict_json(data):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('Duplicate JSON key')
            result[k] = v
        return result
    return json.loads(data.decode('utf-8'), object_pairs_hook=pairs)


def safe_path(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or str(path).startswith(('\\\\', '//')):
        raise ValueError('Unsafe update path')
    if os.name == 'nt' and ':' in str(path)[2:]:
        raise ValueError('Alternate stream')
    for component in [*reversed(path.parents), path]:
        s = component.lstat()
        if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Link/reparse update path')
    s = path.lstat()
    if not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)):
        raise ValueError('Wrong update path type')
    return path


def digest(path):
    safe_path(path)
    h = hashlib.sha256()
    before = Path(path).lstat()
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as f:
        after = os.fstat(f.fileno())
        if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError('Executable path changed')
        if after.st_size > MAX_EXE:
            raise ValueError('Oversized executable')
        count = 0
        for block in iter(lambda: f.read(65536), b''):
            count += len(block)
            if count > MAX_EXE:
                raise ValueError('Growing executable')
            h.update(block)
    return h.hexdigest()


def private_write(path, data):
    safe_path(Path(path).parent, directory=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def prepare(current, state, fetch=download):
    release = strict_json(fetch(API, 1024 * 1024))
    if release.get('draft') is not False or release.get('prerelease') is not False:
        raise ValueError('Not stable')
    tag = release.get('tag_name', '')
    if not tag.startswith('v'):
        raise ValueError('Invalid release tag')
    candidate = tag[1:]
    if version(candidate) <= version(current):
        return None
    assets = release['assets']
    def asset(name):
        matches = [a for a in assets if a.get('name') == name]
        if len(matches) != 1:
            raise ValueError('Missing/duplicate release asset')
        a = matches[0]
        expected = f'https://github.com/{REPO}/releases/download/{tag}/{name}'
        if a.get('browser_download_url') != expected:
            raise ValueError('Asset repository/tag mismatch')
        return a
    meta = asset('update-manifest.json')
    manifest = strict_json(fetch(meta['browser_download_url'], 4096))
    exe = asset(ASSET)
    if set(manifest) != {'schema', 'version', 'asset', 'size', 'sha256'} or type(manifest['schema']) is not int or manifest['schema'] != 1 or manifest['version'] != candidate or manifest['asset'] != ASSET:
        raise ValueError('Manifest mismatch')
    size, sha = manifest['size'], manifest['sha256']
    if type(size) is not int or not 1 <= size <= MAX_EXE or type(exe.get('size')) is not int or exe['size'] != size or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
        raise ValueError('Invalid size/hash')
    data = fetch(exe['browser_download_url'], size)
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha or not data.startswith(b'MZ'):
        raise ValueError('Executable checksum/format mismatch')
    safe_path(state, directory=True)
    stage = Path(tempfile.mkdtemp(prefix='update-', dir=state))
    private_write(stage / ASSET, data)
    return stage, candidate, sha


def clean_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith('_PYI')}
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def launch(target):
    return subprocess.Popen([str(target)], cwd=str(target.parent), env=clean_env(), close_fds=True)


def replace_and_launch(target, payload, old_hash, new_hash, spawn=launch):
    target, payload = safe_path(target), safe_path(payload)
    if digest(target) != old_hash or digest(payload) != new_hash:
        raise ValueError('Executable changed')
    backup = target.with_name(target.stem + '.' + payload.parent.name + '.rollback.exe')
    if backup.exists() or backup.is_symlink():
        raise ValueError('Backup already exists')
    # Copy beside destination first: staging may be on another volume.
    incoming = target.with_name(target.name + '.' + payload.parent.name + '.incoming')
    private_write(incoming, payload.read_bytes())
    if digest(incoming) != new_hash:
        incoming.unlink()
        raise ValueError('Copy checksum mismatch')
    moved = False
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                os.rename(target, backup)
                break
            except PermissionError:
                # PyInstaller's parent bootloader may still hold the EXE briefly.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)
        moved = True
        os.replace(incoming, target)
        spawn(target)
    except Exception:
        if moved:
            os.replace(backup, target)
            spawn(target)
        raise
    finally:
        if incoming.exists():
            incoming.unlink()
    return backup


def kernel32():
    import ctypes
    from ctypes import wintypes as w
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes, k.OpenProcess.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k.CloseHandle.argtypes = [w.HANDLE]
    k.CreateMutexW.argtypes, k.CreateMutexW.restype = [w.LPVOID, w.BOOL, w.LPCWSTR], w.HANDLE
    return k


def instance_lock(state):
    if os.name != 'nt':
        return None
    import ctypes
    name = 'Local\\SPHOLCollector-' + hashlib.sha256(str(Path(state).absolute()).lower().encode()).hexdigest()
    k = kernel32()
    handle = k.CreateMutexW(None, False, name)
    if not handle or ctypes.get_last_error() == 183:
        if handle:
            k.CloseHandle(handle)
        raise RuntimeError('Collector already running')
    return handle  # Kept until process exit, including queue shutdown.


def helper(target, pid, old_hash, new_hash):
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        raise RuntimeError('Frozen Windows helper only')
    import ctypes
    from ctypes import wintypes as w
    stage = safe_path(Path(sys.executable).absolute().parent, directory=True)
    target = safe_path(Path(target))
    if target == Path(sys.executable) or not re.fullmatch('[0-9a-f]{64}', old_hash) or not re.fullmatch('[0-9a-f]{64}', new_hash):
        raise ValueError('Invalid helper binding')
    if digest(target) != old_hash or digest(stage / ASSET) != new_hash:
        raise ValueError('Staging changed')
    k = kernel32()
    handle = k.OpenProcess(0x1000 | 0x100000, False, int(pid))
    if not handle:
        raise OSError('Cannot bind original process')
    try:
        buf, length = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
        if not k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(length)) or os.path.normcase(buf.value) != os.path.normcase(str(target)):
            raise ValueError('Original process image mismatch')
        private_write(stage / 'ready', b'ready')
        if k.WaitForSingleObject(handle, 60000) != 0:
            raise TimeoutError('Original collector did not exit; unchanged')
        if safe_path(stage / 'apply').read_bytes() != b'apply':
            raise ValueError('Restart not authorized')
        replace_and_launch(target, stage / ASSET, old_hash, new_hash)
    finally:
        k.CloseHandle(handle)


def start_helper(stage, sha):
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        raise RuntimeError('Self-update requires portable Windows EXE')
    target = safe_path(Path(sys.executable).absolute())
    old_hash = digest(target)
    private_write(stage / 'update-helper.exe', target.read_bytes())
    return subprocess.Popen([str(stage / 'update-helper.exe'), '--apply-update', str(target), str(os.getpid()), old_hash, sha], cwd=str(stage), env=clean_env(), close_fds=True)
