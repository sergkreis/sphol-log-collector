"""Headless v2 consent callbacks; not native Windows GUI certification."""
import unittest
from unittest.mock import Mock, patch
from test_pairing_gui import gui  # Installs Tk stub only when Tk unavailable.
import sys
with patch.dict(sys.modules, {'tkinter': gui.tk}):
    from collector.expanded_gui import ExpandedControls
    expanded_gui = sys.modules['collector.expanded_gui']
from collector.expanded import SCOPE
from test_transport import credentials


class ExpandedCallbacks(unittest.TestCase):
    def setUp(self):
        self.panel = ExpandedControls.__new__(ExpandedControls)
        self.panel.app = Mock()
        self.panel.status = Mock()
        self.panel.queue = Mock()
        self.panel.work = Mock()
        self.panel.busy = self.panel.enabled = False
        self.panel.tailer = self.panel.pairing = self.panel.uploader = None

    def test_no_scope_no_start_and_no_capture(self):
        with patch.object(expanded_gui, 'capture') as capture:
            self.panel.start()
        capture.assert_not_called()
        self.assertFalse(self.panel.enabled)
        self.assertIn('отключён', self.panel.status.config.call_args.kwargs['text'])

    def test_decline_separate_consent_never_reads(self):
        self.panel.uploader = Mock(paused=False, credentials={**credentials(),'scope':SCOPE})
        with patch.object(expanded_gui.messagebox, 'askyesno',return_value=False), patch.object(expanded_gui, 'capture') as capture:
            self.panel.start()
        capture.assert_not_called()
        self.assertFalse(self.panel.enabled)
        self.panel.app.local_capture.assert_not_called()

    def test_new_browser_scope_does_not_touch_legacy(self):
        with patch.object(expanded_gui.messagebox, 'askyesno',return_value=True), patch.object(expanded_gui, 'Pairing') as pairing:
            self.panel.approve()
        pairing.assert_not_called()
        self.panel.queue.clear.assert_not_called()
        self.panel.app.store.clear.assert_not_called()
        self.panel.app.queue.clear.assert_not_called()
        self.assertFalse(self.panel.enabled)

    def test_stop_preserves_queue_and_legacy(self):
        self.panel.enabled=True
        self.panel.tailer=Mock()
        self.panel.stop()
        self.assertIsNone(self.panel.tailer)
        self.assertFalse(self.panel.enabled)
        self.panel.queue.clear.assert_not_called()
        self.panel.app.store.clear.assert_not_called()
