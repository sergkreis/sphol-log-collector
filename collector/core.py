"""Local-only, combat-only collector. No networking or game interaction."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid

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


def safe_open(path: Path):
    """Reject symlinks, devices and path swaps; never open a game file writable."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise OSError('Not a regular log file')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    try:
        after = os.fstat(fd)
        if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise OSError('Log changed during open')
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


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
        self.db.commit()
        self.max_events, self.max_bytes = max_events, max_bytes

    def put(self, event_id: str, event: dict):
        payload = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
        size = len(payload.encode('utf-8'))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM pending WHERE id=?', (event_id,)).fetchone():
                return
            count, used = self.db.execute('SELECT count(*), coalesce(sum(size),0) FROM pending').fetchone()
            if count >= self.max_events or used + size > self.max_bytes:
                raise QueueFull('Queue full; collection paused. Nothing uploaded.')
            self.db.execute('INSERT INTO pending VALUES (?,?,?)', (event_id, payload, size))

    def batch(self, limit=100):
        return [{'id': i, **json.loads(p)} for i, p in self.db.execute(
            'SELECT id,payload FROM pending ORDER BY rowid LIMIT ?', (min(max(limit, 0), 100),))]

    def count(self):
        return self.db.execute('SELECT count(*) FROM pending').fetchone()[0]

    def acknowledge(self, ids):
        # Future transport must invoke ONLY after authenticated per-ID durable ACK.
        with self.db:
            self.db.executemany('DELETE FROM pending WHERE id=?', [(i,) for i in ids])

    def clear(self):
        with self.db:
            self.db.execute('DELETE FROM pending')
        self.db.execute('VACUUM')

    def close(self):
        self.db.close()


class Tailer:
    """One foreground capture session. Existing files start at EOF, not history."""
    def __init__(self, root: Path, queue: PendingQueue, started=None):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise OSError('Gamelogs directory is missing')
        self.queue = queue
        self.started = started or datetime.now(timezone.utc)
        self.run_id = uuid.uuid4().hex
        self.files = {}
        self.rejected = 0
        for p in self.paths():
            with safe_open(p) as f:
                s = os.fstat(f.fileno())
                listener = self.listener(f)
                # Do not capture the continuation of a line begun before Start.
                f.seek(max(0, s.st_size - 1))
                partial = s.st_size > 0 and f.read(1) != b'\n'
                self.files[(s.st_dev, s.st_ino)] = [s.st_size, listener, 0, partial]

    def paths(self):
        # No recursion; never enumerate Chatlogs. Bound resource use visibly.
        paths = []
        for p in self.root.glob('*.txt'):
            if p.is_symlink() or p.resolve().parent != self.root:
                continue
            paths.append(p)
            if len(paths) > 512:
                raise OSError('More than 512 log files; archive old Gamelogs before starting')
        return sorted(paths)

    @staticmethod
    def listener(f):
        f.seek(0)
        header = f.read(16384).decode('utf-8-sig', errors='replace')
        for line in header.splitlines():
            if line.startswith(('Listener:', 'Слушатель:')):
                return ''.join(c for c in line.split(':', 1)[1].strip() if c.isprintable())[:128]
        return ''

    def poll(self):
        accepted = 0
        for p in self.paths():
            try:
                f = safe_open(p)
            except FileNotFoundError:
                continue  # Normal rotation between directory listing and open.
            with f:
                s = os.fstat(f.fileno())
                key = (s.st_dev, s.st_ino)
                if key not in self.files:
                    if len(self.files) >= 1024:
                        raise OSError('Session file limit reached; stop and restart capture')
                    self.files[key] = [0, self.listener(f), 0, False]
                state = self.files[key]
                if s.st_size < state[0]:
                    state[:] = [0, self.listener(f), state[2] + 1, False]
                if not state[1]:
                    state[1] = self.listener(f)
                f.seek(state[0])
                for _ in range(256):
                    offset = f.tell()
                    raw = f.readline(MAX_LINE + 1)
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
                        break  # Keep offset; retry whole UTF-8 line after next append.
                    event = parse_line(raw, state[1])
                    if event and datetime.fromisoformat(event['time']) >= self.started:
                        identity = f'{self.run_id}:{key}:{state[2]}:{offset}'
                        event_id = hashlib.sha256(identity.encode()).hexdigest()
                        self.queue.put(event_id, event)  # Cursor only advances after commit.
                        accepted += 1
                    state[0] = f.tell()
        return accepted
