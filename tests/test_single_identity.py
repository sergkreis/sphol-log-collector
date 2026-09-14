"""Migration must not implicitly start uploading another character's backlog."""
import unittest
from unittest.mock import Mock, patch
from test_pairing_gui import gui

class SingleIdentity(unittest.TestCase):
    def test_different_character_backlog_blocks_without_mutation(self):
        from collector.network_gui import ConnectedApp
        app = ConnectedApp.__new__(ConnectedApp)
        app.busy = False
        app.pairing = None
        app.uploader = Mock(credentials={'scope':'gamelogs:write','characters':[{'id':42,'name':'Current'}]})
        app.expanded = Mock()
        app.expanded.uploader.credentials = {'characters':[{'id':99,'name':'Previous'}]}
        app.expanded.queue.count.return_value = 1
        app.connection = Mock()
        app.refresh_controls = Mock()
        old = app.expanded.uploader
        with patch.object(gui.App, 'start') as start:
            app.start()
        start.assert_not_called()
        app.expanded.start.assert_not_called()
        app.expanded.queue.clear.assert_not_called()
        app.expanded.queue.acknowledge.assert_not_called()
        self.assertIs(app.expanded.uploader, old)
        self.assertTrue(app.expanded.problem)
