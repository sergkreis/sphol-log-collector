"""Synthetic only: real loopback HTTP, temp SQLite and deterministic clocks."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from collector.core import PendingQueue, QueueFull, Tailer
from collector import transport as t
from test_transport import credentials, event


@contextmanager
def intake():
    class Handler(BaseHTTPRequestHandler):
        mode = 'ack'
        rows = {}
        attempts = []

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            assert self.path == '/api/collector/v1/events'
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            ids = [e['id'] for e in payload['events']]
            self.attempts.append(ids)
            if self.mode in ('429', '503'):
                self.send_response(int(self.mode))
                self.send_header('Retry-After', '30')
                self.end_headers()
                return
            for e in payload['events']:
                if e['id'] in self.rows:
                    assert self.rows[e['id']] == e
                self.rows[e['id']] = e
            if self.mode == 'lost':
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            data = {'accepted_ids': ids if self.mode == 'ack' else ['f' * 64], 'rejected': []}
            raw = t.encode(data)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    original = socket.socket.connect
    def local_only(sock, address):
        if address[0] != '127.0.0.1' or address[1] != server.server_port:
            raise AssertionError('External network forbidden')
        return original(sock, address)
    try:
        with patch('socket.socket.connect', local_only), patch.object(
                t.http.client, 'HTTPSConnection',
                side_effect=lambda *a, **kw: http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)):
            yield Handler
    finally:
        server.shutdown()
        worker.join(3)
        server.server_close()


def put(q, i=1, **fields):
    e = {**event(i), **fields}
    q.put(e.pop('id'), e)


class DurableDeliveryTests(unittest.TestCase):
    def test_lost_ack_reopen_same_ids_and_duplicate_safe_http(self):
        with tempfile.TemporaryDirectory() as temp, intake() as server:
            path = Path(temp) / 'pending.sqlite3'
            with closing(PendingQueue(path)) as q:
                put(q)
                original = q.batch()
                server.mode = 'lost'
                u = t.Uploader(credentials(), clock=lambda: 100, jitter=lambda: .5, wall_clock=lambda: 1000)
                self.assertIn('повтор', u.upload(q))
                self.assertEqual(q.batch(), original)
                self.assertEqual(u.next_try, 101.5)
            with closing(PendingQueue(path)) as q:
                self.assertEqual(q.batch(), original)
                server.mode = 'ack'
                t.Uploader(credentials(), wall_clock=lambda: 1100).upload(q)
                self.assertEqual(q.count(), 0)
            with closing(PendingQueue(path)) as q:
                self.assertEqual(q.count(), 0)
            self.assertEqual(len(server.rows), 1)
            self.assertEqual(server.attempts[0], server.attempts[1])

    def test_wrong_ack_and_429_503_retain_exact_queue(self):
        with tempfile.TemporaryDirectory() as temp, intake() as server:
            with closing(PendingQueue(Path(temp) / 'q')) as q:
                put(q)
                before = q.batch()
                for index, mode in enumerate(('429', '503', 'wrong')):
                    server.mode = mode
                    u = t.Uploader(credentials(), clock=lambda: 100, jitter=lambda: .5, wall_clock=lambda: 1000 + index * 100)
                    if mode == 'wrong':
                        with self.assertRaises(t.ProtocolError):
                            u.upload(q)
                    else:
                        u.upload(q)
                        self.assertEqual(u.next_try, 131.5)
                        self.assertIsNone(u.upload(q))
                        self.assertFalse(u.paused)
                    self.assertEqual(q.batch(), before)

    def test_coalesce_count_bytes_and_clock(self):
        with tempfile.TemporaryDirectory() as temp, closing(PendingQueue(Path(temp) / 'q')) as q:
            now = [10.0]
            u = t.Uploader(credentials(), interval=8, count_limit=3, clock=lambda: now[0], jitter=lambda: .5)
            put(q)
            self.assertFalse(u.ready(q))
            now[0] = 16.4
            self.assertFalse(u.ready(q))
            now[0] = 16.5
            self.assertTrue(u.ready(q))
            u.flush_at = None
            put(q, 2)
            self.assertFalse(u.ready(q))
            put(q, 3)
            self.assertTrue(u.ready(q))
            u = t.Uploader(credentials(), byte_limit=16384)
            q.clear()
            for i in range(3):
                put(q, i, text='Ж' * 4000)
            self.assertTrue(u.ready(q))
            batch = t.build_batch(q, credentials(), byte_limit=16384)
            self.assertLessEqual(len(t.encode(batch)), 16384)
            self.assertEqual(len(batch['events']), 2)

    def test_retry_after_dates_and_jitter_spread(self):
        self.assertEqual(t.retry_after('Thu, 01 Jan 1970 00:01:00 GMT', now=30), 30)
        self.assertEqual(t.retry_after('300'), 300)
        for value in ('nonsense', '-2', ''):
            self.assertEqual(t.retry_after(value), 0)
        with tempfile.TemporaryDirectory() as temp, closing(PendingQueue(Path(temp) / 'q')) as q:
            put(q)
            from unittest.mock import Mock
            http = Mock()
            http.post.side_effect = t.HTTPFailure(503, 30)
            times = []
            for jitter in (0, .5, 1):
                u = t.Uploader(credentials(), http, clock=lambda: 10, jitter=lambda: jitter, wall_clock=lambda: 1000 + jitter * 1000)
                u.upload(q)
                times.append(u.next_try)
            self.assertEqual(times, [40, 41.5, 43])

    def test_stable_034_queue_and_credential_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'pending.sqlite3'
            sentinel = Path(temp) / 'credentials.dpapi'
            sentinel.write_bytes(b'SYNTHETIC ENCRYPTED CREDENTIAL SENTINEL')
            e = event(17)
            identity = e.pop('id')
            payload = json.dumps(e, ensure_ascii=False, separators=(',', ':'))
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE pending (id TEXT PRIMARY KEY, payload TEXT NOT NULL, size INTEGER NOT NULL)')
                db.execute('INSERT INTO pending VALUES (?,?,?)', (identity, payload, len(payload.encode())))
                db.commit()
            with closing(PendingQueue(path)) as q:
                self.assertEqual(q.batch(), [{**e, 'id': identity}])
                self.assertEqual(q.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(sentinel.read_bytes(), b'SYNTHETIC ENCRYPTED CREDENTIAL SENTINEL')

    def test_ack_disk_failure_rolls_back_and_reopens(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'q'
            with closing(PendingQueue(path)) as q:
                put(q)
                q.db.execute("CREATE TRIGGER deny_delete BEFORE DELETE ON pending BEGIN SELECT RAISE(ABORT, 'synthetic full disk'); END")
                with self.assertRaises(sqlite3.DatabaseError):
                    q.acknowledge([event(1)['id']])
            with closing(PendingQueue(path)) as q:
                self.assertEqual(q.batch()[0]['id'], event(1)['id'])

    def test_same_installation_never_overlaps_stream_requests(self):
        from unittest.mock import Mock
        from collector.network_gui import Snapshot
        http = Mock()
        http.post.return_value = (200, {'accepted_ids': [event(1)['id']], 'rejected': []})
        first = t.Uploader(credentials(), http)
        second = t.Uploader(credentials(), http)
        self.assertIs(first.request_lock, second.request_lock)
        snapshot = Snapshot([event(1)])
        with first.request_lock:
            self.assertIsNone(second.upload(snapshot))
            http.post.assert_not_called()
        second.upload(snapshot)
        self.assertEqual(snapshot.accepted, [event(1)['id']])

    def test_retry_persists_through_snapshot_and_reopen(self):
        from unittest.mock import Mock
        from collector.network_gui import Snapshot
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'q'
            http = Mock()
            http.post.side_effect = t.HTTPFailure(503, 30)
            with closing(PendingQueue(path)) as q:
                put(q)
                snapshot = Snapshot(q.batch(), q)
                u = t.Uploader(credentials(), http, clock=lambda: 100,
                               wall_clock=lambda: 1000, jitter=lambda: .5)
                u.upload(snapshot)
                self.assertEqual(q.retry_states(), {})
                q.complete_upload(snapshot.accepted, snapshot.retry_updates)
            with closing(PendingQueue(path)) as q:
                now = [200.0]
                u = t.Uploader(credentials(), http, clock=lambda: now[0],
                               wall_clock=lambda: 1010)
                self.assertIsNone(u.upload(q))
                self.assertEqual(u.failures, 1)
                self.assertEqual(u.next_try, 221.5)
                self.assertEqual(http.post.call_count, 1)
                now[0] = 222
                http.post.side_effect = None
                http.post.return_value = (200, {'accepted_ids': [event(1)['id']], 'rejected': []})
                u.upload(q)
                self.assertEqual(q.count(), 0)
                self.assertEqual(q.retry_states()[u.retry_key], (0, 0))

    def test_malformed_partial_ack_cannot_drain_any_other_row(self):
        from unittest.mock import Mock
        bad = [
            {'accepted_ids': [event(1)['id']], 'rejected': []},
            {'accepted_ids': [event(1)['id'], event(1)['id']], 'rejected': []},
            {'accepted_ids': [event(1)['id'], event(3)['id']], 'rejected': []},
            {'accepted_ids': [event(1)['id']], 'rejected': [{'id': event(2)['id'], 'reason': 'unknown'}]},
        ]
        with tempfile.TemporaryDirectory() as temp, closing(PendingQueue(Path(temp) / 'q')) as q:
            for i in range(1, 4):
                put(q, i)
            before = q.batch()
            for ack in bad:
                http = Mock()
                http.post.return_value = (200, ack)
                with self.assertRaises(t.ProtocolError):
                    t.Uploader(credentials(), http, count_limit=2).upload(q)
                self.assertEqual(q.batch(), before)
            http = Mock()
            http.post.return_value = (200, {'accepted_ids': [event(1)['id']],
                'rejected': [{'id': event(2)['id'], 'reason': 'invalid_event'}]})
            t.Uploader(credentials(), http, count_limit=2).upload(q)
            self.assertEqual([e['id'] for e in q.batch()], [event(2)['id'], event(3)['id']])

    def test_event_id_conflict_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp, closing(PendingQueue(Path(temp) / 'q')) as q:
            put(q)
            with self.assertRaises(ValueError):
                put(q, text='different')
            self.assertEqual(q.batch()[0]['text'], event(1)['text'])


class CaptureCheckpointTests(unittest.TestCase):
    header = b'Listener: Synthetic Pilot\nSession started: synthetic\n'
    line = b'[ 2030.01.01 00:00:01 ] (combat) synthetic\n'
    started = datetime(2030, 1, 1, tzinfo=timezone.utc)

    def test_atomic_crash_recovery_excludes_downtime_and_preserves_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Gamelogs'
            root.mkdir()
            source = root / 'synthetic.txt'
            source.write_bytes(self.header)
            path = Path(temp) / 'q'
            with closing(PendingQueue(path)) as q:
                tailer = Tailer(root, q, started=self.started)
                source.write_bytes(self.header + self.line)
                run_id = tailer.run_id
                key = next(iter(tailer.files))
                expected = hashlib.sha256(f'{run_id}:{key}:0:{len(self.header)}'.encode()).hexdigest()
                real = tailer._checkpoint
                calls = [0]
                def crash(ceilings):
                    calls[0] += 1
                    if calls[0] == 2:
                        raise OSError('synthetic crash before commit')
                    real(ceilings)
                with patch.object(tailer, '_checkpoint', side_effect=crash), self.assertRaises(OSError):
                    tailer.poll()
                self.assertEqual(q.count(), 0)
            with source.open('ab') as f:
                f.write(self.line.replace(b'synthetic', b'while closed'))
            with closing(PendingQueue(path)) as q:
                tailer = Tailer(root, q, started=self.started)
                self.assertEqual(tailer.poll(), 1)
                self.assertEqual(q.batch()[0]['id'], expected)
                self.assertEqual(tailer.poll(), 0)
                with source.open('ab') as f:
                    f.write(self.line.replace(b'synthetic', b'after explicit restart'))
                self.assertEqual(tailer.poll(), 1)
                self.assertNotIn('while closed', [e['text'] for e in q.batch()])
                tailer.stop()
                self.assertEqual(q.db.execute('SELECT count(*) FROM capture_checkpoint').fetchone()[0], 0)

    def test_capacity_commits_prefix_and_resumes_after_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Gamelogs'
            root.mkdir()
            source = root / 'synthetic.txt'
            source.write_bytes(self.header)
            path = Path(temp) / 'q'
            with closing(PendingQueue(path, max_events=1)) as q:
                tailer = Tailer(root, q, started=self.started)
                source.write_bytes(self.header + self.line * 2)
                with self.assertRaises(QueueFull):
                    tailer.poll()
                first = q.batch()[0]['id']
                self.assertEqual(q.inserted_count, 1)
            with closing(PendingQueue(path, max_events=1)) as q:
                q.acknowledge([first])
                tailer = Tailer(root, q, started=self.started)
                self.assertEqual(tailer.poll(), 1)
                self.assertNotEqual(q.batch()[0]['id'], first)

    def test_split_utf8_reopen_skips_unconsented_continuation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Gamelogs'
            root.mkdir()
            source = root / 'synthetic.txt'
            source.write_bytes(self.header)
            path = Path(temp) / 'q'
            line = self.line.replace(b'synthetic', 'Журнал'.encode())
            split = line.index('Ж'.encode()) + 1
            with closing(PendingQueue(path)) as q:
                tailer = Tailer(root, q, started=self.started)
                source.write_bytes(self.header + line[:split])
                self.assertEqual(tailer.poll(), 0)
            # Completion outside the saved consent envelope must not be parsed.
            with source.open('ab') as f:
                f.write(line[split:])
            with closing(PendingQueue(path)) as q:
                tailer = Tailer(root, q, started=self.started)
                self.assertEqual(tailer.poll(), 0)
                with source.open('ab') as f:
                    f.write(line[:split])
                self.assertEqual(tailer.poll(), 0)
                with source.open('ab') as f:
                    f.write(line[split:])
                self.assertEqual(tailer.poll(), 1)
                self.assertEqual(q.batch()[0]['text'], 'Журнал')

    def test_recovery_rotation_and_truncation_do_not_reuse_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Gamelogs'
            root.mkdir()
            source = root / 'synthetic.txt'
            source.write_bytes(self.header)
            path = Path(temp) / 'q'
            with closing(PendingQueue(path)) as q:
                tailer = Tailer(root, q, started=self.started)
                source.write_bytes(self.header + self.line)
                tailer.poll()
                old_id = q.batch()[0]['id']
            source.rename(root / 'rotated.txt')
            source.write_bytes(self.header + self.line.replace(b'synthetic', b'downtime'))
            with closing(PendingQueue(path)) as q:
                tailer = Tailer(root, q, started=self.started)
                self.assertEqual(tailer.poll(), 0)
                source.write_bytes(self.header)
                self.assertEqual(tailer.poll(), 0)
                with source.open('ab') as f:
                    f.write(self.line)
                self.assertEqual(tailer.poll(), 1)
                self.assertEqual(q.count(), 2)
                self.assertNotEqual(q.batch()[1]['id'], old_id)
                self.assertNotIn('downtime', [e['text'] for e in q.batch()])

    def test_stop_does_not_backfill_next_start(self):
        with tempfile.TemporaryDirectory() as temp, closing(PendingQueue(Path(temp) / 'q')) as q:
            root = Path(temp) / 'Gamelogs'
            root.mkdir()
            source = root / 'synthetic.txt'
            source.write_bytes(self.header)
            tailer = Tailer(root, q, started=self.started)
            tailer.poll()
            tailer.stop()
            source.write_bytes(self.header + self.line)
            tailer = Tailer(root, q, started=self.started)
            self.assertEqual(tailer.poll(), 0)


if __name__ == '__main__':
    unittest.main()
