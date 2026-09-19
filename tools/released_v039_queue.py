"""Published v0.3.9 queue classes, verbatim from a45d659c835af336afd884e460c2e00d50ed2103."""
import json
import os
import sqlite3
import time
from pathlib import Path

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

    def batch(self, limit=100, listeners=None, combat_only=False):
        result = []
        limit = min(max(limit, 0), 100)
        if not limit:
            return result
        for identity, payload in self.db.execute('SELECT id,payload FROM pending ORDER BY rowid'):
            event = json.loads(payload)
            if combat_only:
                from .combat_policy import is_combat
                if not is_combat(event):
                    continue
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
