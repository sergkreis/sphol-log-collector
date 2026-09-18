"""Synthetic recovery gates; no production egress or substitute DPAPI claims."""
import gc
import http.client
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch
from collector import diagnostics
from collector.site_code import PendingStore, Redemption
from collector.site_code_gui import SiteCodeControls
from collector.transport import SiteCodeFailure, ProtocolError
from test_site_code import Memory, CODE, CREDENTIAL


class RecoveryTests(unittest.TestCase):
    def ui(self, directory):
        import tkinter as tk
        root = tk.Tk()
        self.addCleanup(gc.collect)
        self.addCleanup(root.destroy)
        app = Mock(window=root, network_area=root, settings=root, store=Memory(), busy=False,
                   pairing=None, tailer=None, uploader=None, recovered_token=None, updates=None)
        app.queue.path = Path(directory) / 'queue.sqlite3'
        with patch.object(root, 'after'):
            ui = SiteCodeControls(app)
        return ui

    def test_archive_ciphertext_bounded_idempotent_and_binding_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            pending = PendingStore(Path(d) / 'pending.dpapi')
            # An opaque synthetic ciphertext fixture, not encryption emulation.
            raw = b'opaque-ciphertext-fixture'
            pending.path.write_bytes(raw)
            with patch.object(pending, 'clear', side_effect=OSError()):
                with self.assertRaises(OSError): pending.archive()
            self.assertTrue(pending.path.exists())
            pending.archive()
            self.assertFalse(pending.path.exists())
            archive = pending.path.parent / 'site-recovery-archive'
            self.assertEqual(len(list(archive.iterdir())), 1)
            self.assertEqual((archive / 'recovery-00.dpapi').read_bytes(), raw)
            for i in range(16):
                (archive / ('recovery-%02d.dpapi' % i)).write_bytes(bytes([i]))
            pending.path.write_bytes(raw)
            with self.assertRaises(ProtocolError): pending.archive()
            self.assertEqual(pending.path.read_bytes(), raw)

    def test_terminal_restart_review_and_confirmed_abandon(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'site-redemption.dpapi').write_bytes(b'opaque-ciphertext-fixture')
            ui = self.ui(d)
            self.assertTrue(ui.uncertain)
            ui.attempts = 1
            ui.results.put((None, SiteCodeFailure(404, 'not_found')))
            with patch.object(ui.app.window, 'after'): ui.tick()
            self.assertTrue(ui.uncertain)
            self.assertFalse(ui.auto_retry)
            self.assertTrue(ui.legacy_button.instate(['disabled']))
            with patch('collector.site_code_gui.messagebox.askyesno', return_value=False):
                ui.abandon_button.invoke()
            self.assertTrue(ui.pending.path.exists())
            sentinel = dict(CREDENTIAL)
            ui.app.store.save(sentinel)
            queue_path = Path(d, 'queue.sqlite3')
            queue_path.write_bytes(b'queue-sentinel')
            with patch('collector.site_code_gui.messagebox.askyesno', return_value=True):
                ui.abandon_button.invoke()
            self.assertFalse(ui.uncertain)
            self.assertTrue(ui.legacy_button.instate(['disabled']))
            self.assertEqual(ui.app.uploader.credentials, sentinel)
            self.assertEqual(ui.app.store.load(), sentinel)
            self.assertEqual(queue_path.read_bytes(), b'queue-sentinel')
            self.assertEqual(Path(d, 'site-recovery-archive/recovery-00.dpapi').read_bytes(), b'opaque-ciphertext-fixture')

    def test_reviewed_abandon_unlocks_unbound_legacy(self):
        with tempfile.TemporaryDirectory() as d:
            ui = self.ui(d)
            ui.pending.path.write_bytes(b'opaque-ciphertext-fixture')
            ui.uncertain = True
            with patch('collector.site_code_gui.messagebox.askyesno', return_value=True):
                ui.abandon()
            self.assertFalse(ui.uncertain)
            self.assertTrue(ui.legacy_button.instate(['!disabled']))
            self.assertIsNone(ui.app.store.load())

    def test_busy_abandon_conflicts_late_ack_no_capture(self):
        with tempfile.TemporaryDirectory() as d:
            ui = self.ui(d)
            ui.uncertain = ui.busy = True
            ui.deadline = 0
            with patch('collector.site_code_gui.messagebox.askyesno') as confirm:
                ui.abandon()
                confirm.assert_not_called()
            ui.results.put((CREDENTIAL, None))
            with patch.object(ui.app.window, 'after'): ui.tick()
            self.assertFalse(ui.uncertain)
            self.assertFalse(ui.app.upload_enabled)
            self.assertEqual(ui.app.uploader.credentials, CREDENTIAL)

    def test_retry_after_bounded_manual_available_expired_uncertain(self):
        with tempfile.TemporaryDirectory() as d:
            ui = self.ui(d)
            ui.uncertain = True
            for attempt in (1, 2, 3):
                ui.attempts = attempt
                ui.results.put((None, SiteCodeFailure(429, 'slow_down', 180)))
                with patch('collector.site_code_gui.time.monotonic', return_value=100), patch.object(ui.app.window, 'after'):
                    ui.tick()
                self.assertEqual(ui.next_try, 280)
                self.assertEqual(ui.auto_retry, attempt < 3)
            self.assertTrue(ui.uncertain)
            ui.results.put((None, SiteCodeFailure(410, 'expired_token')))
            with patch.object(ui.app.window, 'after'): ui.tick()
            self.assertIn('уже могло', ui.label.cget('text'))
            self.assertFalse(ui.auto_retry)
            self.assertTrue(ui.uncertain)

    def test_actual_diagnostic_enum_logged_and_reloaded_no_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            sink = diagnostics.Diagnostics(Path(d))
            with patch.object(diagnostics, '_sink', sink):
                diagnostics.emit('site.redeem', 'error', error=SiteCodeFailure(410, 'expired_token'))
                diagnostics.emit('site.redeem', 'error', error=SiteCodeFailure(400, CODE), body=CODE, verifier='secret')
            restored = diagnostics.Diagnostics(Path(d))
            self.assertEqual(restored.events[0]['site_error'], 'expired_token')
            self.assertNotIn('site_error', restored.events[1])
            self.assertNotIn(CODE, sink.path.read_text())
            self.assertNotIn('secret', sink.path.read_text())

    def test_loopback_lost_response_restart_exact_proof(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                requests.append(self.rfile.read(int(self.headers['Content-Length'])))
                if len(requests) == 1:
                    self.close_connection = True  # Synthetic claim committed, response lost.
                    return
                raw = json.dumps(CREDENTIAL).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        class Loopback:
            def post(self, route, payload):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
                try:
                    connection.request('POST', route, json.dumps(payload))
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally: connection.close()
        try:
            pending, credentials = Memory(), Memory()
            first = Redemption(pending, credentials, Loopback())
            first.prepare(CODE, 'combat:write')
            with self.assertRaises(http.client.RemoteDisconnected): first.redeem()
            second = Redemption(pending, credentials, Loopback())
            second.prepare('different', 'gamelogs:write')
            self.assertEqual(second.redeem(), CREDENTIAL)
            self.assertEqual(requests[0], requests[1])
            self.assertIsNone(pending.load())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)
