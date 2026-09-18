"""Synthetic callback faults: no sockets, no real queues."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from collector.gui import App

class SchedulerTests(unittest.TestCase):
    def app(self):
        app = App.__new__(App)
        app.window = Mock()
        app.window.after.return_value = 'timer'
        app.local_capture = None
        app.tailer = None
        app.queue = Mock()
        app.queue.count.return_value = 0
        app.status = Mock()
        app.pending = Mock()
        app.upload_enabled = True
        app.start_button = Mock()
        app.main_button = Mock()
        app.uploader = None
        return app

    def test_callback_fault_fails_closed(self):
        app = self.app()
        with patch('collector.dashboard.refresh', side_effect=RuntimeError('synthetic')), patch('collector.queue_status.queue_summary', return_value='synthetic'):
            app.tick()
        self.assertFalse(app.upload_enabled)
        self.assertTrue(app.capture_problem)
        self.assertTrue(app._poll_failed)
        app.window.after.assert_not_called()
        self.assertIsNone(app.tailer)
        app.tick()
        app.start()
        self.assertIsNone(app.tailer)
        app.window.after.assert_not_called()

    def test_stream_fault_isolation_and_reentrancy(self):
        from collector.poll_scheduler import scheduled_poll
        class Stream:
            def __init__(self):
                self.window = Mock()
                self.status = Mock()
                self.tailer = SimpleNamespace(stopped=False)
                self.enabled = True
                self.calls = 0
            @scheduled_poll
            def tick(self):
                self.calls += 1
                self.tick()  # Nested Tk callback cannot duplicate polling.
                if self.calls == 1:
                    raise RuntimeError('synthetic')
        first, second = Stream(), Stream()
        original = first.tailer
        first.tick()
        self.assertTrue(original.stopped)
        self.assertFalse(first.enabled)
        self.assertIsNotNone(second.tailer)
        self.assertTrue(second.enabled)
        self.assertEqual(first.calls, 1)
        first.tick()
        self.assertEqual(first.calls, 1)

    def test_manual_tick_cancels_previous_callback(self):
        app = self.app()
        with patch('collector.dashboard.refresh'), patch('collector.queue_status.queue_summary', return_value='synthetic'):
            app.tick()
            app.tick()
        app.window.after_cancel.assert_called_once_with('timer')
        self.assertEqual(app.window.after.call_count, 2)
