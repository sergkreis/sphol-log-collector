"""Counter provenance and real Tk preview acceptance (synthetic, offline)."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
from collector.core import PendingQueue, QueueFull, Tailer
from collector.dashboard import Dashboard


class CounterEvidenceTests(unittest.TestCase):
    def test_new_commit_only_duplicate_full_clear_ack_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'queue.db'
            with closing(PendingQueue(path, max_events=1)) as queue:
                queue.put('first', {'listener': 'Synthetic'})
                queue.put('first', {'listener': 'Synthetic'})
                with self.assertRaises(QueueFull):
                    queue.put('second', {'listener': 'Synthetic'})
                self.assertEqual(queue.inserted_count, 1)
                queue.acknowledge(['first'])
                self.assertEqual(queue.inserted_count, 1)
                queue.put('second', {'listener': 'Synthetic'})
                queue.clear()
                self.assertEqual(queue.inserted_count, 2)
                queue.put('third', {'listener': 'Synthetic'})
            with closing(PendingQueue(path)) as queue:
                self.assertEqual(queue.count(), 1)
                self.assertEqual(queue.inserted_count, 0)

    def test_partial_poll_commits_count_even_when_later_event_fills_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            logs = root / 'Gamelogs'
            logs.mkdir()
            with closing(PendingQueue(root / 'queue.db', max_events=1)) as queue:
                tailer = Tailer(logs, queue, started=datetime(2020, 1, 1, tzinfo=timezone.utc))
                (logs / 'synthetic.txt').write_text('Listener: Synthetic\n-----\n' +
                    '[ 2026.01.01 00:00:00 ] (combat) synthetic\n' * 2)
                with self.assertRaises(QueueFull):
                    tailer.poll()
                self.assertEqual(queue.inserted_count, 1)
                queue.acknowledge([e['id'] for e in queue.batch()])
                self.assertEqual(tailer.poll(), 1)
                self.assertEqual(queue.inserted_count, 2)

    def test_empty_ack_does_not_refresh_age_or_confirmed_count(self):
        view = Dashboard.__new__(Dashboard)
        view.confirmed, view.ack_at = 0, None
        with patch('collector.dashboard.time.monotonic', return_value=10):
            view.acknowledge([])
            self.assertIsNone(view.ack_at)
            view.acknowledge(['a', 'b'])
        with patch('collector.dashboard.time.monotonic', return_value=100):
            view.acknowledge([])
        self.assertEqual((view.confirmed, view.ack_at), (2, 10))

    def test_graphite_text_contrast(self):
        from collector.theme import BG, FG, MUTED, ACCENT
        def luminance(color):
            rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            rgb = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
            return sum(v * w for v, w in zip(rgb, (.2126, .7152, .0722)))
        for foreground, background in [(FG, BG), (MUTED, BG), ('#e5c48b', BG), (BG, ACCENT)]:
            a, b = sorted((luminance(foreground), luminance(background)))
            self.assertGreaterEqual((b + .05) / (a + .05), 4.5)


@unittest.skipUnless(os.environ.get('DISPLAY'), 'Actual Tk acceptance requires Xvfb or a desktop')
class ActualTkTests(unittest.TestCase):
    def test_actual_callbacks_and_synthetic_protocol(self):
        from tools.gui_preview import render
        with tempfile.TemporaryDirectory() as temp:
            render(temp)
            self.assertEqual(len(list(Path(temp).glob('*.png'))), 4)
