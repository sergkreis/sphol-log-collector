"""Update discovery is independent; replacement retains write/ACK barriers."""
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from collector.update_gui import UpdateControls, active


class UpdateRecoveryTests(unittest.TestCase):
    def app(self, directory):
        return SimpleNamespace(tailer=None, local_capture=None, upload_enabled=False,
            busy=False, pairing=None, expanded=None, recovered_token=None,
            site_codes=SimpleNamespace(busy=False, uncertain=True),
            queue=SimpleNamespace(path=Path(directory)/'pending.sqlite3'), window=Mock())

    def test_persisted_failed_claim_allows_replace_but_workers_and_key_do_not(self):
        with tempfile.TemporaryDirectory() as d:
            app = self.app(d)
            proof = Path(d)/'site-redemption.dpapi'
            proof.write_bytes(b'synthetic encrypted proof')
            self.assertFalse(active(app))
            for owner, name in ((app.site_codes, 'busy'), (app, 'busy'), (app, 'recovered_token'), (app, 'pairing')):
                setattr(owner, name, True)
                self.assertTrue(active(app))
                setattr(owner, name, False)
            self.assertEqual(proof.read_bytes(), b'synthetic encrypted proof')

    def test_check_runs_from_failed_binding_and_active_operation(self):
        with tempfile.TemporaryDirectory() as d:
            app = self.app(d)
            ui = UpdateControls.__new__(UpdateControls)
            ui.app, ui.busy, ui.results = app, False, queue.Queue()
            ui.button, ui.label = Mock(), Mock()
            for in_flight in (False, True):
                app.busy = in_flight
                with patch('collector.update_gui.threading.Thread') as worker, patch('collector.update_gui.updater.prepare', return_value=None) as prepare:
                    ui.check()
                    worker.assert_called_once()
                    worker.call_args.kwargs['target']()
                    prepare.assert_called_once()
                ui.poll()
                self.assertFalse(ui.busy)

    def test_downloaded_candidate_never_launches_helper_during_write(self):
        with tempfile.TemporaryDirectory() as d:
            app = self.app(d)
            app.busy = True
            ui = UpdateControls.__new__(UpdateControls)
            ui.app, ui.busy, ui.results = app, True, queue.Queue()
            ui.button, ui.label = Mock(), Mock()
            ui.results.put(((Path(d), '0.3.10', '0'*64), None))
            with patch('collector.update_gui.updater.start_helper') as helper:
                ui.poll()
                helper.assert_not_called()
            self.assertIn('замена отложена', ui.label.config.call_args.kwargs['text'])
