"""Actual Tk fault/ACK fixture; synthetic temporary state and no egress."""
from contextlib import closing
from pathlib import Path
import gc
import socket
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from collector.core import PendingQueue, Tailer
from collector.network_gui import ConnectedApp, Snapshot
from collector.transport import Uploader
from collector.connection_status import LABELS
from tools.native_single_smoke import credentials
from tools.native_recovery_smoke import HEADER, LINE, START

class NativeFaultSmoke(unittest.TestCase):
    def test_tk_fault_pending_ack_green_and_reopen(self):
        for stream in ('main', 'expanded'):
            with self.subTest(stream=stream), tempfile.TemporaryDirectory() as temp, \
                    patch.object(socket.socket, 'connect', side_effect=AssertionError('No networking')), \
                    patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('No networking')), \
                    patch('collector.connection_status.probe', return_value=None):
                root = Path(temp)
                logs = root / 'logs'; logs.mkdir()
                source = logs / 'synthetic.txt'; source.write_bytes(HEADER)
                db = root / 'queue.db'
                with closing(PendingQueue(db)) as queue:
                    window = tk.Tk()
                    app = None
                    errors = []
                    window.report_callback_exception = lambda *args: errors.append(args)
                    try:
                        app = ConnectedApp(window, logs, queue)
                        uploader = Uploader(credentials('Synthetic Pilot', 42, 's'))
                        app.uploader = uploader
                        target = app if stream == 'main' else app.expanded
                        target.uploader = uploader
                        q = target.queue
                        from collector.core import parse_line
                        from collector.expanded import parse_game_line
                        parser = parse_line if stream == 'main' else parse_game_line
                        line = LINE if stream == 'main' else b'[ 2030.01.01 00:00:01 ] (None) Fleet warp initiated.\n'
                        target.tailer = Tailer(logs, q, started=START, parser=parser)
                        with source.open('ab') as f:
                            f.write(line * 2)
                        self.assertEqual(target.tailer.poll(), 2)
                        before = q.db.execute('SELECT id,payload,size FROM pending ORDER BY rowid').fetchall()
                        checkpoint = q.db.execute('SELECT * FROM capture_checkpoint').fetchall()
                        snapshot = Snapshot(q.batch(), q)
                        snapshot.accepted = [before[0][0]]
                        target.busy = True
                        import threading
                        release = threading.Event()
                        def delayed_ack():
                            if release.wait(3):
                                target.results.put(('upload', (snapshot, 'Synthetic ACK'), None))
                        worker = threading.Thread(target=delayed_ack)
                        worker.start()
                        fault = patch('collector.dashboard.refresh', side_effect=RuntimeError('synthetic callback')) if stream == 'main' else patch.object(target, 'refresh_summary', side_effect=RuntimeError('synthetic callback'))
                        with fault:
                            window.after(0, target.tick)
                            window.after(30, window.quit)
                            window.mainloop()
                        self.assertTrue(getattr(target, '_poll_failed', False))
                        self.assertIsNone(target.tailer)
                        self.assertTrue(target.capture_problem)
                        if stream in ('main', 'expanded'):
                            self.assertTrue(target.busy)
                            release.set()
                            worker.join(timeout=1)
                            self.assertFalse(worker.is_alive())
                            if stream == 'expanded':
                                import sqlite3
                                owner = threading.get_ident()
                                original = q.complete_upload
                                calls = []
                                def complete(*args):
                                    self.assertEqual(threading.get_ident(), owner)
                                    calls.append(args)
                                    return original(*args)
                                with patch.object(q, 'complete_upload', side_effect=sqlite3.OperationalError('synthetic disk fault')):
                                    target.result_tick()
                                    first = target._result_after
                                    target.result_tick()
                                    self.assertNotIn(first, window.tk.call('after', 'info'))
                                    self.assertTrue(target.busy)
                                    self.assertEqual(q.db.execute('SELECT id,payload,size FROM pending ORDER BY rowid').fetchall(), before)
                                    self.assertEqual(snapshot.accepted, [before[0][0]])
                                with patch.object(q, 'complete_upload', side_effect=complete), patch.object(target, 'work', side_effect=AssertionError('No new uploads')):
                                    # Only the independently scheduled Tk callback
                                    # can commit the delayed ACK after disk recovery.
                                    window.after(230, window.quit)
                                    window.mainloop()
                                self.assertEqual(len(calls), 1)
                                self.assertFalse(target.enabled)
                                self.assertIsNone(target.tailer)
                            window.after(0, app.network_tick)
                            window.after(30, window.quit)
                            window.mainloop()
                        self.assertFalse(target.busy)
                        self.assertEqual(q.db.execute('SELECT id,payload,size FROM pending ORDER BY rowid').fetchall(), before[1:])
                        self.assertEqual(app.dashboard.confirmed if stream == 'main' else target.ack_count, 1)
                        app.connection_status.state = 'connected'
                        with patch.object(app.connection_status, 'tick', return_value='connected'):
                            app.refresh_controls()
                            app.dashboard.refresh()
                            window.update_idletasks()
                        self.assertEqual(app.connection_badge.cget('text'), LABELS['connected'])
                        self.assertEqual(str(app.connection_badge.cget('foreground')), '#80d8a0')
                        self.assertIn('Не удалось читать' if stream == 'main' else 'наблюдения', app.dashboard.heading.cget('text') if stream == 'main' else app.dashboard.notice.cget('text'))
                        self.assertEqual(errors, [])
                        retained = q.db.execute('SELECT * FROM capture_checkpoint').fetchall()
                        self.assertTrue(retained)
                        path = q.path
                    finally:
                        if app:
                            app.expanded.close()
                            app.expanded.close()
                            # A worker completing after close may enqueue only;
                            # no callback may touch the closed SQLite connection.
                            app.expanded.results.put(('upload', (snapshot, 'Synthetic late close ACK'), None))
                            app.expanded.result_tick()
                            self.assertIsNone(app.expanded._result_after)
                            if getattr(app, 'legacy', None):
                                app.legacy.queue.close()
                        for callback in window.tk.call('after', 'info'):
                            window.after_cancel(callback)
                        window.destroy()
                with closing(PendingQueue(path)) as reopened:
                    self.assertEqual(reopened.db.execute('SELECT id,payload,size FROM pending ORDER BY rowid').fetchall(), before[1:])
                    self.assertEqual(reopened.db.execute('SELECT * FROM capture_checkpoint').fetchall(), retained)
                    tail = Tailer(logs, reopened, started=START, parser=parser)
                    self.assertEqual(tail.poll(), 0)
                    self.assertEqual(reopened.count(), 1)
                gc.collect()
