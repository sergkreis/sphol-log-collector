"""Actual Tk primary + isolated private HTTP; no private source vendored."""
import gc
import os
import socket
import sys
import time
import tempfile
import urllib.parse
import unittest
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
from collector.browser_recovery import BrowserPendingStore


class NativeBrowserRecovery(unittest.TestCase):
    OFFICIAL_EVE_URL = 'https://login.eveonline.com/v2/oauth/authorize?response_type=code&client_id=client&redirect_uri=https%3A%2F%2Fsphol.com%2Fsso%2Feve%2Fcallback&state=collector_' + 'C'*32

    def test_visible_open_eve_button_fresh_waiting_and_legacy_pending(self):
        import tkinter as tk
        from collector.network_gui import ConnectedApp
        from collector.core import PendingQueue
        from collector.pairing_ux import active_url
        from tools.native_single_smoke import visible_button
        with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
            if os.name != 'nt':
                stack.enter_context(patch('collector.browser_recovery.crypt', side_effect=lambda data, **kw: data))
                stack.enter_context(patch('collector.credentials.crypt', side_effect=lambda data, **kw: data))
            stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')))
            stack.enter_context(patch('collector.network_gui.messagebox.askyesno', return_value=True))
            opened = stack.enter_context(patch('collector.pairing_ux.webbrowser.open', return_value=True))
            root = Path(d); logs = root/'Gamelogs'; logs.mkdir()
            q = PendingQueue(root/'queue.sqlite3'); window = tk.Tk(); app = None
            def pump(predicate):
                until = time.monotonic()+5
                while not predicate() and time.monotonic()<until:
                    window.update(); time.sleep(.01)
                self.assertTrue(predicate())
            def destroy():
                app.expanded.close(); app.legacy.close()
                for callback in window.tk.call('after', 'info'): window.after_cancel(callback)
                window.destroy(); gc.collect()
            try:
                app = ConnectedApp(window, logs, q); window.update()
                before = q.count()
                def fake_start(scope):
                    app.browser_pending.save(dict(device_secret='a'*43, verifier='b'*43, scope=scope, recovery=True,
                        browser_proof='c'*43, approval_expires=time.time()+120, browser_uri=self.OFFICIAL_EVE_URL))
                    return self.OFFICIAL_EVE_URL
                app.browser_recovery.start = fake_start
                visible_button(window, 'Войти через EVE').invoke()
                pump(lambda: opened.call_count == 1 and active_url(app.pairing) == self.OFFICIAL_EVE_URL and not app.busy and app.browser_job is None)
                self.assertEqual(q.count(), before)
                self.assertIsNone(app.tailer)
                self.assertFalse(app.upload_enabled)
                self.assertEqual(opened.call_args.args[0], self.OFFICIAL_EVE_URL)
                visible_button(window, 'Открыть EVE').invoke()
                pump(lambda: opened.call_count == 2 and app.browser_job is None)
                self.assertEqual(opened.call_args.args[0], self.OFFICIAL_EVE_URL)
                saved = app.browser_pending.load(); saved.pop('browser_uri'); saved['approval_expires'] = time.time()-60
                app.browser_pending.clear(); app.browser_pending.save(saved)
                recovered_url = self.OFFICIAL_EVE_URL.replace('C'*32, 'D'*32)
                def fake_recover():
                    current = app.browser_pending.load()
                    app.browser_pending.save(dict(current, browser_uri=recovered_url, approval_expires=time.time()+120))
                    return recovered_url
                app.browser_recovery.recover_url = fake_recover
                app.restore_browser(); window.update()
                visible_button(window, 'Открыть EVE').invoke()
                pump(lambda: opened.call_count == 3 and app.browser_job is None)
                self.assertEqual(opened.call_args.args[0], recovered_url)
                self.assertEqual(q.count(), before)
            finally:
                if app: destroy()
                q.close()

    @unittest.skipUnless(os.name == 'nt', 'Native Windows DPAPI required')
    def test_native_dpapi_separate_browser_proof(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'pending.dpapi'
            claim = dict(device_secret='a'*43, verifier='b'*43, scope='combat:write', recovery=True,
                         browser_proof='c'*43, approval_expires=time.time()+300)
            BrowserPendingStore(path).save(claim)
            self.assertEqual(BrowserPendingStore(path).load(), claim)
            for key in ('device_secret', 'verifier', 'browser_proof'):
                self.assertNotIn(claim[key].encode(), path.read_bytes())

    @unittest.skipUnless(os.environ.get('SPHOL_TEST_SERVER_SOURCE'), 'Private isolated HTTP fixture required')
    def test_primary_restart_before_approval_real_http_probe(self):
        import tkinter as tk
        from collector.network_gui import ConnectedApp
        from collector.core import PendingQueue
        from collector.pairing_ux import active_url
        source = Path(os.environ['SPHOL_TEST_SERVER_SOURCE'])
        with patch.dict(os.environ, {'SPHOL_CLIENT_PATH': str(Path(__file__).resolve().parents[1]),
                                   'SPHOL_DIS_AUTOMATIC': '0', 'EVE_DASHBOARD_NO_COLLECTOR': '1'}):
            sys.path[:0] = [str(source), str(source/'tests')]
            try:
                from test_collector_site_codes import SiteCodeHTTPTests
            finally:
                del sys.path[:2]
            fixture = SiteCodeHTTPTests(methodName='runTest'); fixture.setUp()
            try:
                with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
                    import server
                    observed = []
                    destinations = []
                    real_send = server.Handler.send_json
                    real_connect = socket.socket.connect
                    def record_send(handler, status, payload):
                        if handler.path in ('/api/collector/v1/connection', '/api/collector/v1/delivery-check'):
                            observed.append((handler.path, status, handler.server.server_port))
                        return real_send(handler, status, payload)
                    def record_connect(sock, address):
                        self.assertEqual(address[0], '127.0.0.1')
                        self.assertEqual(address[1], fixture.httpd.server_port)
                        destinations.append(address)
                        return real_connect(sock, address)
                    stack.enter_context(patch.object(server.Handler, 'send_json', record_send))
                    stack.enter_context(patch.object(socket.socket, 'connect', record_connect))
                    stack.enter_context(patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('connect_ex forbidden')))
                    # Linux-only explicit synthetic disk encryption double, never native acceptance.
                    if os.name != 'nt':
                        stack.enter_context(patch('collector.browser_recovery.crypt', side_effect=lambda data, **kw: data))
                        stack.enter_context(patch('collector.credentials.crypt', side_effect=lambda data, **kw: data))
                    stack.enter_context(patch('collector.network_gui.messagebox.askyesno', return_value=True))
                    opened = stack.enter_context(patch('collector.pairing_ux.webbrowser.open', return_value=True))
                    root = Path(d); logs = root/'Gamelogs'; logs.mkdir()
                    q = PendingQueue(root/'queue.sqlite3'); window = tk.Tk(); app = None
                    def pump(predicate):
                        until = time.monotonic()+8
                        while not predicate() and time.monotonic()<until:
                            window.update(); time.sleep(.01)
                        self.assertTrue(predicate())
                    def destroy():
                        app.expanded.close(); app.legacy.close()
                        for callback in window.tk.call('after', 'info'): window.after_cancel(callback)
                        window.destroy(); gc.collect()
                    try:
                        app = ConnectedApp(window, logs, q); window.update()
                        from tools.native_single_smoke import visible_button
                        visible_button(window, 'Войти через EVE').invoke()
                        pump(lambda: app.browser_outstanding() and not app.busy)
                        uri = active_url(app.pairing)
                        self.assertTrue(uri)
                        self.assertIsNone(app.tailer)
                        saved = app.browser_pending.path.read_bytes()
                        destroy()
                        window = tk.Tk(); app = ConnectedApp(window, logs, q); window.update()
                        self.assertEqual(active_url(app.pairing), uri)
                        self.assertEqual(app.browser_pending.path.read_bytes(), saved)
                        visible_button(window, 'Открыть EVE').invoke()
                        pump(lambda: opened.call_count >= 2 and app.browser_job is None and not app.busy)
                        # Reproduce the released 0.3.11 disk state, then use the actual
                        # visible control through real isolated HTTP, not a recovery stub.
                        legacy = app.browser_pending.load()
                        legacy.pop('browser_uri')
                        legacy['approval_expires'] = time.time()-60
                        app.browser_pending.save(legacy)
                        with fixture.api.transaction() as con:
                            con.execute('UPDATE collector_pairings SET expires=0')
                        app.restore_browser(); window.update()
                        visible_button(window, 'Открыть EVE').invoke()
                        pump(lambda: opened.call_count >= 3 and app.browser_job is None and not app.busy)
                        uri = active_url(app.pairing)
                        self.assertEqual(opened.call_args.args[0], uri)
                        self.assertEqual(app.browser_pending.load()['browser_uri'], uri)
                        self.assertEqual(app.browser_pending.load()['device_secret'], legacy['device_secret'])
                        with fixture.api.transaction() as con:
                            self.assertEqual(con.execute('SELECT count(*) FROM collector_installations').fetchone()[0], 0)
                        oauth_state = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)['state'][0]
                        import eve_sso
                        fixture.api.approve_oauth(oauth_state, eve_sso.Identity(42, 'Test Pilot', (), 9999999999, 'token'), 123)
                        pump(lambda: app.uploader is not None and app.connection_status.state == 'connected'
                                      and app.delivery_check.state == 'connected')
                        self.assertIn(('/api/collector/v1/connection', 200, fixture.httpd.server_port), observed)
                        self.assertIn(('/api/collector/v1/delivery-check', 200, fixture.httpd.server_port), observed)
                        delivery_id = app.delivery_check.check_id
                        delivery_ack = app.delivery_check.ack_received_at
                        with fixture.api.transaction() as con:
                            row = con.execute('SELECT check_id, received FROM collector_delivery_checks WHERE installation_id=?',
                                              (app.uploader.credentials['installation_id'],)).fetchone()
                            self.assertEqual(row['check_id'], delivery_id)
                            self.assertEqual(__import__('collector_api').utc(row['received']), delivery_ack)
                            self.assertEqual(con.execute('SELECT count(*) FROM collector_events').fetchone()[0], 0)
                            self.assertEqual(con.execute('SELECT count(*) FROM collector_private_events').fetchone()[0], 0)
                            self.assertIsNone(con.execute('SELECT last_upload FROM collector_installations WHERE id=?',
                                                          (app.uploader.credentials['installation_id'],)).fetchone()[0])
                        # Same stable ID after a lost ACK must not create combat/private/DIS/queue state.
                        self.assertEqual(app.delivery_check.check(app.uploader.credentials, delivery_id), delivery_ack)
                        with fixture.api.transaction() as con:
                            self.assertEqual(con.execute('SELECT count(*) FROM collector_delivery_checks').fetchone()[0], 1)
                            self.assertEqual(con.execute('SELECT count(*) FROM collector_events').fetchone()[0], 0)
                            self.assertEqual(con.execute('SELECT count(*) FROM collector_private_events').fetchone()[0], 0)
                        self.assertTrue(destinations)
                        self.assertTrue(all(a == ('127.0.0.1', fixture.httpd.server_port) for a in destinations))
                        print('PROVENANCE: actual /api/collector/v1/connection and /delivery-check HTTP 200; exact committed delivery ACK; all socket connects isolated loopback')
                        self.assertFalse(app.browser_outstanding())
                        self.assertEqual(app.store.load(), app.uploader.credentials)
                        self.assertIsNone(app.tailer); self.assertFalse(app.upload_enabled)
                        app.start_button.invoke(); window.update()
                        self.assertIsNotNone(app.tailer)
                        app.stop()
                    finally:
                        if app: destroy()
                        q.close()
            finally:
                fixture.doCleanups(); fixture.tearDown()
