"""Late irreversible redemption: real worker, offline callbacks, optional real server."""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import test_pairing_gui
from collector.network_gui import ConnectedApp
from collector.transport import Pairing


def app_fixture():
    fixture = test_pairing_gui.PairingCallbacks()
    fixture.setUp()
    app = fixture.app
    del app.work  # Exercise the real thread and generation-tagged result queue.
    app.pair_attempt = 7
    app.resume_after_pair = True
    app.queue.count.return_value = 0
    return app


class LateRedemption(unittest.TestCase):
    def test_timeout_serializes_late_result_and_close_stops_consent(self):
        app = app_fixture()
        release = threading.Event()
        token = {'scope': 'gamelogs:write'}
        app.work('token', lambda: (release.wait(3), token)[1])
        try:
            app.pair_job = (7, 0)
            app.network_tick()
            self.assertTrue(app.busy)
            self.assertEqual(app.pair_attempt, 7)
            self.assertFalse(app.resume_after_pair)
            with patch.object(app, 'start') as start:
                app.retry_pairing()
                start.assert_not_called()
            with patch('collector.gui.App.close') as close:
                self.assertFalse(app.close())
                close.assert_not_called()
            self.assertFalse(app.upload_enabled)
        finally:
            release.set()
        limit = time.monotonic() + 3
        while app.results.empty() and time.monotonic() < limit:
            time.sleep(.01)
        with patch('collector.network_gui.Uploader') as uploader, patch.object(app, 'start') as start, patch.object(app, 'show_identity'):
            uploader.return_value.credentials = token
            app.network_tick()
            start.assert_not_called()
        app.store.save.assert_called_once_with(token)
        self.assertFalse(app.busy)
        self.assertFalse(app.upload_enabled)
        self.assertIsNone(app.tailer)
        app.queue.clear.assert_not_called()
        with patch('collector.gui.App.close', return_value=True) as close:
            self.assertTrue(app.close())
            close.assert_called_once()

    def test_failed_save_retries_same_key_not_redemption(self):
        app = app_fixture()
        old = app.uploader = Mock()
        token = {'scope': 'gamelogs:write'}
        app.results.put(('token', token, None, 7))
        app.store.save.side_effect = OSError('synthetic')
        with patch('collector.network_gui.Uploader'):
            app.network_tick()
        self.assertIs(app.uploader, old)
        self.assertEqual(app.recovered_token, token)
        with patch.object(app, 'pair') as pair, patch('collector.gui.App.close') as close:
            app.start()
            app.unpair()
            self.assertFalse(app.close())
            pair.assert_not_called()
            close.assert_not_called()
        app.store.save.side_effect = None
        with patch('collector.network_gui.Uploader'), patch.object(app, 'start') as start:
            app.retry_pairing()
            app.network_tick()
            start.assert_not_called()
        self.assertIsNone(app.recovered_token)
        self.assertEqual(app.store.save.call_count, 2)

    def test_stale_token_cannot_replace_existing_credentials(self):
        app = app_fixture()
        old = app.uploader = Mock()
        app.busy = True
        app.results.put(('token', {'scope': 'gamelogs:write'}, None, 6))
        app.network_tick()
        self.assertIs(app.uploader, old)
        app.store.save.assert_not_called()
        self.assertTrue(app.busy)

    def test_terminal_transport_failure_gates_retry_but_allows_close(self):
        app = app_fixture()
        old = app.uploader = Mock()
        app.redemption_pending = True
        app.results.put(('token', None, OSError('synthetic'), 7))
        app.network_tick()
        self.assertTrue(app.redemption_uncertain)
        self.assertFalse(app.redemption_pending)
        self.assertIs(app.uploader, old)
        with patch.object(app, 'start') as start:
            app.retry_pairing()
            start.assert_not_called()
        with patch('collector.gui.App.close', return_value=True):
            self.assertTrue(app.close())
        app.store.save.assert_not_called()


SERVER = os.environ.get('SPHOL_SERVER_PATH')
if SERVER:
    sys.path.insert(0, SERVER)
    sys.path.insert(0, str(Path(SERVER) / 'tests'))
    import test_collector_api as server_fixture
    from store_fixture import TempStoreMixin

    class RealServerLateRedemption(TempStoreMixin, unittest.TestCase):
        request = server_fixture.CollectorTests.request
        def setUp(self):
            super().setUp()
            server = server_fixture.server
            self.store = server.get_store()
            self.api = server_fixture.c.Collector(self.store.path)
            self.store.open_session('fixture', character_id=42, character_name='Test Pilot',
                                    corporation_id=123, roles=[], scopes=[], ttl_seconds=3600)
            for name, value in [('home_corporation_id', 123), ('character_corporation', 123)]:
                p = patch.object(server, name, return_value=value)
                p.start()
                self.addCleanup(p.stop)
            for name, value in [('PUBLIC_ACCESS', True), ('ALLOWED_ORIGINS', {'https://sphol.com'})]:
                p = patch.object(server, name, value)
                p.start()
                self.addCleanup(p.stop)

        def test_real_handler_redeems_once_then_late_gui_saves_key(self):
            app = app_fixture()
            fixture = self
            redeemed, release = threading.Event(), threading.Event()
            calls = []
            class HTTP:
                def post(self, path, body, **kwargs):
                    calls.append(path)
                    response = fixture.request(path.split('/v1/')[1], body, cookie=False, **kwargs)
                    if path.endswith('/token'):
                        redeemed.set()
                        release.wait(4)
                    return response
            pairing = app.pairing = Pairing(http=HTTP(), scope='gamelogs:write')
            code = pairing.start()
            self.assertEqual(self.request('approval', {'user_code': code, 'consent': True, 'scope': 'gamelogs:write'})[0], 200)
            pairing.next_poll = 0
            original = {'device_secret': pairing.secret, 'verifier': pairing.verifier}
            app.work('token', pairing.poll)
            try:
                self.assertTrue(redeemed.wait(3))
                self.assertEqual(self.request('pairings/token', original, cookie=False)[0], 410)
                app.pair_job = (7, 0)
                app.network_tick()
                for _ in range(3):
                    app.retry_pairing()
                    app.pair()
                self.assertEqual(len(calls), 2)
                self.assertTrue(app.busy)
                with self.api.transaction() as con:
                    self.assertEqual(con.execute('SELECT count(*) FROM collector_installations').fetchone()[0], 1)
            finally:
                release.set()
            limit = time.monotonic() + 3
            while app.results.empty() and time.monotonic() < limit:
                time.sleep(.01)
            app.network_tick()
            saved = app.store.save.call_args.args[0]
            self.assertEqual(self.api.authenticate(saved['access_token'], lambda *a: None)['character_id'], 42)
            self.assertFalse(app.upload_enabled)
            self.assertIsNone(app.tailer)
            self.assertFalse(app.resume_after_pair)
            app.queue.clear.assert_not_called()
else:
    class RealServerLateRedemption(unittest.TestCase):
        @unittest.skip('Set SPHOL_SERVER_PATH for real server fixture')
        def test_real_handler_redeems_once_then_late_gui_saves_key(self):
            pass
