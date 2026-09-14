import threading
import time
import unittest
from unittest.mock import patch, MagicMock
from collector.connection_status import ConnectionStatus, probe
from collector.transport import HTTPFailure, ProtocolError, encode

CREDS = {'access_token': 's' * 43, 'token_type': 'Bearer', 'installation_id': 'synthetic',
         'scope': 'gamelogs:write', 'expires_at': '2099-01-01T00:00:00Z',
         'characters': [{'id': 42, 'name': 'Synthetic Pilot'}]}

class ConnectionTests(unittest.TestCase):
    def settle(self, model):
        model.tick(CREDS, True)
        model.worker.join(1)
        return model.tick(CREDS, True)

    def test_fresh_only_stop_and_stale(self):
        now = [100.0]
        model = ConnectionStatus(check=lambda _: None, clock=lambda: now[0])
        self.assertEqual(model.tick(CREDS, False), 'stopped')
        self.assertEqual(self.settle(model), 'connected')
        now[0] += 46
        model.next_try = 1000
        self.assertEqual(model.tick(CREDS, True), 'unknown')
        self.assertEqual(model.tick(CREDS, False), 'stopped')

    def test_revoke_offline_unknown_backoff(self):
        for error, expected in [(HTTPFailure(401), 'denied'), (HTTPFailure(403), 'denied'),
                                (HTTPFailure(404), 'unknown'), (TimeoutError(), 'offline')]:
            def check(_):
                raise error
            model = ConnectionStatus(check=check)
            self.assertEqual(self.settle(model), expected)
            worker = model.worker
            model.tick(CREDS, True)
            self.assertIs(model.worker, worker)

    def test_timeout_nonblocking_late_response_and_stop(self):
        event = threading.Event()
        now = [100.0]
        model = ConnectionStatus(check=lambda _: event.wait(2), clock=lambda: now[0])
        before = time.monotonic()
        model.tick(CREDS, True)
        self.assertLess(time.monotonic() - before, .2)
        now[0] += 7
        self.assertEqual(model.tick(CREDS, True), 'offline')
        event.set(); model.worker.join(1)
        self.assertEqual(model.tick(CREDS, True), 'offline')
        model.tick(CREDS, False)
        self.assertNotEqual(model.state, 'connected')

    def test_response_validates_binding_scope_expiry(self):
        data = {k: v for k, v in CREDS.items() if k not in ('access_token', 'token_type')}
        con = MagicMock(); response = con.getresponse.return_value
        response.status = 200
        response.getheader.return_value = 'application/json'
        with patch('collector.connection_status.http.client.HTTPSConnection', return_value=con):
            response.read.return_value = encode(data)
            probe(CREDS)
            self.assertEqual(con.request.call_args.args[:2], ('GET', '/api/collector/v1/connection'))
            for key, value in [('installation_id', 'other'), ('scope', 'combat:write'),
                               ('characters', [{'id': 99, 'name': 'Other'}]), ('expires_at', '2000-01-01T00:00:00Z')]:
                response.read.return_value = encode({**data, key: value})
                with self.assertRaises(ProtocolError):
                    probe(CREDS)
            response.status = 401
            with self.assertRaises(HTTPFailure):
                probe(CREDS)
