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
                    with patch('collector.network_gui.messagebox.askyesno', return_value=False), patch('collector.network_gui.Pairing') as pair:
                        app.main_button.invoke()
                        pair.assert_not_called()
                        self.assertFalse(app.resume_after_pair)
                        self.assertIsNone(app.tailer)
                    with patch('collector.network_gui.messagebox.askyesno', return_value=True), patch.object(app, 'work') as work:
                        app.main_button.invoke()
                        work.assert_called_once()
                    app.busy = True
                    app.results.put(('pair', None, OSError('private details'), app.pair_attempt))
                    app.network_tick()
                    self.assertFalse(app.busy)
                    self.assertIsNone(app.tailer)
                    self.assertFalse(app.settings_open)
                    self.assertIn('сеть', app.pairing_message.cget('text'))
                    self.assertNotIn('private', app.pairing_message.cget('text'))
                    for width, height in ((780, 700), (760, 660)):
                        w.geometry(f'{width}x{height}')
                        w.update()
                        for widget in (app.pairing_message, app.retry_button, app.main_button):
                            self.assertTrue(widget.winfo_ismapped())
                            self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())
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
