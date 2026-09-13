"""Headless callback tests, NOT native Tk rendering / Windows tests."""
import importlib
import sys
import types
import unittest
from unittest.mock import Mock, patch

try:
    from collector import gui
except ModuleNotFoundError as error:
    if error.name != 'tkinter':
        raise
    tk = types.ModuleType('tkinter')
    tk.messagebox = types.SimpleNamespace(askyesno=Mock())
    tk.ttk = types.ModuleType('tkinter.ttk')
    with patch.dict(sys.modules, {'tkinter': tk}):
        gui = importlib.import_module('collector.gui')


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.app = gui.App.__new__(gui.App)
        self.app.window = Mock()
        self.app.queue = Mock()
        self.app.queue.count.return_value = 2
        self.app.tailer = Mock()
        self.app.status = Mock()
        self.app.pending = Mock()

    def test_close_cancel_keeps_running(self):
        with patch.object(gui.messagebox, 'askyesno', return_value=False) as prompt:
            self.app.close()
        prompt.assert_called_once()
        self.app.queue.close.assert_not_called()
        self.app.window.destroy.assert_not_called()

    def test_close_accept_ends_foreground_and_keeps_queue(self):
        with patch.object(gui.messagebox, 'askyesno', return_value=True):
            self.app.close()
        self.app.queue.close.assert_called_once()
        self.app.queue.clear.assert_not_called()
        self.app.window.destroy.assert_called_once()
        self.assertIsNone(self.app.tailer)

    def test_empty_close_needs_no_warning(self):
        self.app.queue.count.return_value = 0
        with patch.object(gui.messagebox, 'askyesno') as prompt:
            self.app.close()
        prompt.assert_not_called()
        self.app.window.destroy.assert_called_once()

    def test_full_status_truthful_and_cursor_retained(self):
        self.app.tailer.poll.side_effect = gui.QueueFull()
        tailer = self.app.tailer
        self.app.tick()
        self.assertIs(self.app.tailer, tailer)
        self.assertIn('not uploaded', self.app.status.set.call_args.args[0])
        self.app.window.after.assert_called_once()

    def test_error_stops_without_exposing_content(self):
        self.app.tailer.poll.side_effect = OSError('sensitive local details')
        self.app.tick()
        self.assertIsNone(self.app.tailer)
        self.assertNotIn('sensitive', self.app.status.set.call_args.args[0])

    def test_stop_is_disconnected(self):
        self.app.stop()
        self.assertIsNone(self.app.tailer)
        self.assertIn('NOT CONNECTED', self.app.status.set.call_args.args[0])
