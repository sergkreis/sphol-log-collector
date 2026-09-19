import threading
import time
import unittest
from unittest.mock import MagicMock
from collector.connection_status import ConnectionStatus, DeliveryCheck, delivery_probe, strict_utc_timestamp
from collector.transport import HTTPFailure, ProtocolError

CREDS = {'access_token': 's' * 43, 'token_type': 'Bearer', 'installation_id': 'synthetic',
         'scope': 'gamelogs:write', 'expires_at': '2099-01-01T00:00:00Z',
         'characters': [{'id': 42, 'name': 'Synthetic Pilot'}]}

class ConnectionTests(unittest.TestCase):
    def settle(self, model):
        model.tick(CREDS, True)
        model.worker.join(1)
        return model.tick(CREDS, True)

    def test_get_freshness_ttl_scheduling_preserved(self):
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
        now[0] += 16
        self.assertEqual(model.tick(CREDS, True), 'offline')
        event.set(); model.worker.join(1)
        self.assertEqual(model.tick(CREDS, True), 'offline')
        model.tick(CREDS, False)
        self.assertNotEqual(model.state, 'connected')

class DeliveryCheckTests(unittest.TestCase):
    def settle(self, model):
        model.tick(CREDS, True)
        model.worker.join(1)
        return model.tick(CREDS, True)

    def test_delivery_check_exact_ack(self):
        http = MagicMock()
        http.post.return_value = (200, {'type': 'startup-delivery-check', 'check_id': 'a'*64, 'received_at': '2026-01-01T00:00:00Z'})
        self.assertEqual(delivery_probe(CREDS, 'a'*64, http), '2026-01-01T00:00:00Z')
        self.assertEqual(http.post.call_args.args, ('/api/collector/v1/delivery-check', {'type': 'startup-delivery-check', 'check_id': 'a'*64}, CREDS['access_token']))
        for bad in ({}, {'type': 'startup-delivery-check', 'check_id': 'b'*64, 'received_at': '2026-01-01T00:00:00Z'},
                    {'type': 'startup-delivery-check', 'check_id': 'a'*64, 'received_at': 'not-a-timestamp'},
                    {'type': 'startup-delivery-check', 'check_id': 'a'*64, 'received_at': '2026-01-01T00:00:00+00:00'},
                    {'type': 'startup-delivery-check', 'check_id': 'a'*64, 'received_at': '2035-01-01T00:00:00Z'}):
            http.post.return_value = (200, bad)
            with self.assertRaises(ProtocolError):
                delivery_probe(CREDS, 'a'*64, http)
        with self.assertRaises(ProtocolError):
            delivery_probe(CREDS, 'A'*64, http)
        http.post.return_value = (503, {'error': 'temporarily_unavailable'})
        with self.assertRaises(HTTPFailure):
            delivery_probe(CREDS, 'a'*64, http)

    def test_lost_ack_retries_same_id_indefinitely_then_stops_after_success(self):
        seen = []
        now = [100.0]
        def check(_, check_id):
            seen.append(check_id)
            if len(seen) <= 6:
                raise TimeoutError()
            return '2026-01-01T00:00:00Z'
        model = DeliveryCheck(check=check, clock=lambda: now[0])
        for _ in range(6):
            self.assertEqual(self.settle(model), 'offline')
            self.assertNotEqual(model.next_try, float('inf'))
            now[0] = model.next_try
        self.assertEqual(self.settle(model), 'connected')
        self.assertEqual(len(set(seen)), 1)
        self.assertEqual(model.ack_received_at, '2026-01-01T00:00:00Z')
        worker = model.worker
        now[0] += 10000
        self.assertEqual(model.tick(CREDS, True), 'connected')
        self.assertIs(model.worker, worker)

    def test_late_other_owner_generation_ignored(self):
        releases = []
        now = [100.0]
        def check(_, check_id):
            event = threading.Event(); releases.append((event, check_id)); event.wait(1); return check_id
        model = DeliveryCheck(check=check, clock=lambda: now[0])
        model.tick(CREDS, True)
        now[0] += 16
        model.tick(dict(CREDS, installation_id='other'), True)
        releases[0][0].set()
        time.sleep(.02)
        self.assertNotEqual(model.ack_received_at, releases[0][1])

    def test_strict_utc_timestamp_parser_bounds(self):
        self.assertEqual(strict_utc_timestamp('2026-01-01T00:00:00.123456Z', now=1767225600), 1767225600.123456)
        for value in ('', 'not-a-timestamp', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00',
                      '2023-12-31T23:59:59Z', '2035-01-01T00:00:00Z'):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                strict_utc_timestamp(value, now=1767225600)
