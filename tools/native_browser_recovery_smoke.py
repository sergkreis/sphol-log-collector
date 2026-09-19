"""Actual Tk primary + isolated private HTTP; no private source vendored."""
import gc
import os
import socket
import sys
import time
import tempfile
import unittest
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
from collector.browser_recovery import BrowserPendingStore


class NativeBrowserRecovery(unittest.TestCase):
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
                        if handler.path == '/api/collector/v1/connection':
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
                        app.browser_button.invoke()
                        pump(lambda: opened.call_count >= 2)
                        self.assertEqual(fixture.request('approval', {'user_code': uri.split('#')[1], 'consent': True})[0], 200)
                        pump(lambda: app.uploader is not None and app.connection_status.state == 'connected')
                        self.assertIn(('/api/collector/v1/connection', 200, fixture.httpd.server_port), observed)
                        self.assertTrue(destinations)
                        self.assertTrue(all(a == ('127.0.0.1', fixture.httpd.server_port) for a in destinations))
                        print('PROVENANCE: actual /api/collector/v1/connection HTTP 200; all socket connects isolated loopback')
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
