"""Real Tk unified capture and original-identity backlog; synthetic, offline."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from collector.core import PendingQueue
from collector.expanded import ExpandedQueue, parse_game_line
from collector.network_gui import ConnectedApp, Snapshot
from collector.transport import Uploader


def credentials(name, char, installation):
    return dict(scope='gamelogs:write', access_token=installation*43, token_type='Bearer',
                installation_id=installation*32, expires_at='2099-01-01T00:00:00Z',
                characters=[{'id':char,'name':name}])


class NativeSingleSmoke(unittest.TestCase):
    def test_unified_metrics_stop_errors_and_legacy_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root/'Gamelogs'; logs.mkdir()
            log = logs/'synthetic.txt'; log.write_text('Listener: Current Pilot\n')
            old = credentials('Previous Pilot', 99, 'b')
            current = credentials('Current Pilot', 42, 'a')
            stamp = (datetime.now(timezone.utc)+timedelta(seconds=2)).strftime('%Y.%m.%d %H:%M:%S')
            event = parse_game_line(f'[ {stamp} ] (notify) Target is invulnerable.'.encode(), 'Previous Pilot')
            self.assertIsNotNone(event)
            with closing(ExpandedQueue(root)) as legacy:
                legacy.put('c'*64, event)
            sentinel = root/'credentials-v2.dpapi'; sentinel.write_bytes(b'synthetic encrypted sentinel')
            def load(store):
                return old if store.path == sentinel else current
            with closing(PendingQueue(root/'pending.sqlite3')) as queue, patch(
                'collector.credentials.CredentialStore.load', load
            ), patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
                window = tk.Tk()
                app = None
                errors = []
                window.report_callback_exception = lambda *args: errors.append(args)
                try:
                    app = ConnectedApp(window, logs, queue)
                    window.update()
                    before = app.legacy.queue.batch()
                    with patch('collector.legacy_backlog.messagebox.askyesno', return_value=False) as prompt:
                        app.main_button.invoke()
                    self.assertIn('Previous Pilot', prompt.call_args.args[1])
                    self.assertIsNotNone(app.tailer)
                    self.assertIsNotNone(app.expanded.tailer)
                    self.assertFalse(app.legacy.enabled)
                    self.assertEqual(app.legacy.uploader.credentials, old)
                    self.assertEqual(app.expanded.uploader.credentials, current)
                    self.assertNotEqual(app.expanded.queue.path, app.legacy.queue.path)
                    self.assertFalse(app.expanded.start_button.winfo_viewable())
                    self.assertFalse(app.expanded.stop_button.winfo_viewable())
                    with log.open('a') as out:
                        out.write(f'[ {stamp} ] (combat) Synthetic current combat\n')
                        out.write(f'[ {stamp} ] (notify) Target is invulnerable.\n')
                    app.tailer.poll()
                    with patch.object(app.expanded, 'work'):
                        app.expanded.tick()
                    app.dashboard.refresh()
                    self.assertEqual([w.cget('text') for w in app.dashboard.metrics], ['2','0','2'])
                    # Current combat succeeds while observation transport fails.
                    app.expanded.uploader.failures = 1
                    snap = Snapshot(queue.batch()); snap.accepted = [e['id'] for e in snap.events]
                    app.results.put(('upload', (snap, 'synthetic ACK'), None))
                    app.network_tick()
                    self.assertIn('наблюдения', app.dashboard.heading.cget('text'))
                    self.assertEqual([w.cget('text') for w in app.dashboard.metrics], ['2','1','1'])
                    app.expanded.uploader.failures = 0
                    obs = Snapshot(app.expanded.queue.batch()); obs.accepted = [e['id'] for e in obs.events]
                    app.expanded.results.put(('upload', (obs, 'synthetic ACK'), False))
                    app.expanded.tick()
                    self.assertEqual([w.cget('text') for w in app.dashboard.metrics], ['2','2','0'])
                    # Exercise the actual capture-error handler, then a late ACK.
                    with patch.object(app.expanded.tailer, 'poll', side_effect=OSError('synthetic read failure')):
                        app.expanded.tick()
                    self.assertIsNone(app.expanded.tailer)
                    self.assertTrue(app.expanded.capture_problem)
                    app.expanded.results.put(('upload', (obs, 'synthetic late ACK'), False))
                    app.expanded.tick()
                    self.assertFalse(app.expanded.problem)
                    app.dashboard.refresh()
                    self.assertIn('наблюдения', app.dashboard.heading.cget('text'))
                    app.main_button.invoke()
                    self.assertIsNone(app.tailer)
                    self.assertIsNone(app.expanded.tailer)
                    self.assertFalse(app.upload_enabled)
                    self.assertFalse(app.expanded.enabled)
                    self.assertFalse(app.legacy.enabled)
                    self.assertEqual(app.legacy.queue.batch(), before)
                    # Explicitly authorized old delivery uses exactly old IDs/token.
                    with patch('collector.legacy_backlog.messagebox.askyesno', return_value=True):
                        app.main_button.invoke()
                    seen = []
                    def ack(http, path, payload, token=None):
                        seen.append((token, payload))
                        return 200, {'accepted_ids':[e['id'] for e in payload['events']], 'rejected':[]}
                    with patch('collector.transport.HTTPS.post', ack):
                        app.legacy.tick()
                        deadline = time.monotonic()+3
                        while app.legacy.results.empty() and time.monotonic()<deadline:
                            time.sleep(.01)
                        app.main_button.invoke()  # Stop before ACK handling; in-flight may finish.
                        app.legacy.tick()
                    self.assertEqual(seen[0][0], old['access_token'])
                    self.assertEqual(seen[0][1]['events'][0]['id'], 'c'*64)
                    self.assertEqual(seen[0][1]['events'][0]['listener'], 'Previous Pilot')
                    self.assertEqual(app.legacy.queue.count(), 0)
                    self.assertEqual(app.legacy.ack_count, 1)
                    self.assertEqual(sentinel.read_bytes(), b'synthetic encrypted sentinel')
                    self.assertEqual(errors, [])
                finally:
                    if app:
                        app.expanded.close(); app.legacy.close()
                    window.destroy()
            with closing(ExpandedQueue(root)) as reopened:
                self.assertEqual(reopened.count(), 0)
            self.assertEqual(sentinel.read_bytes(), b'synthetic encrypted sentinel')


if __name__ == '__main__':
    unittest.main(verbosity=2)
