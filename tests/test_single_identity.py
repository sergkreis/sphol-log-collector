"""Original-identity backlog is delivery-only, independent of current capture."""
import unittest
from unittest.mock import Mock, patch
from test_pairing_gui import gui
from collector.network_gui import ConnectedApp
from collector.legacy_backlog import LegacyBacklog


class SingleIdentity(unittest.TestCase):
    def test_current_start_does_not_rebind_legacy(self):
        app = ConnectedApp.__new__(ConnectedApp)
        app.busy = False
        app.pairing = None
        app.tailer = Mock()
        app.uploader = Mock(paused=False, credentials={'scope':'gamelogs:write'})
        app.expanded = Mock(busy=False)
        app.legacy = Mock()
        app.refresh_controls = Mock()
        old = app.legacy.uploader
        with patch.object(gui.App, 'start') as start:
            app.start()
        start.assert_called_once()
        app.expanded.bind.assert_called_once_with(app.uploader.credentials)
        app.expanded.start.assert_called_once_with(integrated=True)
        app.legacy.authorize.assert_called_once()
        app.legacy.queue.clear.assert_not_called()
        self.assertIs(app.legacy.uploader, old)

    def test_declined_backlog_consent_retains_identity_and_data(self):
        backlog = LegacyBacklog.__new__(LegacyBacklog)
        backlog.queue = Mock()
        backlog.queue.count.return_value = 3
        backlog.uploader = Mock(paused=False, credentials={'characters':[{'id':99,'name':'Previous Pilot'}]})
        with patch('collector.legacy_backlog.messagebox.askyesno', return_value=False) as consent:
            backlog.authorize()
        self.assertIn('Previous Pilot', consent.call_args.args[1])
        self.assertFalse(backlog.enabled)
        backlog.queue.clear.assert_not_called()
        backlog.queue.acknowledge.assert_not_called()

    def test_missing_legacy_credentials_never_asks_or_sends(self):
        backlog = LegacyBacklog.__new__(LegacyBacklog)
        backlog.queue = Mock()
        backlog.uploader = None
        with patch('collector.legacy_backlog.messagebox.askyesno') as consent:
            backlog.authorize()
        consent.assert_not_called()
        self.assertFalse(backlog.enabled)
