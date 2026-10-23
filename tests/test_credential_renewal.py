"""Same-installation renewal; optional private server remains outside this repo."""
import http.client
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import gc
import subprocess
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch
from urllib.parse import urlparse, parse_qs

from collector.browser_recovery import BrowserRecovery, BrowserPendingStore
from collector.credentials import CredentialStore
from collector.transport import ProtocolError, encode
from collector.core import PendingQueue
from test_browser_recovery import OFFICIAL_EVE_URL

OLD = dict(access_token='a'*43, token_type='Bearer', installation_id='b'*32,
           scope='gamelogs:write', expires_at='2000-01-01T00:00:00Z',
           characters=[dict(id=42, name='Test Pilot')])
NEW = dict(OLD, access_token='c'*43, expires_at='2099-01-01T00:00:00Z')


class RenewalTests(unittest.TestCase):
    def state(self, stack, old=OLD):
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        # Explicit synthetic encryption boundary on Linux, never a runtime fallback.
        if os.name != 'nt':
            for module in ('credentials', 'browser_recovery'):
                stack.enter_context(patch('collector.'+module+'.crypt', side_effect=lambda raw, **kw: raw))
        from collector.credentials import crypt
        store = CredentialStore(root/'credentials.dpapi')
        store.path.write_bytes(crypt(encode(old)))
        pending = BrowserPendingStore(root/'browser-redemption.dpapi')
        queue = PendingQueue(root/'queue.sqlite3')
        stack.callback(queue.close)
        queue.put('event-original', dict(schema=1, time='2026-01-01T00:00:00Z', type='combat', listener='Test Pilot', text='Synthetic'))
        return store, pending, queue

    def started(self, stack):
        store, pending, queue = self.state(stack)
        http = Mock()
        http.post.return_value = (200, dict(device_secret='d'*43, user_code='e'*43,
            verification_uri=OFFICIAL_EVE_URL, expires_in=120, interval=5))
        client = BrowserRecovery(pending, store, http)
        client.start(OLD['scope'], renewal=store.load_proof())
        self.assertEqual(http.post.call_args.args[1]['renewal'],
                         dict(installation_id=OLD['installation_id'], old_token=OLD['access_token']))
        self.assertEqual(http.post.call_args.kwargs, {})
        http.post.return_value = (200, NEW)
        return store, pending, queue, client, http

    def test_expired_proof_is_not_upload_authority(self):
        with ExitStack() as stack:
            store, pending, queue = self.state(stack)
            self.assertEqual(store.load_proof(), OLD)
            with self.assertRaises(ProtocolError): store.load()
            from collector.transport import Uploader
            with self.assertRaises(ProtocolError): Uploader(OLD)
            http = Mock()
            with self.assertRaises(ProtocolError): BrowserRecovery(pending, store, http).start('combat:write')
            http.post.assert_not_called()

    def test_wrong_identity_scope_and_missing_proof_preserve_every_byte(self):
        for changed in (dict(installation_id='f'*32), dict(characters=[dict(id=99, name='Other')]), dict(scope='combat:write')):
            with self.subTest(changed=changed), ExitStack() as stack:
                store, pending, queue, client, http = self.started(stack)
                before = (store.path.read_bytes(), pending.path.read_bytes(), queue.batch(10))
                http.post.return_value = (200, dict(NEW, **changed))
                with self.assertRaises(ProtocolError): client.redeem()
                self.assertEqual((store.path.read_bytes(), pending.path.read_bytes(), queue.batch(10)), before)
        with ExitStack() as stack:
            store, pending, queue, client, http = self.started(stack)
            store.path.unlink()
            with self.assertRaises(ProtocolError): client.redeem()
            self.assertIsNotNone(pending.load())
            self.assertFalse(store.path.exists())

    def test_save_and_readback_failure_keep_exact_claim_then_retry(self):
        for fault in ('save', 'readback'):
            with self.subTest(fault=fault), ExitStack() as stack:
                store, pending, queue, client, http = self.started(stack)
                claim = pending.path.read_bytes(); rows = queue.batch(10)
                if fault == 'save':
                    failure = patch.object(store, 'save', side_effect=OSError('synthetic denied'))
                else:
                    failure = patch.object(store, 'load', side_effect=lambda **kw: OLD if kw.get('allow_expired') else None)
                with failure, self.assertRaises((OSError, ProtocolError)):
                    client.redeem()
                self.assertEqual(pending.path.read_bytes(), claim)
                self.assertEqual(queue.batch(10), rows)
                self.assertEqual(client.unsaved_result, NEW)
                # Fresh stores model restart, including save-success/readback-failure.
                result = BrowserRecovery(BrowserPendingStore(pending.path), CredentialStore(store.path), http).redeem()
                self.assertEqual(result, NEW)
                self.assertEqual(store.load(), NEW)
                self.assertIsNone(pending.load())
                self.assertEqual(queue.batch(10), rows)

    @unittest.skipUnless(os.environ.get('SPHOL_SERVER_PATH') or os.environ.get('SPHOL_TEST_SERVER_SOURCE'), 'Requires private HTTP fixture')
    def test_real_http_renewal_lost_response_restart(self):
        source = Path(os.environ.get('SPHOL_SERVER_PATH') or os.environ['SPHOL_TEST_SERVER_SOURCE'])
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'SPHOL_CLIENT_PATH': str(Path(__file__).resolve().parents[1]), 'EVE_DASHBOARD_NO_COLLECTOR': '1'}))
            sys.path[:0] = [str(source), str(source/'tests')]
            try:
                from test_collector_browser_recovery import BrowserRecoveryHTTPTests
                import collector_api
                import eve_sso
                import server
            finally:
                del sys.path[:2]
            fixture = BrowserRecoveryHTTPTests(methodName='runTest')
            fixture.setUp()
            def cleanup():
                fixture.doCleanups(); fixture.tearDown()
            stack.callback(cleanup)
            status, issued = fixture.redeem(fixture.issue('gamelogs:write'))
            self.assertEqual(status, 200)
            old = dict(issued, expires_at=OLD['expires_at'])
            store, pending, queue = self.state(stack, old)
            rows = queue.batch(10)
            with fixture.api.transaction() as con:
                con.execute('UPDATE collector_installations SET expires=0')
            # Invoke the actual modern Tk login button against the real HTTP server.
            import tkinter as tk
            from collector.network_gui import ConnectedApp
            from tools.native_single_smoke import visible_button
            logs = queue.path.parent/'Gamelogs'; logs.mkdir()
            stack.enter_context(patch('collector.network_gui.webbrowser.open', return_value=True))
            stack.enter_context(patch('collector.network_gui.messagebox.askyesno', return_value=True))
            stack.enter_context(patch('collector.modern_ui.ModernShell.portrait_image', return_value=None))
            stack.enter_context(patch('collector.connection_status.probe', return_value=None))
            stack.enter_context(patch('collector.connection_status.delivery_probe', side_effect=OSError('synthetic offline')))
            window = tk.Tk()
            app = ConnectedApp(window, logs, queue)
            def close_gui():
                for checker in (app.connection_status, app.delivery_check):
                    if hasattr(checker, 'shutdown'): checker.shutdown(timeout=.5)
                app.expanded.close(); app.legacy.close()
                for callback in window.tk.call('after', 'info'): window.after_cancel(callback)
                window.destroy(); gc.collect()
            stack.callback(close_gui)
            window.update()
            client = app.browser_recovery
            with patch.object(server, 'SSO_CLIENT_ID', 'client-id'), patch.object(server, 'SSO_CLIENT_SECRET', 'secret'):
                visible_button(window, 'Войти через EVE').invoke()
                deadline = time.monotonic()+5
                while app.busy and time.monotonic() < deadline:
                    window.update(); time.sleep(.01)
                self.assertFalse(app.busy)
                self.assertTrue(app.browser_outstanding())
                app.browser_paused = True
                uri = pending.load()['browser_uri']
                self.assertIsNone(app.tailer); self.assertFalse(app.upload_enabled)
                claim = pending.path.read_bytes()
                self.assertIsNone(client.redeem())
                state = parse_qs(urlparse(uri).query)['state'][0]
                identity = eve_sso.Identity(42, 'Test Pilot', (), 9999999999, 'synthetic')
                with patch.object(server.eve_sso, 'exchange_code', return_value=identity):
                    self.assertEqual(fixture.callback(state)[1], '/collector/eve-complete?status=confirm')
                self.assertIsNone(client.redeem())
                self.assertEqual(fixture.confirm(fixture.confirm_cookie), '/collector/eve-complete?status=ok')
            original = server.Handler.send_json
            committed = []
            def lose(handler, status, payload):
                if handler.path.endswith('/pairings/token') and status == 200:
                    committed.append(payload)
                    handler.close_connection = True
                    return False
                return original(handler, status, payload)
            with patch.object(server.Handler, 'send_json', lose):
                with self.assertRaises(http.client.RemoteDisconnected): client.redeem()
            self.assertEqual(len(committed), 1)
            self.assertEqual(pending.path.read_bytes(), claim)
            self.assertEqual(store.load_proof(), old)
            fixture.stop_http()
            # Private fixture code stays in the private server repository. Restart
            # the HTTP authority in a NEW interpreter over the same durable DB/key.
            import json
            ready = Path(fixture.store.path).parent / 'restart-ready.json'
            restarted = subprocess.Popen([sys.executable, str(source/'tests'/'renewal_restart_http_fixture.py'),
                str(fixture.store.path), str(ready)], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            def stop_restarted():
                restarted.communicate(input=b'\n', timeout=35)
                self.assertEqual(restarted.returncode, 0)
            stack.callback(stop_restarted)
            deadline = time.monotonic()+10
            while not ready.exists() and restarted.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(ready.exists(), 'Fresh server did not become ready')
            endpoint = json.loads(ready.read_text())
            self.assertEqual(endpoint['pid'], restarted.pid)
            self.assertNotEqual(endpoint['pid'], os.getpid())
            fixture.httpd.server_port = endpoint['port']
            # Fresh client interpreter: no cached credential, proof or service object.
            script = '''
import http.client, json, os, sys
from pathlib import Path
from collector.browser_recovery import BrowserRecovery, BrowserPendingStore
from collector.credentials import CredentialStore
if os.name != 'nt':
    import collector.credentials, collector.browser_recovery
    collector.credentials.crypt = collector.browser_recovery.crypt = lambda raw, **kw: raw
class Loopback:
    def post(self, path, body):
        con = http.client.HTTPConnection('127.0.0.1', int(sys.argv[3]), timeout=5)
        try:
            con.request('POST', path, json.dumps(body), {'Content-Type':'application/json'})
            res = con.getresponse()
            return res.status, json.loads(res.read())
        finally:
            con.close()
client = BrowserRecovery(BrowserPendingStore(Path(sys.argv[1])), CredentialStore(Path(sys.argv[2])), Loopback())
assert client.redeem() is not None
print('renewal-readback-ok')
'''
            child = subprocess.run([sys.executable, '-c', script, str(pending.path), str(store.path), str(fixture.httpd.server_port)],
                capture_output=True, text=True, timeout=15, cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(child.returncode, 0, 'Fresh client renewal failed (output withheld)')
            self.assertEqual(child.stdout.strip(), 'renewal-readback-ok')
            recovered = store.load()
            self.assertEqual(recovered, committed[0])
            self.assertEqual(recovered['installation_id'], old['installation_id'])
            self.assertEqual(recovered['scope'], old['scope'])
            self.assertEqual(recovered['characters'], old['characters'])
            self.assertEqual(store.load(), recovered)
            self.assertIsNone(pending.load())
            app.browser_result('browser_token', recovered, None)
            self.assertIsNone(app.tailer); self.assertFalse(app.upload_enabled)
            self.assertEqual(queue.batch(10), rows)
            self.assertEqual(fixture.request('connection', auth=False, token=recovered['access_token'])[0], 200)
            self.assertEqual(fixture.request('connection', auth=False, token=old['access_token'])[0], 401)
            with fixture.api.transaction() as con:
                self.assertEqual(con.execute('SELECT count(*) FROM collector_installations').fetchone()[0], 1)
