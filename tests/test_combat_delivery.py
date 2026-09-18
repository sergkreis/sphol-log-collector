"""Combat policy gates, synthetic queues only; no credential mutation."""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from collector.core import PendingQueue
from collector.network_gui import ConnectedApp
from collector.expanded_gui import ExpandedControls
from collector.legacy_backlog import LegacyBacklog
from collector.transport import build_batch
from test_transport import credentials, event

class CombatDelivery(unittest.TestCase):
    def test_unknown_prefix_no_starvation_and_both_scopes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp, closing(PendingQueue(Path(tmp)/'q')) as q:
            for i in range(150):
                q.put(f'{i:064x}', {'schema': 7, 'text': 'unknown'})
            e={**event(), 'id':'f'*64}; q.put(e['id'], {k:v for k,v in e.items() if k != 'id'})
            before=q.db.execute('SELECT * FROM pending').fetchall()
            for scope in ('combat:write','gamelogs:write'):
                c={**credentials(), 'scope':scope}; saved=dict(c)
                self.assertEqual(build_batch(q,c)['events'], [e])
                self.assertEqual(c,saved)
            self.assertEqual(q.db.execute('SELECT * FROM pending').fetchall(),before)

    def test_dormant_controls_do_not_upgrade_or_dispatch(self):
        panel=ExpandedControls.__new__(ExpandedControls)
        panel.status=Mock();panel.work=Mock();panel.uploader=Mock()
        panel.approve();panel.start(integrated=True)
        panel.work.assert_not_called()
        legacy=LegacyBacklog.__new__(LegacyBacklog);legacy.enabled=True
        legacy.authorize();self.assertFalse(legacy.enabled)

    def test_combat_start_preserves_old_binding_without_upgrade(self):
        from collector.gui import App
        for scope in ('combat:write','gamelogs:write'):
            app=ConnectedApp.__new__(ConnectedApp)
            app._poll_failed=False; app.pairing=None; app.busy=False
            app.uploader=Mock();app.uploader.credentials={**credentials(),'scope':scope};app.uploader.paused=False
            app.expanded=Mock();app.legacy=Mock();app.refresh_controls=Mock();app.tailer=object()
            saved=app.uploader.credentials
            with patch('collector.network_gui.blocked',return_value=False),patch.object(App,'start'):
                app.start()
            self.assertIs(app.uploader.credentials,saved)
            self.assertTrue(app.upload_enabled)
            app.expanded.bind.assert_not_called();app.expanded.start.assert_not_called();app.legacy.authorize.assert_not_called()
