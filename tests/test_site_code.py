import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from collector.site_code import Redemption, PendingStore, ROUTE
from collector.credentials import CredentialStore
from collector.transport import HTTPS, SiteCodeFailure, HTTPFailure, ProtocolError, encode
from collector import diagnostics

CODE = 'ABCD-EFGH-JKLM-NPQR-STUV'
CREDENTIAL = dict(access_token='a'*43, token_type='Bearer', scope='combat:write', installation_id='a'*32,
                  expires_at='2099-01-01T00:00:00Z', characters=[dict(id=42, name='Synthetic Pilot')])

class Memory:
    """Explicit in-memory test double; never substitutes for DPAPI tests."""
    def __init__(self): self.value = None
    def load(self): return copy.deepcopy(self.value)
    def save(self, value): self.value = copy.deepcopy(value)
    def clear(self): self.value = None

class SiteTests(unittest.TestCase):
    def test_native_button_synthetic(self):
        import tkinter as tk
        import gc
        from collector.site_code_gui import SiteCodeControls
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest('Native Tk display unavailable')
        try:
            with tempfile.TemporaryDirectory() as d, patch.object(root, 'after'), patch('collector.site_code_gui.threading.Thread'):
                app = Mock(window=root, network_area=root, settings=root, store=Memory(), busy=False, pairing=None,
                           tailer=None, uploader=None, recovered_token=None, updates=None)
                app.queue.path = Path(d)/'queue.sqlite3'
                ui = SiteCodeControls(app)
                ui.redemption = Mock()
                ui.code.set(CODE)
                ui.button.invoke()
                self.assertTrue(ui.busy)
                self.assertTrue(ui.button.instate(['disabled']))
                self.assertEqual(ui.code.get(), '')
                ui.redemption.prepare.assert_called_once_with(CODE, 'combat:write')
        finally:
            root.destroy()
            gc.collect()

    def test_lost_response_restart_exact_payload(self):
        pending, store = Memory(), Memory()
        http = Mock()
        http.post.side_effect = [TimeoutError(), (200, CREDENTIAL)]
        first = Redemption(pending, store, http)
        payload = first.prepare(CODE, 'combat:write')
        self.assertEqual(len(payload['verifier']), 43)
        with self.assertRaises(TimeoutError): first.redeem()
        second = Redemption(pending, store, http)
        self.assertEqual(second.prepare('OTHER', 'gamelogs:write'), payload)
        self.assertEqual(second.redeem(), CREDENTIAL)
        self.assertEqual(http.post.call_args_list[0], http.post.call_args_list[1])
        self.assertIsNone(pending.load())
        self.assertEqual(store.load(), CREDENTIAL)

    def test_no_overwrite_and_scope_mismatch(self):
        for existing, response in [(dict(CREDENTIAL, installation_id='b'*32), CREDENTIAL), (None, dict(CREDENTIAL, scope='gamelogs:write'))]:
            pending, store = Memory(), Memory()
            store.value = existing
            r = Redemption(pending, store, Mock(post=Mock(return_value=(200, response))))
            r.prepare(CODE, 'combat:write')
            with self.assertRaises(ProtocolError): r.redeem()
            self.assertEqual(store.load(), existing)
            self.assertIsNotNone(pending.load())

    def test_persistence_failure_prevents_request(self):
        pending = Memory()
        pending.save = Mock(side_effect=OSError())
        http = Mock()
        with self.assertRaises(OSError): Redemption(pending, Memory(), http).prepare(CODE, 'combat:write')
        http.post.assert_not_called()

    def test_commit_readback_failure_retains_proof(self):
        pending, store = Memory(), Memory()
        store.save = Mock()
        r = Redemption(pending, store, Mock(post=Mock(return_value=(200, CREDENTIAL))))
        r.prepare(CODE, 'combat:write')
        with self.assertRaises(ProtocolError): r.redeem()
        self.assertIsNotNone(pending.load())

    def test_http_typed_route_only_and_privacy(self):
        with tempfile.TemporaryDirectory() as d:
            sink = diagnostics.Diagnostics(Path(d))
            response = Mock(status=429)
            response.read.return_value = encode({'error': 'slow_down'})
            response.getheader.side_effect = lambda k, default='': {'Content-Type':'application/json','Retry-After':'9'}.get(k,default)
            connection = Mock()
            connection.getresponse.return_value = response
            with patch('collector.transport.http.client.HTTPSConnection', return_value=connection), patch.object(diagnostics, '_sink', sink):
                with self.assertRaises(SiteCodeFailure) as cm: HTTPS().post(ROUTE, {'secret': CODE})
                self.assertEqual(cm.exception.code, 'slow_down')
                for code in ('authority_busy', 'authority_unavailable'):
                    response.status = 503
                    response.read.return_value = encode({'error': code})
                    with self.assertRaises(SiteCodeFailure) as temporary:
                        HTTPS().post(ROUTE, {'secret': CODE})
                    self.assertEqual(temporary.exception.code, code)
                    self.assertEqual(temporary.exception.retry_after, 9)
                with self.assertRaises(HTTPFailure) as cm: HTTPS().post('/api/collector/v1/events', {})
                self.assertNotIsInstance(cm.exception, SiteCodeFailure)
            text = str(sink.events)
            self.assertNotIn(CODE, text)
            self.assertIn('http.connect_tls', text)
            self.assertIn('duration_ms', text)

    def test_missing_credentials_not_game_log_error(self):
        with tempfile.TemporaryDirectory() as d:
            sink = diagnostics.Diagnostics(Path(d)/'history')
            with patch.object(diagnostics, '_sink', sink):
                self.assertIsNone(CredentialStore(Path(d)/'absent').load())
            self.assertEqual(sink.events[-1]['outcome'], 'missing')
            self.assertFalse(any(e['event']=='logs.open' for e in sink.events))

    @unittest.skipUnless(os.name == 'nt', 'Requires actual Windows DPAPI')
    def test_actual_dpapi_restart_and_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'pending.dpapi'
            pending = PendingStore(path)
            r = Redemption(pending, CredentialStore(Path(d)/'credential.dpapi'))
            payload = r.prepare(CODE, 'combat:write')
            self.assertNotIn(CODE.encode(), path.read_bytes())
            self.assertNotIn(payload['verifier'].encode(), path.read_bytes())
            self.assertEqual(PendingStore(path).load(), payload)
            path.write_bytes(b'corrupt')
            with self.assertRaises(OSError): PendingStore(path).load()

    def test_real_gui_callback_synthetic(self):
        from collector.site_code_gui import SiteCodeControls
        ui = SiteCodeControls.__new__(SiteCodeControls)
        ui.app = Mock(busy=False, pairing=None, tailer=None, uploader=None, recovered_token=None, updates=None)
        ui.busy = ui.uncertain = False
        ui.next_try = 0
        ui.label = Mock()
        ui.code = Mock(get=Mock(return_value=CODE))
        ui.scope = Mock(get=Mock(return_value='combat:write'))
        ui.redemption = Mock()
        ui.refresh = Mock()
        with patch('collector.site_code_gui.threading.Thread') as thread:
            ui.submit()
            self.assertTrue(ui.busy)
            self.assertTrue(ui.uncertain)
            ui.redemption.prepare.assert_called_once_with(CODE, 'combat:write')
            thread.return_value.start.assert_called_once()
            ui.submit()
            self.assertEqual(ui.redemption.prepare.call_count, 1)
            self.assertIn('SPHOL', ui.label.config.call_args.kwargs['text'])
