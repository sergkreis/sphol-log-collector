"""Synthetic per-source recovery isolation; never opens a network connection."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from collector.core import Tailer, PendingQueue

HEADER = b'Listener: Synthetic Pilot\n'
LINE = b'[ 2030.01.01 00:00:01 ] (combat) synthetic\n'
START = datetime(2030, 1, 1, tzinfo=timezone.utc)

class RecoveryIsolation(unittest.TestCase):
    def test_multi_recovery_queuefull_commit_fault_reopen(self):
        import sqlite3
        from unittest.mock import patch
        from collector.core import QueueFull
        for fault in ('full', 'commit'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'logs'
                root.mkdir()
                blockers = [root / 'a.txt', root / 'b.txt']
                for p in blockers:
                    p.write_bytes(b'prelogin\n')
                live = root / 'z.txt'
                live.write_bytes(HEADER)
                db = Path(tmp) / 'queue'
                with closing(PendingQueue(db)) as q:
                    t = Tailer(root, q, started=START)
                    for p in blockers:
                        with p.open('ab') as f:
                            f.write(b'waiting\n')
                    t.poll()
                    t.stop()
                    envelopes = json.loads(q.db.execute('SELECT payload FROM capture_checkpoint').fetchone()[0])['files']
                    self.assertEqual(len(envelopes), 2)
                with closing(PendingQueue(db)) as q:
                    t = Tailer(root, q, started=START)
                    with live.open('ab') as f:
                        f.write(LINE)
                    self.assertEqual(t.poll(), 1)
                    rows = q.db.execute('SELECT id,payload,size FROM pending').fetchall()
                    with live.open('ab') as f:
                        f.write(LINE)
                    if fault == 'full':
                        q.max_events = 1
                        with self.assertRaises(QueueFull):
                            t.poll()
                    else:
                        original = t._poll
                        def deny(ceilings):
                            result = original(ceilings)
                            q.db.set_authorizer(lambda op, arg, *_: sqlite3.SQLITE_DENY if op == sqlite3.SQLITE_TRANSACTION and arg == 'COMMIT' else sqlite3.SQLITE_OK)
                            return result
                        try:
                            with patch.object(t, '_poll', side_effect=deny), self.assertRaises(sqlite3.DatabaseError):
                                t.poll()
                        finally:
                            q.db.set_authorizer(None)
                    self.assertEqual(q.db.execute('SELECT id,payload,size FROM pending').fetchall(), rows)
                    checkpoint = q.db.execute('SELECT * FROM capture_checkpoint').fetchall()
                    current = json.loads(checkpoint[0][1])
                    for envelope in envelopes:
                        self.assertIn(envelope, current['files'])
                with live.open('ab') as f:
                    f.write(LINE.replace(b'synthetic', b'downtime'))
                with closing(PendingQueue(db)) as q:
                    self.assertEqual(q.db.execute('SELECT * FROM capture_checkpoint').fetchall(), checkpoint)
                    self.assertEqual(q.db.execute('SELECT id,payload,size FROM pending').fetchall(), rows)
                    t = Tailer(root, q, started=START)
                    self.assertEqual(t.poll(), 1)
                    self.assertEqual(t.poll(), 0)
                    self.assertEqual(q.count(), 2)
                    self.assertEqual(q.db.execute('SELECT id,payload,size FROM pending').fetchall()[:1], rows)
                    self.assertNotIn('downtime', str(q.batch()))
                    current = json.loads(q.db.execute('SELECT payload FROM capture_checkpoint').fetchone()[0])
                    for envelope in envelopes:
                        self.assertIn(envelope, current['files'])

    def test_unresolved_sources_do_not_starve_live_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'logs'
            root.mkdir()
            blockers = [root / 'a.txt', root / 'b.txt']
            for p in blockers:
                p.write_bytes(b'prelogin\n')
            live = root / 'z.txt'
            live.write_bytes(HEADER + LINE.replace(b'synthetic', b'old'))
            db = Path(tmp) / 'queue'
            with closing(PendingQueue(db)) as q:
                t = Tailer(root, q, started=START)
                for p in blockers:
                    with p.open('ab') as f:
                        f.write(b'waiting\n')
                self.assertEqual(t.poll(), 0)
                t.stop()
                saved = json.loads(q.db.execute('SELECT payload FROM capture_checkpoint').fetchone()[0])
                unresolved = [x for x in saved['files'] if x[1][0] < x[2]]
                self.assertEqual(len(unresolved), 2)
            with live.open('ab') as f:
                f.write(LINE.replace(b'synthetic', b'downtime'))
            with closing(PendingQueue(db)) as q:
                t = Tailer(root, q, started=START)
                for n in range(3):
                    with live.open('ab') as f:
                        f.write(LINE)
                    self.assertEqual(t.poll(), 1)
                    current = json.loads(q.db.execute('SELECT payload FROM capture_checkpoint').fetchone()[0])
                    for old in unresolved:
                        self.assertIn(old, current['files'])
                self.assertEqual(q.count(), 3)
                self.assertNotIn('downtime', str(q.batch()))
                self.assertNotIn("'old'", str(q.batch()))
                # A late header releases only the original envelope; gap events
                # remain excluded even when their listener becomes knowable.
                for p in blockers:
                    with p.open('ab') as f:
                        f.write(HEADER + LINE.replace(b'synthetic', b'gap'))
                self.assertEqual(t.poll(), 0)
                self.assertEqual(len(t.recovery), 0)
                # Bytes written during the active consent after its EOF baseline
                # are admissible on the next poll, not historical downtime.
                self.assertEqual(t.poll(), 2)
                self.assertEqual(q.count(), 5)
