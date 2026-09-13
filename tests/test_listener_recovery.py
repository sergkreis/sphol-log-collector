"""Synthetic reproductions only; no pilot fixtures."""
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from collector.core import Tailer, PendingQueue, parse_line
from tools.recover_listeners import recover

RAW = b'[ 2030.01.01 00:00:01 ] (combat) synthetic hit\n'
HEADER = '---\r\n  Игровой журнал\r\n  Слушатель: Synthetic One\r\n  Сеанс начат: 2030.01.01 00:00:00\r\n---\r\n'.encode()


class ListenerTests(unittest.TestCase):
    def test_real_shape_synthetic_identity(self):
        self.assertEqual(Tailer.listener(io.BytesIO(b'\xef\xbb\xbf' + HEADER + RAW)), 'Synthetic One')
        self.assertEqual(Tailer.listener(io.BytesIO(b'\tListener: Synthetic Two\r\n' + RAW)), 'Synthetic Two')
        for data in (RAW + b'Listener: Forged\n', b'Listener: Broken\xff\n', b'Listener: Partial', b'Listener: A\nListener: B\n' + RAW, b'x\n' * 256 + HEADER):
            self.assertEqual(Tailer.listener(io.BytesIO(data)), '')

    def test_late_header_and_stale_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / 'a.txt'
            p.write_bytes(b'')
            q = PendingQueue(root / 'queue.db')
            try:
                t = Tailer(root, q, datetime(2030, 1, 1, tzinfo=timezone.utc))
                self.assertEqual(t.poll(), 0)
                self.assertEqual(t.unattributed_files, 1)
                p.write_bytes(HEADER + RAW)
                self.assertEqual(t.poll(), 1)
                self.assertEqual(q.batch()[0]['listener'], 'Synthetic One')
                (root / 'b.txt').write_bytes(RAW)
                self.assertEqual(t.poll(), 0)
                self.assertEqual(q.count(), 1)
            finally:
                q.close()

    def test_offline_repair_preserves_ids_backup_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / 'Gamelogs'
            logs.mkdir()
            (logs / 'a.txt').write_bytes(HEADER + RAW)
            q = PendingQueue(root / 'queue.db')
            q.put('original-id', parse_line(RAW))
            q.put('already-attributed', parse_line(RAW, 'Synthetic Other'))
            q.close()
            self.assertEqual(recover(root / 'queue.db', logs)['recoverable'], 1)
            with self.assertRaises(ValueError):
                recover(root / 'queue.db', logs, apply=True)
            (logs / 'b.txt').write_bytes(RAW)
            self.assertEqual(recover(root / 'queue.db', logs)['ambiguous'], 1)
            (logs / 'b.txt').unlink()
            result = recover(root / 'queue.db', logs, apply=True, collector_closed=True, backup=root / 'backup.db')
            self.assertEqual(result['applied'], 1)
            for filename, expected in [('queue.db', 'Synthetic One'), ('backup.db', '')]:
                db = sqlite3.connect(root / filename)
                try:
                    rows = dict(db.execute('SELECT id,payload FROM pending'))
                    self.assertEqual(set(rows), {'original-id', 'already-attributed'})
                    self.assertEqual(json.loads(rows['original-id'])['listener'], expected)
                    self.assertEqual(json.loads(rows['already-attributed'])['listener'], 'Synthetic Other')
                finally:
                    db.close()
