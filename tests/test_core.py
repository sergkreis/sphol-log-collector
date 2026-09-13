"""All logs and characters below are synthetic. No private fixtures."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from collector.core import PendingQueue, QueueFull, Tailer, parse_line, safe_open

START = datetime(2030, 1, 1, tzinfo=timezone.utc)
HEADER = 'Слушатель: Synthetic Pilot\nСеанс начат: 2030.01.01 00:00:00\n'.encode()
COMBAT = '[ 2030.01.01 00:00:01 ] (combat) <color=red>235 на Test Pilot[FAKE](Test Ship*)</color> - Попал\n'.encode()
OLD = b'[ 2020.01.01 00:00:01 ] (combat) historical\n'


class ParserTests(unittest.TestCase):
    def test_russian_and_markup(self):
        event = parse_line(COMBAT, 'Synthetic Pilot')
        self.assertEqual(event['listener'], 'Synthetic Pilot')
        self.assertIn('Попал', event['text'])
        self.assertNotIn('<color', event['text'])

    def test_allowlist(self):
        for category in ('notify', 'None', 'chat', 'info', 'COMBAT'):
            self.assertIsNone(parse_line(COMBAT.replace(b'combat', category.encode())))

    def test_bad_input(self):
        for raw in (b'\xff', b'x' * 9000, b'[ 2030.99.99 00:00:00 ] (combat) x'):
            self.assertIsNone(parse_line(raw))

    def test_tackle_not_claimed_success(self):
        raw = b'[ 2030.01.01 00:00:01 ] (combat) Synthetic Pilot attempts to warp scramble Test Pilot\n'
        self.assertIn('attempts', parse_line(raw)['text'])


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'Gamelogs'
        self.root.mkdir()
        self.db = Path(self.tmp.name) / 'pending.sqlite3'
        self.queue = PendingQueue(self.db)
        self.addCleanup(lambda: self.queue.close())

    def log(self, name='a.txt', data=HEADER):
        p = self.root / name
        p.write_bytes(data)
        return p

    def append(self, p, raw=COMBAT):
        with p.open('ab') as f:
            f.write(raw)

    def tail(self):
        return Tailer(self.root, self.queue, START)

    def test_existing_content_skipped_and_idempotent_poll(self):
        p = self.log(data=HEADER + COMBAT)
        tail = self.tail()
        self.assertEqual(tail.poll(), 0)
        self.append(p)
        self.assertEqual(tail.poll(), 1)
        identity = self.queue.batch()[0]['id']
        self.assertEqual(tail.poll(), 0)
        self.assertEqual(self.queue.batch()[0]['id'], identity)

    def test_new_file_filters_history(self):
        tail = self.tail()
        self.log(data=HEADER + OLD + COMBAT)
        self.assertEqual(tail.poll(), 1)

    def test_multiple_characters(self):
        a, b = self.log(), self.log('b.txt', b'Listener: Synthetic Second\n')
        tail = self.tail()
        self.append(a)
        self.append(b)
        self.assertEqual(tail.poll(), 2)
        self.assertEqual({e['listener'] for e in self.queue.batch()}, {'Synthetic Pilot', 'Synthetic Second'})

    def test_split_utf8(self):
        p = self.log()
        tail = self.tail()
        split = COMBAT.index('П'.encode()) + 1
        self.append(p, COMBAT[:split])
        self.assertEqual(tail.poll(), 0)
        self.append(p, COMBAT[split:])
        self.assertEqual(tail.poll(), 1)

    def test_partial_before_start_is_not_collected(self):
        p = self.log(data=HEADER + COMBAT[:-5])
        tail = self.tail()
        self.append(p, COMBAT[-5:] + COMBAT)
        self.assertEqual(tail.poll(), 1)

    def test_rename_and_new_file(self):
        p = self.log()
        tail = self.tail()
        self.append(p)
        self.assertEqual(tail.poll(), 1)
        p.rename(self.root / 'rotated.txt')
        self.log(data=HEADER + COMBAT)
        self.assertEqual(tail.poll(), 1)
        self.assertEqual(self.queue.count(), 2)

    def test_observed_truncation(self):
        p = self.log(data=HEADER + COMBAT * 4)
        tail = self.tail()
        p.write_bytes(HEADER)
        tail.poll()
        self.append(p)
        self.assertEqual(tail.poll(), 1)

    def test_queue_full_does_not_advance(self):
        p = self.log()
        tail = self.tail()
        self.queue.max_events = 1
        self.append(p, COMBAT * 2)
        with self.assertRaises(QueueFull):
            tail.poll()
        first = self.queue.batch()[0]['id']
        self.queue.acknowledge([first])
        self.assertEqual(tail.poll(), 1)
        self.assertNotEqual(self.queue.batch()[0]['id'], first)

    def test_restart_pending_id_survives_no_history_backfill(self):
        p = self.log()
        tail = self.tail()
        self.append(p)
        tail.poll()
        before = self.queue.batch()
        self.queue.close()
        self.queue = PendingQueue(self.db)
        self.assertEqual(self.queue.batch(), before)
        self.append(p)
        self.assertEqual(self.tail().poll(), 0)

    def test_duplicate_insert_and_byte_bound(self):
        event = parse_line(COMBAT)
        self.queue.put('a', event)
        self.queue.put('a', event)
        self.assertEqual(self.queue.count(), 1)
        self.queue.max_bytes = 1
        with self.assertRaises(QueueFull):
            self.queue.put('b', event)
        self.assertEqual(self.queue.count(), 1)

    def test_unknown_ack_does_not_delete(self):
        self.queue.put('a', parse_line(COMBAT))
        self.queue.acknowledge(['unknown'])
        self.assertEqual(self.queue.count(), 1)
        self.queue.clear()
        self.assertEqual(self.queue.count(), 0)

    def test_chatlogs_and_subdirectories_not_scanned(self):
        chat = self.root.parent / 'Chatlogs'
        chat.mkdir()
        (chat / 'private.txt').write_bytes(COMBAT)
        tail = self.tail()
        (self.root / 'nested').mkdir()
        (self.root / 'nested' / 'x.txt').write_bytes(COMBAT)
        self.assertEqual(tail.poll(), 0)

    def test_symlink_not_read(self):
        outside = self.root.parent / 'outside.txt'
        outside.write_bytes(COMBAT)
        link = self.root / 'link.txt'
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('Symlinks unavailable without Windows developer mode')
        with self.assertRaises(OSError):
            safe_open(link)
        self.assertEqual(self.tail().poll(), 0)

    def test_oversized_line_resynchronizes(self):
        p = self.log()
        tail = self.tail()
        self.append(p, b'x' * 20000 + b'\n' + COMBAT)
        self.assertEqual(tail.poll(), 1)
        self.assertGreater(tail.rejected, 0)

    def test_late_header_new_file(self):
        tail = self.tail()
        p = self.log(data=b'')
        tail.poll()
        self.append(p, HEADER + COMBAT)
        tail.poll()
        self.assertEqual(self.queue.batch()[0]['listener'], 'Synthetic Pilot')


if __name__ == '__main__':
    unittest.main()
