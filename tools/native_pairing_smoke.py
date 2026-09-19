"""Real visible Tk controls with temporary state and forbidden sockets."""
import os
from pathlib import Path
import socket
import tempfile
import time
import tkinter as tk
import types
import unittest
from unittest.mock import patch
from collector.core import PendingQueue
from collector.network_gui import ConnectedApp
from collector.transport import PAIR_URI


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt', 'native display required')
class RecoveryTk(unittest.TestCase):
    def tearDown(self):
        # Collect destroyed Tk cycles on their owning thread, before later HTTP
        # fixture workers can trigger cyclic GC and Tcl_AsyncDelete aborts.
        import gc
        gc.collect()

    def test_late_redemption_waits_and_requires_explicit_resume(self):
        import threading
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(socket.socket, 'connect', side_effect=AssertionError('offline only')), \
                patch('collector.network_gui.CredentialStore.load', return_value=None), \
                patch('collector.connection_status.probe', return_value=None):
            root = Path(temp)
            logs = root / 'Gamelogs'
            logs.mkdir()
            q = PendingQueue(root / 'pending.sqlite3')
            w = tk.Tk()
            release = threading.Event()
            with patch.object(w, 'after'):
                app = ConnectedApp(w, logs, q)
                try:
                    token = dict(access_token='A'*43, token_type='Bearer', scope='gamelogs:write',
                                 installation_id='fixture-late', expires_at='2099-01-01T00:00:00Z',
                                 characters=[{'id': 42, 'name': 'Test Pilot'}])
                    app.pairing = types.SimpleNamespace(deadline=time.monotonic()+60, browser=False)
                    app.resume_after_pair = True
                    app.work('token', lambda: (release.wait(3), token)[1])
                    app.pair_job = (app.pair_attempt, 0)
                    app.network_tick()
                    self.assertTrue(app.retry_button.instate(['disabled']))
                    self.assertTrue(app.unpair_button.instate(['disabled']))
                    self.assertIn('ждём результат', app.pairing_message.cget('text'))
                    self.assertFalse(app.close())
                    self.assertTrue(w.winfo_exists())
                    release.set()
                    deadline = time.monotonic()+3
                    while app.results.empty() and time.monotonic() < deadline:
                        time.sleep(.01)
                    with patch.object(app.store, 'save', side_effect=[OSError('synthetic storage failure'), None]) as save, patch.object(app, 'start') as start:
                        app.network_tick()
                        self.assertEqual(app.recovered_token, token)
                        self.assertFalse(app.close())
                        self.assertTrue(app.retry_button.instate(['!disabled']))
                        app.retry_button.invoke()
                        app.network_tick()
                        self.assertEqual(save.call_count, 2)
                        save.assert_called_with(token)
                        self.assertIsNone(app.recovered_token)
                        start.assert_not_called()
                    self.assertIsNone(app.tailer)
                    self.assertFalse(app.upload_enabled)
                    self.assertFalse(app.busy)
                    self.assertEqual(app.identity.cget('text'), 'Test Pilot')
                finally:
                    release.set()
                    app.expanded.close()
                    app.legacy.close()
                    q.close()
                    w.destroy()

    def test_visible_retry_consent_expiry_stale_and_copy(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(socket.socket, 'connect', side_effect=AssertionError('offline only')), \
                patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('offline only')), \
                patch('collector.network_gui.CredentialStore.load', return_value=None), \
                patch('collector.connection_status.probe', return_value=None):
            root = Path(temp)
            logs = root / 'Gamelogs'
            logs.mkdir()
            q = PendingQueue(root / 'pending.sqlite3')
            w = tk.Tk()
            errors = []
            w.report_callback_exception = lambda *args: errors.append(args)
            with patch.object(w, 'after'):
                app = ConnectedApp(w, logs, q)
                try:
                    w.update()
                    self.assertTrue(app.site_codes.login_button.winfo_viewable())
                    with patch('collector.network_gui.messagebox.askyesno', return_value=False), patch('collector.network_gui.Pairing') as pair:
                        app.site_codes.login_button.invoke()
                        pair.assert_not_called()
                        self.assertFalse(app.resume_after_pair)
                        self.assertIsNone(app.tailer)
                    with patch('collector.network_gui.messagebox.askyesno', return_value=True), patch.object(app, 'work') as work:
                        app.site_codes.login_button.invoke()
                        work.assert_called_once()
                    app.busy = True
                    app.results.put(('pair', None, OSError('private details'), app.pair_attempt))
                    app.network_tick()
                    self.assertFalse(app.busy)
                    self.assertIsNone(app.tailer)
                    self.assertFalse(app.settings_open)
                    self.assertIn('сеть', app.pairing_message.cget('text'))
                    self.assertNotIn('private', app.pairing_message.cget('text'))
                    for width, height in ((560, 440),):
                        w.geometry(f'{width}x{height}')
                        w.update()
                        for widget in (app.pairing_message, app.retry_button, app.browser_button,
                                       app.link_button, app.settings_button,
                                       app.footer, app.open_site):
                            self.assertTrue(widget.winfo_ismapped(), f'{widget}: {width}x{height}')
                            self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
                            self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
                            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), w.winfo_rootx() + w.winfo_width())
                            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), w.winfo_rooty() + w.winfo_height())
                    with patch('collector.network_gui.messagebox.askyesno', return_value=True), patch.object(app, 'work') as work:
                        app.retry_button.invoke()
                        work.assert_called_once()
                    current = app.pairing
                    app.busy = True
                    app.results.put(('token', {'must_not_save': True}, None, app.pair_attempt - 1))
                    with patch.object(app.store, 'save') as save:
                        app.network_tick()
                        save.assert_not_called()
                    self.assertIs(app.pairing, current)
                    self.assertTrue(app.busy)
                    app.pair_job = (app.pair_attempt, 0)
                    app.network_tick()
                    self.assertFalse(app.busy)
                    self.assertIsNone(app.pairing)
                    app.pairing = types.SimpleNamespace(browser=True, browser_uri=PAIR_URI + '#' + 'A'*43,
                                                       deadline=time.monotonic() + 60)
                    app.refresh_controls()
                    w.clipboard_clear()
                    w.clipboard_append('untouched')
                    self.assertEqual(w.clipboard_get(), 'untouched')
                    app.link_button.invoke()
                    self.assertEqual(w.clipboard_get(), app.pairing.browser_uri)
                    for outcome in (False, OSError('private browser details'), True):
                        with patch('collector.pairing_ux.webbrowser.open',
                                   side_effect=outcome if isinstance(outcome, Exception) else None,
                                   return_value=outcome is True) as browser:
                            app.browser_button.invoke()
                            deadline = time.monotonic() + 3
                            while app.browser_results.empty() and time.monotonic() < deadline:
                                time.sleep(.01)
                            self.assertFalse(app.browser_results.empty(), 'browser worker timed out')
                            app.poll_browser()
                            app.refresh_controls()
                            browser.assert_called_once_with(app.pairing.browser_uri)
                        self.assertNotIn('private', app.pairing_message.cget('text'))
                        self.assertIn('Подтвердите' if outcome is True else 'браузер не открылся', app.pairing_message.cget('text'))
                        self.assertTrue(app.browser_button.instate(['!disabled']))
                    from tools.native_single_smoke import evidence
                    evidence(w, 'pairing-recovery-buttons')
                    app.pairing.deadline = time.monotonic() - 1
                    app.busy = True
                    app.network_tick()
                    self.assertTrue(app.link_button.instate(['disabled']))
                    self.assertTrue(app.browser_button.instate(['disabled']))
                    self.assertFalse(app.busy)
                    self.assertIsNone(app.tailer)
                    self.assertEqual(q.count(), 0)
                    self.assertFalse(errors)
                finally:
                    app.expanded.close()
                    app.legacy.close()
                    q.close()
                    w.destroy()
