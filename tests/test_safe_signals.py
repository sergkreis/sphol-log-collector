"""Synthetic privacy boundary; no user logs or remote requests."""
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from collector.expanded import parse_game_line, ExpandedQueue
from collector.signals import sanitize, VOCABULARY
from collector.transport import build_batch, ProtocolError
from test_expanded import creds


class SafeSignalsTests(unittest.TestCase):
    def test_allowlist_and_no_dynamic_fields(self):
        for signal, forms in VOCABULARY.items():
            for text in forms:
                for category in ('notify','None'):
                    self.assertEqual(sanitize(category,text), forms[0])
        text='Переход в варп-режим по приказу Synthetic Commander'
        event=parse_game_line(('[ 2030.01.01 00:00:01 ] (notify) '+text+'\n').encode(),'Synthetic Pilot')
        self.assertEqual(event['text'],'Выполняется варп флота.')
        self.assertNotIn('Commander',str(event))
        for bad in (text+'? private', '<b>'+text+'</b>', 'Contract accepted',
                    'https://private.invalid', 'Warping to Private System',
                    'Самоуничтожение через 120 секунд.', 'Join fleet?', 'Jumping.'):
            self.assertIsNone(sanitize('notify',bad))
        self.assertIsNone(sanitize('chat','Fleet warp initiated.'))

    def test_existing_raw_queue_is_not_rewritten_or_sent(self):
        with TemporaryDirectory() as tmp, closing(ExpandedQueue(Path(tmp))) as queue:
            event={'schema':2,'time':'2030-01-01T00:00:01Z','type':'game-event',
                   'category':'notify','listener':creds()['characters'][0]['name'],'text':'private raw'}
            queue.put('a'*64,event)
            before=queue.batch()
            with self.assertRaises(ProtocolError):
                build_batch(queue,creds())
            self.assertEqual(queue.batch(),before)
            self.assertEqual(queue.count(),1)
