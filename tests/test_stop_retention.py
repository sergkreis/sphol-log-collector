"""Synthetic consent boundaries and genuine released-schema upgrade gates."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from collector.core import PendingQueue, Tailer, QueueFull, PendingCaptureUnavailable
from tools.released_v034_queue import PendingQueue as ReleasedQueue
from tools.native_cross_version_smoke import queue_rows, QUEUES

HEADER = b'Listener: Synthetic Pilot\nSession started: synthetic\n'
LINE = b'[ 2030.01.01 00:00:01 ] (combat) synthetic\n'
START = datetime(2030, 1, 1, tzinfo=timezone.utc)


class StopRetention(unittest.TestCase):
    def fixture(self, temp):
        root = Path(temp) / 'Gamelogs'
        root.mkdir()
        source = root / 'synthetic.txt'
        source.write_bytes(HEADER)
        return root, source, Path(temp) / 'q'

    def test_257_stop_reopen_retains_observed_excludes_downtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source, path = self.fixture(temp)
            with closing(PendingQueue(path)) as q:
                tail = Tailer(root, q, started=START)
                source.write_bytes(HEADER + LINE * 257)
                self.assertEqual(tail.poll(), 256)
                key = next(iter(tail.files))
                expected = hashlib.sha256(f'{tail.run_id}:{key}:0:{len(HEADER) + 256 * len(LINE)}'.encode()).hexdigest()
                tail.stop()
                before = q.db.execute('SELECT * FROM capture_checkpoint').fetchall()
                self.assertTrue(before)
                with source.open('ab') as f:
                    f.write(LINE.replace(b'synthetic', b'downtime'))
                with patch.object(tail, 'paths', side_effect=AssertionError('Stop must not read')):
                    self.assertEqual(tail.poll(), 0)
                self.assertEqual(q.count(), 256)
            with closing(PendingQueue(path)) as q:
                tail = Tailer(root, q, started=START)
                self.assertEqual(tail.poll(), 1)
                self.assertEqual(q.count(), 257)
                self.assertEqual(q.db.execute('SELECT id FROM pending ORDER BY rowid DESC LIMIT 1').fetchone()[0], expected)
                self.assertEqual(tail.poll(), 0)
                with source.open('ab') as f:
                    f.write(LINE.replace(b'synthetic', b'new consent'))
                self.assertEqual(tail.poll(), 1)
                rows = q.db.execute('SELECT id,payload FROM pending').fetchall()
                self.assertEqual(len({r[0] for r in rows}), 258)
                self.assertNotIn('downtime', ''.join(r[1] for r in rows))

    def test_capacity_stop_does_not_evict_and_replays_original_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source, path = self.fixture(temp)
            with closing(PendingQueue(path, max_events=1)) as q:
                tail = Tailer(root, q, started=START)
                source.write_bytes(HEADER + LINE * 2)
                with self.assertRaises(QueueFull):
                    tail.poll()
                first = q.batch()
                key = next(iter(tail.files))
                expected = hashlib.sha256(f'{tail.run_id}:{key}:0:{len(HEADER) + len(LINE)}'.encode()).hexdigest()
                tail.stop()
                self.assertEqual(q.batch(), first)
            with source.open('ab') as f:
                f.write(LINE.replace(b'synthetic', b'downtime'))
            with closing(PendingQueue(path, max_events=1)) as q:
                tail = Tailer(root, q, started=START)
                with self.assertRaises(QueueFull):
                    tail.poll()
                self.assertEqual(q.batch(), first)
                q.acknowledge([first[0]['id']])
                self.assertEqual(tail.poll(), 1)
                self.assertEqual(q.batch()[0]['id'], expected)
                self.assertEqual(tail.poll(), 0)

    def test_missing_or_changed_pending_source_preserves_metadata(self):
        for failure in ('missing', 'prefix', 'anchor', 'truncate'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root, source, path = self.fixture(temp)
                with closing(PendingQueue(path)) as q:
                    tail = Tailer(root, q, started=START)
                    source.write_bytes(HEADER + LINE * 257)
                    tail.poll()
                    tail.stop()
                    checkpoint = q.db.execute('SELECT * FROM capture_checkpoint').fetchall()
                    rows = q.db.execute('SELECT * FROM pending').fetchall()
                if failure == 'missing':
                    source.unlink()
                elif failure == 'prefix':
                    source.write_bytes((HEADER + LINE * 257).replace(b'Synthetic Pilot', b'Different Pilot'))
                elif failure == 'truncate':
                    source.write_bytes(HEADER)
                else:
                    # Prefix unchanged; provably changed bytes behind the cursor.
                    with source.open('r+b') as f:
                        f.seek(len(HEADER) + 255 * len(LINE))
                        f.write(LINE.replace(b'synthetic', b'REWRITTEN'))
                with closing(PendingQueue(path)) as q:
                    with self.assertRaises(PendingCaptureUnavailable):
                        Tailer(root, q, started=START)
                    self.assertEqual(q.db.execute('SELECT * FROM capture_checkpoint').fetchall(), checkpoint)
                    self.assertEqual(q.db.execute('SELECT * FROM pending').fetchall(), rows)

    def test_active_same_inode_regrow_anchor_warns_not_mixes(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source, path = self.fixture(temp)
            with closing(PendingQueue(path)) as q:
                tail = Tailer(root, q, started=START)
                source.write_bytes(HEADER + LINE * 2)
                self.assertEqual(tail.poll(), 2)
                before = q.batch()
                checkpoint = q.db.execute('SELECT * FROM capture_checkpoint').fetchall()
                source.write_bytes(HEADER + LINE.replace(b'synthetic', b'REWRITTEN') * 3)
                with self.assertRaises(PendingCaptureUnavailable):
                    tail.poll()
                self.assertEqual(q.batch(), before)
                self.assertEqual(q.db.execute('SELECT * FROM capture_checkpoint').fetchall(), checkpoint)

    def test_utf8_ceiling_stop_excludes_completed_downtime_line(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source, path = self.fixture(temp)
            line = LINE.replace(b'synthetic', 'Журнал'.encode())
            split = line.index('Ж'.encode()) + 1
            with closing(PendingQueue(path)) as q:
                tail = Tailer(root, q, started=START)
                source.write_bytes(HEADER + LINE * 257 + line[:split])
                self.assertEqual(tail.poll(), 256)
                tail.stop()
            with source.open('ab') as f:
                f.write(line[split:])
            with closing(PendingQueue(path)) as q:
                tail = Tailer(root, q, started=START)
                self.assertEqual(tail.poll(), 1)
                self.assertEqual(tail.poll(), 0)
                self.assertEqual(q.count(), 257)
                with source.open('ab') as f:
                    f.write(line)
                self.assertEqual(tail.poll(), 1)
                self.assertEqual(q.db.execute('SELECT payload FROM pending ORDER BY rowid DESC LIMIT 1').fetchone()[0].find('Журнал') >= 0, True)

    def test_recovery_transition_commit_denial_keeps_old_envelope(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source, path = self.fixture(temp)
            with closing(PendingQueue(path)) as q:
                tail = Tailer(root, q, started=START)
                source.write_bytes(HEADER + LINE * 257)
                tail.poll()
                tail.stop()
            with source.open('ab') as f:
                f.write(LINE.replace(b'synthetic', b'downtime'))
            with closing(PendingQueue(path)) as q:
                tail = Tailer(root, q, started=START)
                original = tail._poll
                def deny_transition(ceilings):
                    result = original(ceilings)
                    q.db.set_authorizer(lambda op, arg, *_: sqlite3.SQLITE_DENY if op == sqlite3.SQLITE_TRANSACTION and arg == 'COMMIT' else sqlite3.SQLITE_OK)
                    return result
                try:
                    with patch.object(tail, '_poll', side_effect=deny_transition), self.assertRaises(sqlite3.DatabaseError):
                        tail.poll()
                finally:
                    q.db.set_authorizer(None)
                self.assertEqual(q.count(), 256)
                tail.stop()
            with closing(PendingQueue(path)) as q:
                tail = Tailer(root, q, started=START)
                self.assertEqual(tail.poll(), 1)
                self.assertEqual(tail.poll(), 0)
                self.assertEqual(q.count(), 257)

    def test_process_exit_before_and_after_uncommitted_insert(self):
        for stage in ('envelope', 'insert'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temp:
                root, source, path = self.fixture(temp)
                code = '''
import os, sys
from pathlib import Path
from datetime import datetime, timezone
from collector.core import PendingQueue, Tailer
root, path, stage = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
q = PendingQueue(path)
t = Tailer(root, q, started=datetime(2030, 1, 1, tzinfo=timezone.utc))
with (root / 'synthetic.txt').open('ab') as f:
    f.write(b'[ 2030.01.01 00:00:01 ] (combat) synthetic\\n')
if stage == 'envelope':
    t._poll = lambda ceilings: os._exit(73)
else:
    put = q.put
    def crash(*args):
        put(*args)
        os._exit(73)
    q.put = crash
t.poll()
'''
                result = subprocess.run([sys.executable, '-c', code, str(root), str(path), stage], timeout=20)
                self.assertEqual(result.returncode, 73)
                with source.open('ab') as f:
                    f.write(LINE.replace(b'synthetic', b'downtime'))
                with closing(PendingQueue(path)) as q:
                    self.assertEqual(q.count(), 0)
                    tail = Tailer(root, q, started=START)
                    self.assertEqual(tail.poll(), 1)
                    self.assertEqual(tail.poll(), 0)
                    self.assertEqual(q.batch()[0]['text'], 'synthetic')

    def test_real_tk_close_retains_backlog_and_start_warns_missing(self):
        import gc
        import tkinter as tk
        from collector.gui import App
        from collector.core import PENDING_CAPTURE_WARNING
        with tempfile.TemporaryDirectory() as temp:
            root, source, path = self.fixture(temp)
            q = PendingQueue(path)
            window = tk.Tk()
            try:
                app = App(window, root, q)
                app.start_button.invoke()
                source.write_bytes(HEADER + LINE * 257)
                app.tick()
                self.assertEqual(q.count(), 256)
                with patch('collector.gui.messagebox.askyesno', return_value=True):
                    self.assertTrue(app.close())
            finally:
                try:
                    window.destroy()
                except tk.TclError:
                    pass  # App.close already destroyed the interpreter.
                q.close()
            source.unlink()
            with closing(PendingQueue(path)) as q:
                checkpoint = q.db.execute('SELECT * FROM capture_checkpoint').fetchall()
                self.assertTrue(checkpoint)
                window = tk.Tk()
                try:
                    app = App(window, root, q)
                    app.start_button.invoke()
                    self.assertIsNone(app.tailer)
                    self.assertEqual(app.status.get(), PENDING_CAPTURE_WARNING)
                    self.assertEqual(q.count(), 256)
                    self.assertEqual(q.db.execute('SELECT * FROM capture_checkpoint').fetchall(), checkpoint)
                finally:
                    window.destroy()
            gc.collect()

    def test_both_real_released_queues_migrate_and_old_reader_works(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in QUEUES:
                path = Path(temp) / name
                with closing(ReleasedQueue(path)) as old:
                    old.put('synthetic-1', {'listener': 'Synthetic Pilot', 'text': 'Журнал'})
                    old.put('synthetic-2', {'listener': 'unknown', 'schema': 2})
                    batch = old.batch()
                before = queue_rows(path, migrated=False)
                with closing(PendingQueue(path)) as new:
                    self.assertEqual(new.batch(), batch)
                self.assertEqual(queue_rows(path, migrated=True), before)
                with closing(ReleasedQueue(path)) as rollback:
                    self.assertEqual(rollback.batch(), batch)
                self.assertEqual(queue_rows(path, migrated=True), before)


if __name__ == '__main__':
    unittest.main()
