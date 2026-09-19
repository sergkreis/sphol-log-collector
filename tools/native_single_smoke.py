"""Real Tk unified capture and original-identity backlog; synthetic, offline."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import os
import subprocess
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from collector.core import PendingQueue
from collector.expanded import ExpandedQueue, parse_game_line
from collector.network_gui import ConnectedApp, Snapshot
from collector.transport import Uploader
from collector.transport import PAIR_URI


def visible_button(root, text):
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    return next(w for w in descendants(root)
                if w.winfo_class() in ('Button', 'TButton') and w.cget('text') == text and w.winfo_viewable())


def evidence(window, name):
    """Opt-in synthetic window-only screenshot, never the whole desktop."""
    destination = os.environ.get('SPHOL_SMOKE_EVIDENCE')
    if not destination or os.name != 'nt':
        return
    window.update_idletasks()
    path = Path(destination).absolute() / (name + '.png')
    path.parent.mkdir(parents=True, exist_ok=True)
    x, y = window.winfo_rootx(), window.winfo_rooty()
    w, h = window.winfo_width(), window.winfo_height()
    env = os.environ.copy(); env['SPHOL_SMOKE_IMAGE'] = str(path)
    script = ("Add-Type -AssemblyName System.Drawing; "
              f"$b=New-Object System.Drawing.Bitmap({w},{h}); "
              "$g=[System.Drawing.Graphics]::FromImage($b); "
              f"$g.CopyFromScreen({x},{y},0,0,$b.Size); "
              "$b.Save($env:SPHOL_SMOKE_IMAGE); $g.Dispose(); $b.Dispose()")
    subprocess.run(['powershell', '-NoProfile', '-Command', script], env=env,
                   check=True, timeout=15, capture_output=True)


def credentials(name, char, installation):
    return dict(scope='gamelogs:write', access_token=installation*43, token_type='Bearer',
                installation_id=installation*32, expires_at='2099-01-01T00:00:00Z',
                characters=[{'id':char,'name':name}])


@patch('collector.connection_status.delivery_probe', new=lambda *args, **kwargs: '2026-01-01T00:00:00Z')
@patch('collector.connection_status.probe', new=lambda _: None)
class NativeSingleSmoke(unittest.TestCase):
    def test_pending_browser_consent_default_off_and_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); logs = root/'Gamelogs'; logs.mkdir()
            with closing(PendingQueue(root/'pending.sqlite3')) as queue, patch.object(
                socket.socket, 'connect', side_effect=AssertionError('No network')
            ), patch('collector.credentials.CredentialStore.load', return_value=None):
                window = tk.Tk(); app = None
                try:
                    app = ConnectedApp(window, logs, queue); window.update()
                    self.assertFalse(app.upload_enabled)
                    self.assertFalse(app.expanded.enabled)
                    self.assertIsNone(app.tailer)
                    self.assertIsNone(app.expanded.tailer)
                    self.assertTrue(app.modern.root.winfo_viewable())
                    self.assertTrue(visible_button(window, 'Войти через EVE').winfo_viewable())
                    self.assertFalse(app.site_codes.legacy_button.winfo_viewable())
                    app.modern.show('settings'); window.update()
                    self.assertTrue(visible_button(window, 'Обновить приложение').winfo_viewable())
                    self.assertTrue(visible_button(window, 'Сменить персонажа').winfo_viewable())
                    self.assertTrue(visible_button(window, 'Помощь').winfo_viewable())
                    app.modern.show('main'); window.update()
                    with patch('collector.network_gui.messagebox.askyesno', return_value=True), patch.object(app, 'work'):
                        visible_button(window, 'Войти через EVE').invoke()
                    assert app.pairing is not None
                    self.assertTrue(app.pairing.browser)
                    self.assertEqual(app.pairing.scope, 'combat:write')
                    proof = 's'*43
                    app.pairing.browser_uri = PAIR_URI + '#' + proof
                    app.pairing.deadline = float('inf')
                    app.results.put(('pair', proof, None))
                    with patch.object(app, 'work'), patch('collector.pairing_ux.webbrowser.open', return_value=True) as browser:
                        def await_browser(count):
                            # Tk's scheduled poll may already have consumed the result.
                            # Assert completed work, not transient queue occupancy.
                            deadline = time.monotonic() + 3
                            while time.monotonic() < deadline:
                                window.update()
                                app.poll_browser(); app.refresh_controls()
                                if browser.call_count == count and app.browser_job is None:
                                    break
                                time.sleep(.01)
                            self.assertEqual(browser.call_count, count, 'browser worker timed out')
                            self.assertIsNone(app.browser_job, 'browser result not handled')
                            self.assertIn('Подтвердите привязку', app.pairing_message.cget('text'))
                        app.network_tick(); window.update()
                        await_browser(1)
                        app.browser_button.invoke()
                        await_browser(2)
                        self.assertEqual(browser.call_count, 2)
                        browser.assert_called_with(app.pairing.browser_uri)
                    self.assertEqual(app.code_field.get(), '')
                    self.assertTrue(app.copy_button.instate(['disabled']))
                    self.assertFalse(app.upload_enabled)
                    self.assertFalse(app.expanded.enabled)
                    self.assertIsNone(app.tailer)
                    self.assertIsNone(app.expanded.tailer)
                    evidence(window, 'pending-consent')
                finally:
                    if app:
                        app.expanded.close(); app.legacy.close()
                    for callback in window.tk.call('after', 'info'):
                        window.after_cancel(callback)
                    window.destroy()

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
                    prompt.assert_not_called()
                    self.assertIsNotNone(app.tailer)
                    self.assertIsNone(app.expanded.tailer)
                    self.assertFalse(app.expanded.enabled)
                    self.assertEqual(app.expanded.uploader.credentials, current)
                    self.assertFalse(app.legacy.enabled)
                    self.assertEqual(app.legacy.uploader.credentials, old)
                    self.assertEqual(app.uploader.credentials, current)
                    self.assertNotEqual(app.expanded.queue.path, app.legacy.queue.path)
                    self.assertFalse(app.expanded.start_button.winfo_viewable())
                    self.assertFalse(app.expanded.stop_button.winfo_viewable())
                    with log.open('a') as out:
                        out.write(f'[ {stamp} ] (combat) Synthetic current combat\n')
                        out.write(f'[ {stamp} ] (notify) Target is invulnerable.\n')
                    app.tailer.poll()
                    with patch.object(app.expanded, 'work') as observation_work:
                        app.expanded.tick()
                        observation_work.assert_not_called()
                    app.dashboard.refresh()
                    self.assertEqual([w.cget('text') for w in app.dashboard.metrics], ['1','0','1'])
                    evidence(window, 'single-start-combat-only')
                    app.uploader.failures = 1
                    app.dashboard.refresh()
                    self.assertIn('боевые', app.dashboard.notice.cget('text'))
                    self.assertEqual(queue.count(), 1)
                    snap = Snapshot(queue.batch()); snap.accepted = [e['id'] for e in snap.events]
                    # Capture failure stops new work, but a previously started
                    # combat request must still commit its late ACK.
                    with patch.object(app.tailer, 'poll', side_effect=OSError('synthetic read failure')):
                        app.tick()
                    self.assertIsNone(app.tailer)
                    self.assertIn('Не удалось читать журналы', app.dashboard.heading.cget('text'))
                    self.assertFalse(app.upload_enabled)
                    self.assertEqual(queue.count(), 1)
                    app.uploader.failures = 0
                    app.results.put(('upload', (snap, 'synthetic late ACK'), None))
                    app.network_tick()
                    app.dashboard.refresh()
                    self.assertEqual([w.cget('text') for w in app.dashboard.metrics], ['1','1','0'])
                    self.assertIsNotNone(app.dashboard.ack_at)
                    self.assertIn('Не удалось читать журналы', app.dashboard.heading.cget('text'))
                    self.assertEqual(queue.count(), 0)
                    app.main_button.invoke()  # Explicit restart, then explicit Stop.
                    self.assertIsNotNone(app.tailer)
                    app.main_button.invoke()
                    self.assertIsNone(app.tailer)
                    self.assertIsNone(app.expanded.tailer)
                    self.assertFalse(app.upload_enabled)
                    self.assertFalse(app.expanded.enabled)
                    self.assertFalse(app.legacy.enabled)
                    with patch('collector.transport.HTTPS.post') as post, patch('collector.legacy_backlog.messagebox.askyesno') as prompt:
                        app.legacy.authorize()
                        app.legacy.tick()
                        post.assert_not_called()
                        prompt.assert_not_called()
                    self.assertEqual(app.legacy.queue.batch(), before)
                    self.assertEqual(app.legacy.ack_count, 0)
                    self.assertEqual(app.legacy.uploader.credentials, old)
                    self.assertEqual(app.uploader.credentials, current)
                    self.assertEqual(sentinel.read_bytes(), b'synthetic encrypted sentinel')
                    self.assertEqual(errors, [])
                finally:
                    if app:
                        app.expanded.close(); app.legacy.close()
                    for callback in window.tk.call('after', 'info'):
                        window.after_cancel(callback)
                    window.destroy()
            with closing(ExpandedQueue(root)) as reopened:
                self.assertEqual(reopened.batch(), before)
            with closing(PendingQueue(root/'pending.sqlite3')) as reopened:
                self.assertEqual(reopened.count(), 0)
            self.assertEqual(sentinel.read_bytes(), b'synthetic encrypted sentinel')


if __name__ == '__main__':
    unittest.main(verbosity=2)
