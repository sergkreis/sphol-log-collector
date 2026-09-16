"""Verbatim queue classes from v0.3.4 core.py, source
16e41f08eb36ab14d2551847653d09873a01d2c8. Fixture only; never production.
"""
import json
import os
from pathlib import Path
import sqlite3


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
        self.inserted_count = 0  # Process-local, successful new commits only.

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
        self.inserted_count += 1

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

