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
                     'store', 'queue', 'status', 'work'):
            setattr(self.app, name, Mock())
        self.app.pairing_code = Value()
        self.app.pairing = types.SimpleNamespace(deadline=float('inf'), poll=Mock())
        self.app.uploader = None
        self.app.upload_enabled = False
        self.app.busy = False
        self.app.results = queue.Queue()

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
