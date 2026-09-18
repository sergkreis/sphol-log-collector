"""Explicit-consent Gamelogs recorder. Never imported by network transport."""
from datetime import datetime, timezone
import csv
import json
import os
from pathlib import Path
import subprocess
import uuid
from .core import safe_open, parse_line


class CaptureStopped(Exception):
    """A visible stop, never a silently discarded line."""


def private_directory(path):
    path = Path(path)
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            s = part.lstat()
            if part.is_symlink() or getattr(s, 'st_file_attributes', 0) & 0x400:
                raise OSError('Unsafe local capture directory')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == 'nt':
        flags = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'],
                                capture_output=True, text=True, check=True, creationflags=flags)
        sid = next(csv.reader(result.stdout.splitlines()))[1]
        if not sid.startswith('S-1-') or not all(c in 'S-0123456789' for c in sid):
            raise OSError('Cannot establish private owner')
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', f'*{sid}:(OI)(CI)F'],
                       capture_output=True, check=True, creationflags=flags)
    else:
        os.chmod(path, 0o700)
    return path


class LocalCapture:
    def __init__(self, root, directory, *, consent=False, max_bytes=64*1024*1024,
                 total_bytes=128*1024*1024, max_line=1024*1024):
        if consent is not True:
            raise ValueError('Explicit local capture consent required')
        root = Path(root)
        if root.name != 'Gamelogs' or root.is_symlink():
            raise ValueError('Only Gamelogs is permitted')
        self.root = root.resolve(strict=True)
        self.files = {}
        self.count = self.used = 0
        self.closed = False
        self.max_line = max_line
        self.started = datetime.now(timezone.utc).isoformat()
        # Snapshot before creating output: no history, including partial old lines.
        for p in self.paths():
            with safe_open(p) as f:
                s = os.fstat(f.fileno())
                f.seek(max(0, s.st_size - 1))
                self.files[(s.st_dev, s.st_ino)] = [s.st_size, s.st_size > 0 and f.read(1) != b'\n']
        base = private_directory(directory)
        entries = list(base.iterdir())
        if len(entries) >= 1000:
            raise CaptureStopped('Лимит локальных сессий. Перенесите старые записи вручную.')
        used = 0
        for folder in entries:
            if folder.is_symlink() or not folder.is_dir():
                raise OSError('Unsafe capture entry')
            for p in folder.iterdir():
                with safe_open(p) as f:
                    used += os.fstat(f.fileno()).st_size
        self.limit = min(max_bytes, total_bytes - used - 16384)
        if self.limit <= 0:
            raise CaptureStopped('Локальное хранилище заполнено. Старые записи не удалены.')
        self.directory = private_directory(base / uuid.uuid4().hex)
        self.path = self.directory / 'events.jsonl'
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.stream = os.fdopen(fd, 'wb')
        try:
            self.metadata('recording')
        except Exception:
            self.stream.close()
            self.closed = True
            raise

    def paths(self):
        paths = list(self.root.glob('*.txt'))
        if len(paths) > 512:
            raise CaptureStopped('Более 512 файлов Gamelogs: локальная запись остановлена.')
        return sorted(paths)

    def metadata(self, status):
        data = {'schema': 1, 'started_utc': self.started,
                'updated_utc': datetime.now(timezone.utc).isoformat(),
                'status': status, 'complete_lines': self.count, 'bytes': self.used,
                'limit_bytes': self.limit, 'source': 'Gamelogs', 'uploaded': False,
                'historical_backfill': False}
        target = self.directory / 'session.json'
        temp = self.directory / 'session.tmp'
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, target)

    def poll(self):
        if self.closed:
            return
        try:
            for p in self.paths():
                try:
                    f = safe_open(p)
                except FileNotFoundError:
                    continue
                with f:
                    s = os.fstat(f.fileno())
                    key = (s.st_dev, s.st_ino)
                    if key not in self.files:
                        if len(self.files) >= 1024:
                            raise CaptureStopped('Лимит файлов сессии: запись остановлена.')
                        self.files[key] = [0, False]
                    state = self.files[key]
                    if s.st_size < state[0]:
                        state[:] = [0, False]
                    f.seek(state[0])
                    for _ in range(256):
                        offset = f.tell()
                        raw = f.readline(self.max_line + 1)
                        if not raw:
                            break
                        if state[1]:
                            state[:] = [f.tell(), not raw.endswith(b'\n')]
                            continue
                        if len(raw) > self.max_line:
                            raise CaptureStopped('Слишком длинная строка: запись остановлена; оригинал в Gamelogs.')
                        if not raw.endswith(b'\n'):
                            break
                        if parse_line(raw) is None:
                            state[0] = f.tell()
                            continue
                        text = raw.decode('utf-8')
                        record = json.dumps({'file': p.name, 'offset': offset, 'line': text},
                                            ensure_ascii=False).encode('utf-8') + b'\n'
                        if self.used + len(record) > self.limit:
                            raise CaptureStopped('Достигнут лимит размера: запись остановлена; оригиналы в Gamelogs.')
                        self.stream.write(record)
                        self.stream.flush()
                        os.fsync(self.stream.fileno())
                        self.used += len(record)
                        self.count += 1
                        state[0] = f.tell()
            self.metadata('recording')
        except Exception:
            self.close('stopped-error-or-limit')
            raise

    def close(self, status='stopped'):
        if not self.closed:
            self.closed = True
            self.stream.close()
            self.metadata(status)
