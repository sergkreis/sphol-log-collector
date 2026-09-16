"""Synthetic offline transport fixtures only; no requests to production."""
from contextlib import closing
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from collector import transport as t
from collector.credentials import CredentialStore, crypt
from collector.core import PendingQueue


def credentials():
    return {'access_token': 'SYNTHETIC_' * 8, 'token_type': 'Bearer', 'scope': 'combat:write',
            'installation_id': 'synthetic-installation', 'expires_at': '2099-01-01T00:00:00Z',
            'characters': [{'id': 123, 'name': 'Synthetic Pilot'}]}


def event(i=0):
    return {'id': f'{i:064x}', 'schema': 1, 'time': '2030-01-01T00:00:00+00:00', 'type': 'combat', 'listener': 'Synthetic Pilot', 'text': 'Synthetic combat'}


class TransportTests(unittest.TestCase):
    def test_strict_ack(self):
        e = event()
        good = {'accepted_ids': [e['id']], 'rejected': []}
        self.assertEqual(t.validate_ack(good, [e])[0], [e['id']])
        for bad in ({}, {'accepted_ids': [], 'rejected': []},
                    {'accepted_ids': ['x'], 'rejected': []},
                    {'accepted_ids': [e['id']] * 2, 'rejected': []},
                    {'accepted_ids': [e['id']], 'rejected': [{'id': e['id'], 'reason': 'invalid_event'}]},
                    {'accepted_ids': [None], 'rejected': []}):
            with self.subTest(bad=bad), self.assertRaises(t.ProtocolError):
                t.validate_ack(bad, [e])

    def test_bytes_and_listener_filter(self):
        q = Mock()
        events = [event(i) for i in range(100)]
        for e in events:
            e['text'] = 'Ж' * 4000
        q.batch.return_value = events
        batch = t.build_batch(q, credentials())
        self.assertLessEqual(len(t.encode(batch)), 262144)
        self.assertGreater(len(batch['events']), 1)
        self.assertLess(len(batch['events']), 100)
        q.batch.return_value = [{**event(), 'listener': 'Other Pilot'}]
        self.assertEqual(t.build_batch(q, credentials())['events'], [])

    def test_unapproved_prefix_does_not_starve_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            q = PendingQueue(Path(temp) / 'q.sqlite')
            try:
                for i in range(101):
                    e = event(i)
                    e['listener'] = 'Other Pilot' if i < 100 else 'Synthetic Pilot'
                    q.put(e['id'], {k: v for k, v in e.items() if k != 'id'})
                batch = q.batch(listeners={'Synthetic Pilot'})
                self.assertEqual([e['id'] for e in batch], [event(100)['id']])
                self.assertEqual(q.count(), 101)
            finally:
                q.close()

    def test_unknown_pending_fields_never_transmitted(self):
        q = Mock()
        q.batch.return_value = [{**event(), 'private_extra': 'synthetic-only'}]
        with self.assertRaises(t.ProtocolError):
            t.build_batch(q, credentials())

    def test_durable_queue_only_deleted_on_valid_ack(self):
        # Exit in reverse order: close SQLite before removing its directory,
        # including assertion failures. addCleanup runs too late for this scope.
        with tempfile.TemporaryDirectory() as temp, closing(PendingQueue(Path(temp) / 'q.sqlite')) as q:
            e = event()
            q.put(e['id'], {k: v for k, v in e.items() if k != 'id'})
            http = Mock()
            http.post.return_value = (200, {'accepted_ids': [], 'rejected': []})
            with self.assertRaises(t.ProtocolError):
                t.Uploader(credentials(), http).upload(q)
            self.assertEqual(q.count(), 1)
            http.post.return_value = (200, {'accepted_ids': [e['id']], 'rejected': []})
            self.assertIn('Сервер подтвердил', t.Uploader(credentials(), http).upload(q))
            self.assertEqual(q.count(), 0)

    def test_retry_and_revocation(self):
        q = Mock()
        q.batch.return_value = [event()]
        q.retry_states.return_value = {}
        for error in (OSError(), t.HTTPFailure(429, 300), t.HTTPFailure(503)):
            http = Mock()
            http.post.side_effect = error
            uploader = t.Uploader(credentials(), http)
            self.assertIn('повтор', uploader.upload(q))
            self.assertGreater(uploader.next_try, 0)
            q.acknowledge.assert_not_called()
        http.post.side_effect = t.HTTPFailure(401)
        uploader = t.Uploader(credentials(), http)
        with self.assertRaises(t.HTTPFailure):
            uploader.upload(q)
        self.assertTrue(uploader.paused)

    def test_pairing_challenge_interval_and_credentials(self):
        http = Mock()
        http.post.return_value = (201, {'device_secret': 'SYNTHETIC_' * 8, 'user_code': 'TEST-1234', 'verification_uri': t.PAIR_URI, 'expires_in': 300, 'interval': 5})
        p = t.Pairing(http)
        self.assertEqual(p.start(), 'TEST-1234')
        self.assertEqual(len(http.post.call_args.args[1]['challenge']), 43)
        self.assertIsNone(p.poll())
        self.assertEqual(http.post.call_count, 1)
        p.next_poll = 0
        http.post.return_value = (400, {'error': 'slow_down'})
        self.assertIsNone(p.poll())
        self.assertEqual(p.interval, 10)
        p.next_poll = 0
        http.post.return_value = (200, credentials())
        self.assertEqual(p.poll(), credentials())
        with self.assertRaises(t.ProtocolError):
            p.poll()

    def test_bad_binding_and_json(self):
        for key, value in (('characters', []), ('scope', 'admin'), ('access_token', 'x\r\n' * 20), ('expires_at', '2000-01-01T00:00:00Z')):
            c = credentials()
            c[key] = value
            with self.assertRaises(t.ProtocolError):
                t.validate_credentials(c)
        with self.assertRaises(t.ProtocolError):
            t.decode(b'{"x":1,"x":2}')

    def test_https_no_redirect_and_verified_tls(self):
        response = Mock(status=302)
        response.read.return_value = b''
        response.getheader.return_value = ''
        with patch.object(t.http.client, 'HTTPSConnection') as factory:
            factory.return_value.getresponse.return_value = response
            with self.assertRaises(t.HTTPFailure):
                t.HTTPS().post('/api/collector/v1/pairings', {})
            self.assertEqual(factory.call_args.args[0], 'sphol.com')
            context = factory.call_args.kwargs['context']
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, t.ssl.CERT_REQUIRED)
            factory.return_value.close.assert_called_once()

    @unittest.skipUnless(os.name == 'nt', 'Native Windows DPAPI release gate')
    def test_native_dpapi_roundtrip_and_tamper(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CredentialStore(Path(temp) / 'credential.dpapi')
            store.save(credentials())
            self.assertNotIn(credentials()['access_token'].encode(), store.path.read_bytes())
            self.assertEqual(store.load(), credentials())
            raw = bytearray(store.path.read_bytes())
            raw[-1] ^= 1
            store.path.write_bytes(raw)
            with self.assertRaises(OSError):
                store.load()
            store.clear()
            self.assertIsNone(store.load())

    @unittest.skipIf(os.name == 'nt', 'Non-Windows only')
    def test_no_plaintext_fallback(self):
        with self.assertRaises(OSError):
            crypt(b'synthetic')
