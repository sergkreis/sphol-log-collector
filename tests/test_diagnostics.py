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

    def test_gamelogs_and_classifications(self):
        self.sink.inspect_logs(self.root / SECRET.replace('/', '_'))
        self.assertEqual(self.sink.events[-1]['error'], 'missing')
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
