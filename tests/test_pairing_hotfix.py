"""Offline compatibility with actual server code, opt-in via local checkout."""
import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from collector.site_code import Redemption
from collector.transport import SiteCodeFailure
from test_site_code import Memory


class ProtocolTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('SPHOL_TEST_SERVER_SOURCE'), 'Requires private server checkout')
    def test_real_server_new_and_failed_pending_lost_response(self):
        sys.path.insert(0, os.environ['SPHOL_TEST_SERVER_SOURCE'])
        try:
            from collector_api import Collector, Refusal
        finally:
            sys.path.pop(0)
        import socket
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SPHOL_COLLECTOR_RECOVERY_KEY': 'ab'*32}), patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
            server = Collector(str(Path(directory)/'server.sqlite3'))
            owner = dict(characterId=42, characterName='Synthetic Pilot', corporationId=7)
            class DiskFixture:
                # Persistence fixture only; Windows DPAPI remains a native gate.
                path = Path(directory)/'synthetic-pending.json'
                def load(self):
                    return json.loads(self.path.read_text()) if self.path.exists() else None
                def save(self, value):
                    self.path.write_text(json.dumps(value))
                def clear(self):
                    self.path.unlink()
            class Transport:
                lose = False
                committed = None
                def post(self, route, payload):
                    try:
                        result = server.redeem_site_code(payload, lambda *args: None)
                    except Refusal as exc:
                        raise SiteCodeFailure(exc.status, exc.error) from None
                    self.committed = result
                    if self.lose:
                        self.lose = False
                        raise ConnectionResetError('Synthetic lost response after real commit')
                    return 200, result
            for recovery in (True, False):
                issued = server.issue_site_code(dict(consent=True, scope='gamelogs:write'), owner)
                pending, credentials, http = DiskFixture(), Memory(), Transport()
                client = Redemption(pending, credentials, http)
                original = client.prepare(issued['user_code'], 'combat:write' if recovery else 'gamelogs:write')
                if recovery:
                    with self.assertRaises(SiteCodeFailure) as raised:
                        client.redeem()
                    self.assertEqual(raised.exception.code, 'scope_consent_required')
                    self.assertEqual(pending.load(), original)
                    self.assertEqual(server.installations(42), [])
                http.lose = True
                with self.assertRaises(ConnectionResetError):
                    client.redeem(website_consent=recovery)
                committed = http.committed
                self.assertEqual(pending.load(), original)
                restarted = Redemption(DiskFixture(), credentials, http)
                self.assertEqual(restarted.redeem(website_consent=recovery), committed)
                self.assertEqual(credentials.load(), committed)
                self.assertIsNone(pending.load())
            self.assertEqual(len(server.installations(42)), 2)

    @unittest.skipUnless(os.environ.get('SPHOL_TEST_SERVER_SOURCE'), 'Requires private server checkout')
    def test_actual_http_recovery_conflict_and_existing_state(self):
        import http.client
        import secrets
        from collector.core import PendingQueue
        from collector.transport import ProtocolError, HTTPS
        source = Path(os.environ['SPHOL_TEST_SERVER_SOURCE'])
        with patch.dict(os.environ, {'SPHOL_CLIENT_PATH': str(Path(__file__).resolve().parents[1]),
                                    'EVE_DASHBOARD_NO_COLLECTOR': '1'}):
            sys.path[:0] = [str(source), str(source / 'tests')]
            try:
                from test_collector_site_codes import SiteCodeHTTPTests
                import server
            finally:
                del sys.path[:2]
            fixture = SiteCodeHTTPTests(methodName='runTest')
            fixture.setUp()
            try:
                with tempfile.TemporaryDirectory() as directory:
                    class Disk:
                        path = Path(directory) / 'pending.json'
                        def load(self):
                            return json.loads(self.path.read_text()) if self.path.exists() else None
                        def save(self, value):
                            self.path.write_text(json.dumps(value))
                        def clear(self):
                            self.path.unlink()
                    from contextlib import closing
                    with closing(PendingQueue(Path(directory) / 'queue.sqlite')) as queue:
                        queue.put('synthetic-preserved', {'listener': 'Synthetic Pilot'})
                        rows = list(queue.db.execute('SELECT * FROM pending'))
                        pending, store = Disk(), Memory()
                        body = fixture.issue('gamelogs:write')
                        client = Redemption(pending, store)
                        original = client.prepare(body['user_code'], 'combat:write')
                        before = pending.path.read_bytes()
                        with self.assertRaises(SiteCodeFailure) as refusal:
                            client.redeem()
                        self.assertEqual((refusal.exception.status, refusal.exception.code), (403, 'scope_consent_required'))
                        self.assertEqual(pending.path.read_bytes(), before)
                        send = server.Handler.send_json
                        committed = []
                        def lose(handler, status, payload):
                            if handler.path.endswith('/site-codes/redeem') and status == 200:
                                committed.append(payload)
                                handler.close_connection = True
                                return False
                            return send(handler, status, payload)
                        with patch.object(server.Handler, 'send_json', lose):
                            with self.assertRaises(http.client.RemoteDisconnected):
                                client.redeem(website_consent=True)
                        self.assertEqual(len(committed), 1)
                        self.assertEqual(pending.path.read_bytes(), before)
                        restarted = Redemption(Disk(), store)
                        self.assertEqual(restarted.redeem(website_consent=True), committed[0])
                        self.assertEqual(store.load(), committed[0])
                        self.assertIsNone(pending.load())
                        rival = dict(original, scope='gamelogs:write', verifier=secrets.token_urlsafe(32))
                        with self.assertRaises(SiteCodeFailure) as conflict:
                            HTTPS().post('/api/collector/v1/site-codes/redeem', rival)
                        self.assertEqual((conflict.exception.status, conflict.exception.code), (409, 'invalid_grant'))
                        # New website code must not replace an existing local binding.
                        fresh = fixture.issue('gamelogs:write')
                        blocked = Redemption(pending, store)
                        saved = blocked.prepare(fresh['user_code'], 'gamelogs:write')
                        with self.assertRaisesRegex(ProtocolError, 'Existing binding preserved'):
                            blocked.redeem()
                        self.assertEqual(pending.load(), saved)
                        self.assertEqual(store.load(), committed[0])
                        self.assertEqual(list(queue.db.execute('SELECT * FROM pending')), rows)
                        self.assertEqual(len(fixture.api.installations(42)), 2)
            finally:
                fixture.doCleanups()
                fixture.tearDown()


class ClipboardTests(unittest.TestCase):
    def test_real_tk_english_paste_selection_and_errors(self):
        import tkinter as tk
        from tkinter import ttk
        from collector.clipboard import bind_paste, paste
        root = tk.Tk()
        try:
            entry = ttk.Entry(root)
            entry.pack()
            bind_paste(entry)
            root.update()
            entry.focus_force()
            root.update()
            root.clipboard_clear()
            root.clipboard_append('new')
            entry.insert(0, 'old')
            entry.selection_range(0, 'end')
            entry.event_generate('<Control-KeyPress-v>')
            root.update()
            self.assertEqual(entry.get(), 'new')
            entry.selection_range(0, 'end')
            with patch.object(entry, 'clipboard_get', side_effect=tk.TclError()):
                paste(entry)
            self.assertEqual(entry.get(), 'new')
            entry.config(state='readonly')
            paste(entry)
            self.assertEqual(entry.get(), 'new')
        finally:
            root.destroy()
