"""Private fixture optional; all HTTP restricted to loopback by fixture."""
import os
import sys
import tempfile
import http.client
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from collector.browser_recovery import BrowserRecovery, BrowserPendingStore
from collector.transport import ProtocolError
from test_site_code import Memory, CREDENTIAL


class BrowserTests(unittest.TestCase):
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
                    client = BrowserRecovery(pending, store)
                    uri = client.start('combat:write')
                    claim = pending.load()
                    self.assertEqual(fixture.request('approval', {'user_code': uri.split('#')[1], 'consent': True})[0], 200)
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
