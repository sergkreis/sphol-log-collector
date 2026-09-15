"""Synthetic adversarial diagnostics tests; never open user logs or network."""
import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from collector import diagnostics as d
from collector.core import ReadFailure, Tailer

SECRET = 'TOKEN-secret Character Private /home/private/Pilot C:\\Users\\Private https://sphol.com/collector/pair#secret'


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sink = d.Diagnostics(self.root / 'state' / 'diagnostics')
        self.patch = patch.object(d, '_sink', self.sink)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def report(self):
        target = self.root / 'report.json'
        self.sink.export(target)
        raw = target.read_text()
        self.assertNotIn(SECRET, raw)
        self.assertNotIn(str(self.root), raw)
        return json.loads(raw)

    def test_adversarial_exception_fields_and_disk_revalidation(self):
        evil_class = type(SECRET, (Exception,), {})
        for exc in (OSError(errno.EACCES, SECRET, SECRET), TimeoutError(SECRET), evil_class(SECRET),
                    UnicodeDecodeError('utf8', SECRET.encode(), 0, 1, SECRET)):
            d.emit('capture.poll', 'error', error=exc, token=SECRET, path=SECRET,
                   count=SECRET, version=SECRET, exception=SECRET)
        self.sink.record(SECRET, token=SECRET)
        report = self.report()
        self.assertEqual(len(report['events']), 4)
        self.assertEqual(report['events'][0]['error'], 'permission')
        self.assertEqual(report['events'][1]['error'], 'timeout')
        self.sink.path.write_text(json.dumps([{'time': 1, 'event': 'app.start', 'token': SECRET,
                                               'exception': SECRET, 'version': SECRET}]))
        self.sink = d.Diagnostics(self.sink.directory)
        self.assertEqual(self.report()['events'], [{'time': 1, 'event': 'app.start'}])

    def test_rotation_restart_retains_last_ack_and_bounded_bytes(self):
        self.sink.record('upload.ack', accepted=5)
        for _ in range(d.MAX_EVENTS + 30):
            self.sink.record('upload.retry', failures=9, delay=300)
        self.assertLessEqual(self.sink.path.stat().st_size, d.LIMIT)
        self.sink = d.Diagnostics(self.sink.directory)
        report = self.report()
        self.assertEqual(len(report['events']), d.MAX_EVENTS)
        self.assertEqual(report['events'][0]['accepted'], 5)
        self.assertEqual(len(list(self.sink.directory.iterdir())), 1)
        if os.name != 'nt':
            self.assertEqual(self.sink.path.stat().st_mode & 0o777, 0o600)

    def test_logging_failure_preserves_original_error_and_old_file(self):
        self.sink.record('app.start')
        before = self.sink.path.read_bytes()
        original = PermissionError(errno.EACCES, SECRET)
        @d.observed('capture.poll')
        def fail(): raise original
        with patch('collector.diagnostics.os.replace', side_effect=OSError(errno.ENOSPC, SECRET)):
            with self.assertRaises(PermissionError) as result: fail()
            self.assertIs(result.exception, original)
            self.assertFalse(self.sink.available)
            self.assertEqual(self.sink.path.read_bytes(), before)
            self.assertEqual(len(list(self.sink.directory.iterdir())), 1)
        with patch.object(self.sink, 'record', side_effect=RuntimeError(SECRET)):
            with self.assertRaises(PermissionError) as result: fail()
            self.assertIs(result.exception, original)

    def test_export_protects_state_and_existing_destination(self):
        for target in (self.root / 'state' / 'pending.sqlite3', self.root / 'state' / 'credentials.json',
                       self.root / 'raw.txt'):
            with self.assertRaises(ValueError): self.sink.export(target)
        target = self.root / 'report.json'
        target.write_text('old')
        with patch('collector.diagnostics.os.replace', side_effect=PermissionError(SECRET)):
            with self.assertRaises(PermissionError): self.sink.export(target)
        self.assertEqual(target.read_text(), 'old')

    @unittest.skipIf(os.name == 'nt', 'POSIX symlinks/FIFO permission fixture')
    def test_links_fifo_and_parent_links_rejected(self):
        victim = self.root / 'victim.json'
        victim.write_text(SECRET)
        self.sink.path.symlink_to(victim)
        self.sink.record('app.start')
        self.assertFalse(self.sink.available)
        self.assertEqual(victim.read_text(), SECRET)
        self.sink.path.unlink()
        os.mkfifo(self.sink.path)
        self.assertFalse(d.Diagnostics(self.sink.directory).available)
        self.sink.path.unlink()
        link = self.root / 'linked'
        link.symlink_to(self.sink.directory, target_is_directory=True)
        self.assertFalse(d.Diagnostics(link).available)
        target = self.root / 'report.json'
        target.symlink_to(victim)
        with self.assertRaises(OSError): self.sink.export(target)
        self.assertEqual(victim.read_text(), SECRET)

    def test_export_dotdot_cannot_overwrite_credentials(self):
        outside = self.root / 'exports'
        outside.mkdir()
        credentials = self.root / 'state' / 'credentials.json'
        credentials.write_text(SECRET)
        with self.assertRaises(ValueError):
            self.sink.export(outside / '..' / 'state' / 'credentials.json')
        self.assertEqual(credentials.read_text(), SECRET)

    def test_schema_rejects_wrong_types_and_secret_strings(self):
        for value in (SECRET, True, -1, 2**40, [], {}, None):
            record = dict.fromkeys(d.NUMBERS, value)
            record.update(time=1, event='app.start', outcome=SECRET,
                          error=SECRET, exception=SECRET, version=SECRET)
            self.assertEqual(d.clean(record), {'time': 1, 'event': 'app.start'})
        self.assertIsNone(d.clean({'time': True, 'event': 'app.start'}))
        self.assertIsNone(d.clean({'time': 1, 'event': SECRET}))
        self.sink.events.append({'time': 1, 'event': 'app.start', 'path': SECRET})
        self.assertEqual(self.report()['events'], [{'time': 1, 'event': 'app.start'}])

    def test_duplicate_main_preserves_running_instance_ack(self):
        from collector import gui
        state = self.root / 'SPHOLLogCollector'
        running = d.Diagnostics(state / 'diagnostics')
        running.record('app.start')
        before = None

        def reject_duplicate(path):
            nonlocal before
            self.assertEqual(path, state)
            # The owner writes after the duplicate could have loaded a stale snapshot.
            running.record('upload.ack', accepted=7)
            before = running.path.read_bytes()
            raise RuntimeError('Collector already running')

        with patch.dict(os.environ, LOCALAPPDATA=str(self.root)), \
                patch.object(gui.tk, 'Tk') as tk, \
                patch.object(gui.messagebox, 'showerror') as dialog, \
                patch.object(gui, 'initialize', wraps=d.initialize) as initialize, \
                patch.object(gui, 'emit', wraps=d.emit) as emit, \
                patch('collector.updater.instance_lock', side_effect=reject_duplicate):
            gui.main()
            self.assertEqual(running.path.read_bytes(), before)
            self.assertEqual(d.Diagnostics(running.directory).events[-1]['accepted'], 7)
            initialize.assert_not_called()
            emit.assert_not_called()
            dialog.assert_called_once()
            tk.return_value.destroy.assert_called_once()
            tk.return_value.mainloop.assert_not_called()

    def test_main_initialization_failure_does_not_use_stale_sink(self):
        from collector import gui
        self.sink.record('upload.ack', accepted=3)
        before = self.sink.path.read_bytes()
        with patch.dict(os.environ, LOCALAPPDATA=str(self.root)), \
                patch.object(gui.tk, 'Tk') as tk, \
                patch.object(gui.messagebox, 'showerror') as dialog, \
                patch('collector.updater.instance_lock', return_value=object()), \
                patch.object(gui, 'initialize', side_effect=OSError('unavailable')), \
                patch.object(gui, 'documents', side_effect=OSError('unavailable')), \
                patch.object(gui, 'emit', wraps=d.emit) as emit:
            gui.main()
            emit.assert_not_called()
            self.assertEqual(self.sink.path.read_bytes(), before)
            dialog.assert_called_once()
            tk.return_value.destroy.assert_called_once()

    def test_main_owned_startup_failure_is_recorded(self):
        from collector import gui
        order = []

        def acquire(path):
            order.append('lock')
            return object()

        def initialize(path):
            self.assertEqual(order, ['lock'])
            return d.initialize(path)

        with patch.dict(os.environ, LOCALAPPDATA=str(self.root)), \
                patch.object(gui.tk, 'Tk'), \
                patch.object(gui.messagebox, 'showerror'), \
                patch('collector.updater.instance_lock', side_effect=acquire), \
                patch.object(gui, 'initialize', side_effect=initialize), \
                patch.object(gui, 'documents', side_effect=PermissionError('unavailable')):
            gui.main()
        history = d.Diagnostics(self.root / 'SPHOLLogCollector' / 'diagnostics')
        self.assertEqual([event['event'] for event in history.events], ['app.start', 'app.init'])
        self.assertEqual(history.events[-1]['outcome'], 'error')

    def test_native_error_codes_without_posix_mapping(self):
        for code, category in ((2, 'missing'), (3, 'missing'), (5, 'permission'),
                               (32, 'sharing_violation'), (33, 'sharing_violation'),
                               (39, 'disk_full'), (112, 'disk_full'),
                               (267, 'not_directory'), (123, 'os_error'),
                               (9999, 'os_error')):
            with self.subTest(winerror=code):
                # Set explicitly: Linux ignores the OSError winerror constructor arg.
                exc = OSError(SECRET)
                exc.winerror = code
                self.assertIsNone(exc.errno)
                with patch('collector.diagnostics.os.scandir', side_effect=exc):
                    self.sink.inspect_logs(self.root)
                event = self.sink.events[-1]
                self.assertEqual(event['error'], category)
                self.assertEqual(event['winerror'], code)
                self.assertEqual(event['exists'], int(category != 'missing'))
                self.assertEqual(event['accessible'], 0)
        self.report()
        self.assertNotIn(SECRET, self.sink.path.read_text())

    def test_native_missing_path_apis(self):
        missing = self.root / 'Synthetic Private Pilot' / 'Gamelogs'
        for operation in (lambda: missing.resolve(strict=True), lambda: missing.stat(),
                          lambda: os.scandir(missing)):
            with self.subTest(operation=operation):
                with self.assertRaises(OSError) as caught:
                    operation()
                self.assertEqual(d.exception_fields(caught.exception)['error'], 'missing')
                d.emit('logs.paths', 'error', error=caught.exception)
        self.report()

    @unittest.skipUnless(os.name == 'nt', 'Native Windows invalid-name distinction')
    def test_native_invalid_name_is_not_missing(self):
        # The original fixture contained colons/backslashes from SECRET, which
        # is invalid Windows syntax, not evidence of a missing directory.
        self.sink.inspect_logs(self.root / SECRET.replace('/', '_'))
        self.assertEqual(self.sink.events[-1]['winerror'], 123)
        self.assertEqual(self.sink.events[-1]['error'], 'os_error')
        self.report()

    def test_gamelogs_and_classifications(self):
        # A valid private filename on both platforms; retain strict classification.
        self.sink.inspect_logs(self.root / 'Synthetic Private Pilot Gamelogs')
        self.assertEqual(self.sink.events[-1]['error'], 'missing')
        self.assertEqual(self.sink.events[-1]['exists'], 0)
        with patch('collector.diagnostics.os.scandir', side_effect=PermissionError(errno.EACCES, SECRET)):
            self.sink.inspect_logs(self.root)
        self.assertEqual(self.sink.events[-1]['error'], 'permission')
        for number, category in ((errno.ENOTDIR, 'not_directory'), (errno.ENOSPC, 'disk_full')):
            self.assertEqual(d.exception_fields(OSError(number, SECRET))['error'], category)
        for reason in ('file_limit', 'unsafe_file', 'changed_file'):
            self.assertEqual(d.exception_fields(ReadFailure(reason))['error'], reason)
        win = OSError(SECRET)
        win.winerror = 32
        self.assertEqual(d.exception_fields(win)['error'], 'sharing_violation')
        (self.root / 'synthetic.txt').write_text(SECRET)
        self.sink.inspect_logs(self.root)
        self.assertEqual(self.sink.events[-1]['count'], 1)
        self.report()

    def test_pairing_retry_ack_and_credential_failure_are_safe(self):
        from collector.transport import Pairing, Uploader, HTTPFailure
        from collector.credentials import CredentialStore
        from collector.network_gui import Snapshot
        credentials = {'access_token': 't'*43, 'token_type': 'Bearer', 'scope': 'combat:write',
                       'installation_id': 'a'*32, 'expires_at': '2099-01-01T00:00:00Z',
                       'characters': [{'id': 42, 'name': SECRET}]}
        class HTTP:
            def post(self, *args, **kwargs): raise TimeoutError(SECRET)
        with self.assertRaises(TimeoutError): Pairing(http=HTTP()).start()
        with patch('collector.credentials.crypt', side_effect=PermissionError(errno.EACCES, SECRET)):
            with self.assertRaises(PermissionError): CredentialStore(self.root/'key').save(credentials)
        snapshot = Snapshot([{'id': 'e'*64, 'schema': 1, 'time': '2026-01-01T00:00:00Z',
                              'type': 'combat', 'listener': SECRET, 'text': SECRET}])
        uploader = Uploader(credentials, http=HTTP())
        uploader.upload(snapshot)
        self.assertEqual(uploader.failures, 1)
        self.assertEqual(snapshot.accepted, [])
        uploader.next_try = 0
        with patch.object(uploader.http, 'post', return_value=(200, {'accepted_ids': ['e'*64], 'rejected': []})):
            uploader.upload(snapshot)
        self.assertEqual(snapshot.accepted, ['e'*64])
        events = self.report()['events']
        self.assertTrue(any(e['event'] == 'upload.retry' and e['error'] == 'timeout' for e in events))
        self.assertTrue(any(e['event'] == 'credential.save' and e.get('error') == 'permission' for e in events))
        self.assertTrue(any(e['event'] == 'upload.ack' and e['accepted'] == 1 for e in events))


if __name__ == '__main__': unittest.main()
