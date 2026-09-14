"""Read-only presentation regressions; all records are synthetic."""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from collector.core import PendingQueue
from collector.queue_status import queue_summary
from collector.updater import version
from collector.version import VERSION


class PresentationTests(unittest.TestCase):
    def test_unknown_and_other_are_not_eligible_or_migrated(self):
        with tempfile.TemporaryDirectory() as temp:
            with closing(PendingQueue(Path(temp) / 'queue.db')) as queue:
                for i, listener in enumerate(['unknown'] * 4 + ['Тест', 'Другой', '']):
                    queue.put(str(i), {'listener': listener})
                before = list(queue.db.execute('SELECT * FROM pending'))
                text = queue_summary(queue, {'Тест'})
                self.assertIn('Событий на компьютере: 7', text)
                self.assertIn('Для персонажа: 1', text)
                self.assertIn('Без персонажа: 5', text)
                self.assertIn('Остальные: 1', text)
                self.assertEqual(before, list(queue.db.execute('SELECT * FROM pending')))

    def test_unbound_does_not_claim_sendable(self):
        with tempfile.TemporaryDirectory() as temp:
            with closing(PendingQueue(Path(temp) / 'queue.db')) as queue:
                queue.put('a', {'listener': 'Тест'})
                self.assertIn('Для персонажа: 0', queue_summary(queue, set()))

    def test_next_version_is_accepted_by_existing_updater(self):
        self.assertEqual(VERSION, '0.3.1')
        self.assertGreater(version(VERSION), version('0.3.0'))
