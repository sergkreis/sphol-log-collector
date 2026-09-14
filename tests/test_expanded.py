"""Synthetic schema 2 sources in isolated temp paths; no real user logs/network."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from collector.core import PendingQueue, QueueFull, Tailer
from collector.expanded import ExpandedQueue, capture, parse_game_line, SCOPE
from collector import transport as t
from test_transport import credentials, event


def creds():
    return {**credentials(), 'scope': SCOPE}


class ExpandedTests(unittest.TestCase):
    def test_categories_original_markup_private_text(self):
        for category in ('combat', 'notify', 'None', 'info', 'warning', 'question', 'custom_1'):
            raw = f' [ 2030.01.01 00:00:01 ] ({category}) <b>Личное уведомление</b>  \r\n'.encode()
            e = parse_game_line(raw, 'Synthetic Pilot')
            self.assertIsNone(e)
        for raw in (b'[ 2030.01.01 00:00:01 ] (notify) x\x00y\n', b'bad\xff\n', b'x'*8193,
                    b'[ 2030.01.01 00:00:01 ] (bad category) x\n'):
            self.assertIsNone(parse_game_line(raw))

    def test_separate_scoped_consent_eof_and_inert_prelogin(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-private-') as tmp:
            root = Path(tmp)
            logs = root / 'Gamelogs'
            logs.mkdir()
            chat = root / 'Chatlogs'
            chat.mkdir()
            (chat / 'private.txt').write_text('PRIVATE CHAT NEVER READ')
            (root / 'diagnostic.txt').write_text('PRIVATE DIAGNOSTIC NEVER READ')
            (logs / 'prelogin.txt').write_text('---\nGamelog\nSession started: 2030\n---\n')
            p = logs / 'pilot.txt'
            p.write_text('  Listener: Synthetic Pilot\n---\n[ 2030.01.01 00:00:01 ] (notify) OLD\n')
            with closing(PendingQueue(root/'pending.sqlite3')) as legacy, closing(ExpandedQueue(root)) as q:
                legacy.put('a'*64, {k:v for k,v in event().items() if k != 'id'})
                saved = legacy.batch()
                for consent, c in ((False, creds()), (True, credentials()), (True, None)):
                    with self.assertRaises(ValueError):
                        capture(logs, q, consent=consent, credentials=c)
                tailer = capture(logs, q, consent=True, credentials=creds(), started=datetime(2030,1,1,tzinfo=timezone.utc))
                self.assertEqual(tailer.poll(), 0)
                self.assertEqual(tailer.unattributed_files, 0)
                with p.open('ab') as f:
                    for category in ('notify','None','combat','info'):
                        f.write(f'[ 2030.01.01 00:00:02 ] ({category}) Fleet warp initiated.\n'.encode())
                self.assertEqual(tailer.poll(),2)
                self.assertEqual({e['category'] for e in q.batch()}, {'notify','None'})
                self.assertEqual(legacy.batch(), saved)
                self.assertNotIn('PRIVATE', str(q.batch()))
                ids = [e['id'] for e in q.batch()]
            with closing(ExpandedQueue(root)) as q:
                self.assertEqual([e['id'] for e in q.batch()], ids)

    def test_bounded_versioned_queue_and_mixed_envelope(self):
        with tempfile.TemporaryDirectory() as tmp, closing(ExpandedQueue(Path(tmp), max_events=1)) as q:
            e = parse_game_line(b'[ 2030.01.01 00:00:01 ] (None) Fleet warp initiated.\n', 'Synthetic Pilot')
            q.put('b'*64,e)
            q.put('b'*64,e)
            with self.assertRaises(QueueFull):
                q.put('c'*64,e)
            with self.assertRaises(t.ProtocolError):
                t.build_batch(q,credentials())
            mixed = Mock()
            mixed.batch.return_value = [event(), *q.batch()]
            batch = t.build_batch(mixed,creds())
            self.assertEqual(batch['schema'],2)
            self.assertEqual([e['schema'] for e in batch['events']],[1,2])
            http = Mock()
            http.post.side_effect = t.HTTPFailure(503)
            uploader = t.Uploader(creds(),http)
            uploader.upload(q)
            self.assertEqual(q.count(),1)
            self.assertGreater(uploader.next_try,0)
            self.assertFalse(uploader.paused)
            uploader.next_try = 0
            http.post.side_effect = None
            http.post.return_value = (200, {'accepted_ids':['b'*64], 'rejected':[]})
            uploader.upload(q)
            self.assertEqual(q.count(),0)

    def test_new_pairing_requests_scope_rejects_legacy_redemption(self):
        http = Mock()
        http.post.return_value = (201, {'device_secret':'SYNTHETIC_'*8,'user_code':'TEST-1234','verification_uri':t.PAIR_URI,'expires_in':300,'interval':5})
        p=t.Pairing(http,scope=SCOPE)
        p.start()
        self.assertEqual(http.post.call_args.args[1]['scope'],SCOPE)
        p.next_poll=0
        http.post.return_value=(200,credentials())
        with self.assertRaises(t.ProtocolError):
            p.poll()

    def test_local_consent_does_not_enable_cloud(self):
        # LocalCapture remains a separate disk-only API, never an expanded queue.
        from collector.local_capture import LocalCapture
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            logs=root/'Gamelogs'
            logs.mkdir()
            local=LocalCapture(logs,root/'local',consent=True)
            try:
                self.assertFalse(hasattr(local,'uploader'))
                self.assertFalse((root/'pending-v2.sqlite3').exists())
            finally:
                local.close()
