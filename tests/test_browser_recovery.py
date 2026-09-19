"""Private fixture optional; all HTTP restricted to loopback by fixture."""
import os
import sys
import tempfile
import http.client
import time
import types
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from collector.browser_recovery import BrowserRecovery, BrowserPendingStore, validate_claim
from collector.transport import ProtocolError, valid_eve_authorize_url
from collector.browser_gui import BrowserGUI
from collector.pairing_ux import active_url, PairingUX
from test_site_code import Memory, CREDENTIAL


OFFICIAL_EVE_URL = 'https://login.eveonline.com/v2/oauth/authorize?response_type=code&client_id=client&redirect_uri=https%3A%2F%2Fsphol.com%2Fsso%2Feve%2Fcallback&state=collector_' + 'B'*32


class BrowserTests(unittest.TestCase):
    def test_start_persists_exact_official_url(self):
        pending, store, http = Memory(), Memory(), Mock()
        http.post.return_value = (201, dict(device_secret='D'*43, user_code='U'*43,
                                           verification_uri=OFFICIAL_EVE_URL, expires_in=120, interval=5))
        self.assertEqual(BrowserRecovery(pending, store, http).start('combat:write'), OFFICIAL_EVE_URL)
        claim = pending.load()
        self.assertEqual(claim['browser_uri'], OFFICIAL_EVE_URL)
        self.assertEqual(validate_claim(claim), claim)

    def test_legacy_pending_recovers_official_url_without_new_device(self):
        pending, store, http = Memory(), Memory(), Mock()
        claim = dict(device_secret='d'*43, verifier='e'*43, scope='combat:write', recovery=True,
                     browser_proof='f'*43, approval_expires=time.time()-60)
        pending.save(claim)
        http.post.return_value = (200, dict(verification_uri=OFFICIAL_EVE_URL, expires_in=120, interval=5))
        self.assertEqual(BrowserRecovery(pending, store, http).recover_url(), OFFICIAL_EVE_URL)
        http.post.assert_called_once_with('/api/collector/v1/pairings/browser/recover', {
            'device_secret': claim['device_secret'], 'verifier': claim['verifier'],
            'browser_proof': claim['browser_proof'], 'scope': 'combat:write', 'recovery': True})
        recovered = pending.load()
        self.assertEqual(recovered['browser_uri'], OFFICIAL_EVE_URL)
        self.assertEqual(recovered['browser_proof'], claim['browser_proof'])
        self.assertGreater(recovered['approval_expires'], time.time())
        self.assertIsNone(store.load())

    def test_restore_restart_uses_official_url_and_legacy_no_url_is_visible(self):
        class App(BrowserGUI, PairingUX):
            busy = False
            uploader = None
            def __init__(self, claim):
                self.browser_pending = types.SimpleNamespace(path=types.SimpleNamespace(exists=lambda: True), load=lambda: claim)
                self.store = Memory()
                self.notices = []
                self.pairing_message = Mock()
                self.browser_recovery = Mock()
                self.work = Mock()
            def pairing_notice(self, text): self.notices.append(text)
        claim = dict(device_secret='a'*43, verifier='b'*43, scope='combat:write', recovery=True,
                     browser_proof='c'*43, approval_expires=time.time()+120, browser_uri=OFFICIAL_EVE_URL)
        app = App(claim); app.restore_browser()
        self.assertEqual(active_url(app.pairing), OFFICIAL_EVE_URL)
        legacy = dict(claim); legacy.pop('browser_uri')
        app = App(legacy); app.restore_browser(); app.open_browser()
        self.assertIsNone(active_url(app.pairing))
        app.work.assert_called_once_with('browser_recover_url', app.browser_recovery.recover_url)
        self.assertEqual(app.browser_pending.load(), legacy)

    def test_expired_pending_has_actionable_error_without_deletion(self):
        class App(BrowserGUI, PairingUX):
            busy = False
            uploader = None
            def __init__(self, claim):
                self.browser_pending = types.SimpleNamespace(path=types.SimpleNamespace(exists=lambda: True), load=lambda: claim)
                self.store = Memory()
                self.notices = []
                self.pairing_message = Mock()
                self.browser_recovery = Mock()
                self.work = Mock()
            def pairing_notice(self, text): self.notices.append(text)
        claim = dict(device_secret='a'*43, verifier='b'*43, scope='combat:write', recovery=True,
                     browser_proof='c'*43, approval_expires=time.time()-1, browser_uri=OFFICIAL_EVE_URL)
        app = App(claim); app.restore_browser(); app.open_browser()
        self.assertIsNone(active_url(app.pairing))
        app.work.assert_called_once_with('browser_recover_url', app.browser_recovery.recover_url)
        self.assertEqual(app.browser_pending.load(), claim)

    def test_binding_and_readback_preserve_claim(self):
        for existing in (dict(CREDENTIAL, installation_id='other'), None):
            pending, store = Memory(), Memory()
            pending.save(dict(device_secret='a'*43, verifier='b'*43, scope='combat:write', recovery=True))
            store.save(existing)
            if existing is None:
                store.save = Mock()
            client = BrowserRecovery(pending, store, Mock(post=Mock(return_value=(200, CREDENTIAL))))
            with self.assertRaises(ProtocolError): client.redeem()
            self.assertIsNotNone(pending.load())
            self.assertEqual(store.load(), existing)

    @unittest.skipUnless(os.name == 'nt', 'Requires actual Windows DPAPI')
    def test_dpapi_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'browser.dpapi'
            claim = dict(device_secret='a'*43, verifier='b'*43, scope='combat:write', recovery=True)
            BrowserPendingStore(path).save(claim)
            self.assertNotIn(claim['verifier'].encode(), path.read_bytes())
            self.assertEqual(BrowserPendingStore(path).load(), claim)

    @unittest.skipUnless(os.environ.get('SPHOL_TEST_SERVER_SOURCE'), 'Requires private server fixture')
    def test_http_commit_drop_restart_and_denials(self):
        source = Path(os.environ['SPHOL_TEST_SERVER_SOURCE'])
        with patch.dict(os.environ, {'SPHOL_CLIENT_PATH': str(Path(__file__).resolve().parents[1]), 'EVE_DASHBOARD_NO_COLLECTOR': '1'}):
            sys.path[:0] = [str(source), str(source/'tests')]
            try:
                from test_collector_site_codes import SiteCodeHTTPTests
                from collector_api import Collector
                import server
            finally:
                del sys.path[:2]
            fixture = SiteCodeHTTPTests(methodName='runTest')
            fixture.setUp()
            try:
                with tempfile.TemporaryDirectory() as directory:
                    # Explicit plaintext synthetic disk double, NOT a DPAPI substitute.
                    import json
                    class Disk:
                        path = Path(directory)/'synthetic.json'
                        def load(self):
                            return json.loads(self.path.read_text()) if self.path.exists() else None
                        def save(self, value): self.path.write_text(json.dumps(value))
                        def clear(self): self.path.unlink()
                    pending, store = Disk(), Memory()
                    with patch.object(server, 'SSO_CLIENT_ID', 'client-id'), patch.object(server, 'SSO_CLIENT_SECRET', 'secret'):
                        client = BrowserRecovery(pending, store)
                        uri = client.start('combat:write')
                    claim = pending.load()
                    self.assertTrue(valid_eve_authorize_url(uri))
                    # Simulate 0.3.11 pending storage: device/verifier/proof exist,
                    # official browser_uri is missing and the original request expired.
                    legacy = dict(claim)
                    legacy.pop('browser_uri')
                    legacy['approval_expires'] = time.time()-60
                    pending.save(legacy)
                    with patch.object(server, 'SSO_CLIENT_ID', 'client-id'), patch.object(server, 'SSO_CLIENT_SECRET', 'secret'):
                        recovered_uri = client.recover_url()
                    recovered = pending.load()
                    self.assertTrue(valid_eve_authorize_url(recovered_uri))
                    self.assertEqual(recovered['browser_uri'], recovered_uri)
                    self.assertEqual(recovered['browser_proof'], claim['browser_proof'])
                    self.assertNotEqual(recovered_uri, uri)
                    claim = recovered
                    import urllib.parse
                    oauth_state = urllib.parse.parse_qs(urllib.parse.urlparse(recovered_uri).query)['state'][0]
                    import eve_sso
                    fixture.api.validate_oauth_state(oauth_state)
                    fixture.api.approve_oauth(oauth_state, eve_sso.Identity(42, 'Test Pilot', (), 9999999999, 'token'), 123)
                    send, committed = server.Handler.send_json, []
                    def lose(handler, status, payload):
                        if handler.path.endswith('/pairings/token') and status == 200:
                            committed.append(payload)
                            handler.close_connection = True
                            return False
                        return send(handler, status, payload)
                    with patch.object(server.Handler, 'send_json', lose):
                        with self.assertRaises(http.client.RemoteDisconnected): client.redeem()
                    self.assertEqual(len(committed), 1)
                    self.assertEqual(pending.load(), claim)
                    # Recreate SQLite authority against the same disk, no in-memory token cache.
                    import threading
                    fixture.stop_http()
                    fixture.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
                    fixture.thread = threading.Thread(target=fixture.httpd.serve_forever, daemon=True)
                    fixture.thread.start()
                    fixture.api = Collector(fixture.store.path)
                    self.assertEqual(BrowserRecovery(Disk(), store).redeem(), committed[0])
                    self.assertIsNone(pending.load())
                    def retry(body): return fixture.request('pairings/token', {k: body[k] for k in ('device_secret', 'verifier', 'scope', 'recovery')}, auth=False, csrf=False)
                    self.assertEqual(retry(claim), (200, committed[0]))
                    self.assertEqual(retry(dict(claim, verifier='c'*43))[0], 409)
                    self.assertEqual(retry(dict(claim, scope='gamelogs:write'))[0], 403)
                    with fixture.api.transaction() as con:
                        self.assertEqual(con.execute('SELECT count(*) FROM collector_installations').fetchone()[0], 1)
                        con.execute('UPDATE collector_pairings SET expires=0')
                    self.assertEqual(retry(claim)[0], 200)
                    with patch.dict(os.environ, {'SPHOL_COLLECTOR_RECOVERY_KEY': ''}):
                        self.assertEqual(retry(claim)[0], 503)
                    with patch.dict(os.environ, {'SPHOL_COLLECTOR_RECOVERY_KEY': '00'*32}):
                        self.assertEqual(retry(claim)[0], 503)
                    fixture.character_corporation.return_value = 999
                    self.assertEqual(retry(claim)[0], 403)
                    fixture.character_corporation.return_value = 123
                    with fixture.api.transaction() as con:
                        con.execute('UPDATE collector_installations SET revoked=1')
                    self.assertEqual(retry(claim)[0], 410)
            finally:
                fixture.doCleanups()
                fixture.tearDown()
