"""Synthetic headless pairing UI callbacks; native controls covered separately."""
import importlib
import queue
import sys
import types
import unittest
from unittest.mock import Mock, patch

try:
    from collector import network_gui as gui
except ModuleNotFoundError as error:
    if error.name != 'tkinter':
        raise
    tk = types.ModuleType('tkinter')
    tk.messagebox = types.SimpleNamespace(askyesno=Mock())
    tk.ttk = types.ModuleType('tkinter.ttk')
    tk.TclError = type('TclError', (Exception,), {})
    with patch.dict(sys.modules, {'tkinter': tk}):
        gui = importlib.import_module('collector.network_gui')
    if not hasattr(gui.tk, 'TclError'):
        gui.tk.TclError = tk.TclError


class Value:
    def __init__(self):
        self.value = ''

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class PairingCallbacks(unittest.TestCase):
    def setUp(self):
        self.app = gui.ConnectedApp.__new__(gui.ConnectedApp)
        for name in ('window', 'copy_button', 'copy_feedback', 'connection',
                     'store', 'queue', 'status', 'work', 'identity', 'last_ack',
                     'code_frame', 'pair_button', 'enable_button', 'unpair_button',
                     'upload_state', 'start_button', 'stop_button'):
            setattr(self.app, name, Mock())
        self.app.pairing_code = Value()
        self.app.pairing = types.SimpleNamespace(deadline=float('inf'), poll=Mock())
        self.app.uploader = None
        self.app.upload_enabled = False
        self.app.busy = False
        self.app.results = queue.Queue()
        self.app.tailer = None

    def ready(self):
        self.app.results.put(('pair', 'TEST-1234', None))
        with patch.object(gui.webbrowser, 'open'):
            self.app.network_tick()

    def assert_cleared(self):
        self.assertEqual(self.app.pairing_code.get(), '')
        self.app.copy_button.config.assert_called_with(state='disabled')
        self.app.window.clipboard_clear.assert_not_called()

    def test_explicit_copy_matches_displayed_code(self):
        self.app.copy_pairing_code()
        self.app.window.clipboard_append.assert_not_called()
        self.ready()
        self.app.window.clipboard_clear.assert_not_called()
        self.app.copy_button.config.assert_called_with(state='normal')
        self.app.copy_pairing_code()
        self.app.window.clipboard_clear.assert_called_once()
        self.app.window.clipboard_append.assert_called_once_with(self.app.pairing_code.get())
        self.app.copy_feedback.config.assert_called_with(text='Код скопирован')

    def test_expired_click_cannot_copy(self):
        self.ready()
        self.app.pairing.deadline = 0
        self.app.copy_pairing_code()
        self.assert_cleared()

    def test_expiry_clears_even_while_worker_busy(self):
        self.ready()
        self.app.busy = True
        self.app.pairing.deadline = 0
        self.app.network_tick()
        self.assert_cleared()

    def test_error_clears(self):
        self.ready()
        self.app.results.put(('token', None, 'ProtocolError'))
        self.app.network_tick()
        self.assert_cleared()

    def test_success_and_storage_failure_clear(self):
        for failed in (False, True):
            with self.subTest(storage_failed=failed):
                self.setUp()
                self.ready()
                self.app.results.put(('token', {'synthetic': True}, None))
                if failed:
                    self.app.store.save.side_effect = OSError('private details')
                with patch.object(gui, 'Uploader'), patch.object(self.app, 'show_identity'):
                    self.app.network_tick()
                self.assert_cleared()
                self.assertIsNone(self.app.pairing)

    def test_unpair_clears(self):
        self.ready()
        with patch.object(gui.messagebox, 'askyesno', return_value=True):
            self.app.unpair()
        self.assert_cleared()

    def test_clipboard_error_has_no_sensitive_details(self):
        self.ready()
        self.app.window.clipboard_append.side_effect = gui.tk.TclError('secret details')
        self.app.copy_pairing_code()
        text = self.app.copy_feedback.config.call_args.kwargs['text']
        self.assertNotIn('secret', text)
        self.assertNotIn('TEST-1234', text)
        self.assertNotEqual(text, 'Код скопирован')

    def test_empty_polls_preserve_identity_ack_and_consent(self):
        app = self.app
        app.pairing = None
        app.queue.batch.return_value = []
        app.queue.count.return_value = 0
        app.uploader = types.SimpleNamespace(paused=False, credentials={
            'scope': 'gamelogs:write', 'characters': [{'name': 'Synthetic Pilot'}], 'expires_at': '2099-01-01T00:00:00Z'})
        app.show_identity()
        app.identity.reset_mock()
        app.network_tick()
        app.work.assert_not_called()
        self.assertFalse(app.upload_enabled)
        app.tailer = object()
        app.start()
        self.assertTrue(app.upload_enabled)
        for _ in range(3):
            app.network_tick()
        app.work.assert_not_called()
        app.identity.config.assert_not_called()
        app.last_ack.config.assert_not_called()
        app.pair_button.config.assert_called_with(state='disabled')
        snapshot = gui.Snapshot([])
        snapshot.accepted = ['synthetic-id']
        app.results.put(('upload', (snapshot, 'Сервер подтвердил сохранение'), None))
        app.network_tick()
        app.last_ack.config.assert_called_once()
        app.network_tick()
        app.last_ack.config.assert_called_once()
        app.results.put(('upload', None, 'OSError'))
        app.network_tick()
        self.assertFalse(app.upload_enabled)
        app.identity.config.assert_not_called()
        app.last_ack.config.assert_called_once()
