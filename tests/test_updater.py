import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from collector import updater as u
from collector.update_gui import active


class Updates(unittest.TestCase):
    def fixture(self):
        self.data = b'MZsynthetic-only'
        sha = hashlib.sha256(self.data).hexdigest()
        self.manifest = dict(schema=1, version='1.2.3', asset=u.ASSET, size=len(self.data), sha256=sha)
        self.release = dict(draft=False, prerelease=False, tag_name='v1.2.3', assets=[dict(name=n, size=len(self.data), browser_download_url=f'https://github.com/{u.REPO}/releases/download/v1.2.3/{n}') for n in (u.ASSET, 'update-manifest.json')])
        def fetch(url, limit):
            if url == u.API:
                return json.dumps(self.release).encode()
            if url.endswith('.json'):
                return json.dumps(self.manifest).encode()
            return self.data
        return fetch

    def test_versions(self):
        for bad in ['v1.2.3', '1.2', '01.2.3', '1.2.3-rc1', '../1.2.3', '1.2.3\n', None, '9999999.0.0']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                u.version(bad)
        self.assertGreater(u.version('1.10.0'), u.version('1.9.9'))

    def test_urls_and_redirects(self):
        for bad in ['http://github.com/a', 'https://github.com.evil/a', 'https://user@github.com/a', 'https://github.com:444/a', 'file:///a', 'https://evil.test/a']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                u.safe_url(bad)
            with self.assertRaises(ValueError):
                u.Redirects().redirect_request(None, None, 302, '', {}, bad)
        u.safe_url('https://release-assets.githubusercontent.com/a?token=synthetic')

    def test_prepare_and_no_downgrade(self):
        fetch = self.fixture()
        with tempfile.TemporaryDirectory() as d:
            result = u.prepare('1.0.0', Path(d), fetch)
            self.assertEqual((result[0] / u.ASSET).read_bytes(), self.data)
            self.assertIsNone(u.prepare('1.2.3', Path(d), fetch))
            self.assertIsNone(u.prepare('2.0.0', Path(d), fetch))

    def test_adversarial_manifest(self):
        for field, value in [('sha256', '0'*64), ('size', True), ('size', u.MAX_EXE+1), ('asset', '../evil.exe'), ('version', '1.2.4'), ('schema', True)]:
            fetch = self.fixture()
            self.manifest[field] = value
            with tempfile.TemporaryDirectory() as d, self.subTest(field=field), self.assertRaises(ValueError):
                u.prepare('1.0.0', Path(d), fetch)

    def test_release_mismatch(self):
        for mode in ('prerelease', 'duplicate', 'url'):
            fetch = self.fixture()
            if mode == 'prerelease': self.release['prerelease'] = True
            if mode == 'duplicate': self.release['assets'].append(self.release['assets'][0])
            if mode == 'url': self.release['assets'][0]['browser_download_url'] = 'https://github.com/other/repo/a'
            with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
                u.prepare('1.0.0', Path(d), fetch)
        with self.assertRaises(ValueError): u.strict_json(b'{"a":1,"a":2}')

    def test_bounded_download(self):
        for payload, length in [(b'12345', None), (b'12', '3'), (b'12', '999')]:
            response = io.BytesIO(payload)
            response.url = u.API
            response.headers = {} if length is None else {'Content-Length': length}
            opener = SimpleNamespace(open=lambda *a, **k: response)
            with patch.object(u.urllib.request, 'build_opener', return_value=opener), self.assertRaises(ValueError):
                u.download(u.API, 4)

    def test_path_and_exclusive_write(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root/'file'
            u.private_write(target, b'a')
            with self.assertRaises(FileExistsError): u.private_write(target, b'b')
            with self.assertRaises(ValueError): u.safe_path(root)
            with self.assertRaises(ValueError): u.safe_path(root/'..'/'file')
            try: (root/'link').symlink_to(target)
            except OSError: return  # Windows unprivileged symlink creation unavailable.
            with self.assertRaises(ValueError): u.safe_path(root/'link')

    def test_replace_and_rollback_preserve_state(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stage = root/'update-synthetic'; stage.mkdir()
            target, payload = root/'original.exe', stage/u.ASSET
            target.write_bytes(b'old'); payload.write_bytes(b'new')
            queue = root/'pending.sqlite3'; queue.write_bytes(b'untouched')
            old, new = u.digest(target), u.digest(payload)
            calls = []
            def fail_once(path):
                calls.append(path)
                if len(calls) == 1: raise OSError('synthetic launch failure')
            with self.assertRaises(OSError): u.replace_and_launch(target, payload, old, new, fail_once)
            self.assertEqual(target.read_bytes(), b'old')
            backup = u.replace_and_launch(target, payload, old, new, lambda p: calls.append(p))
            self.assertEqual(backup.read_bytes(), b'old')
            self.assertEqual(target.read_bytes(), b'new')
            self.assertEqual(queue.read_bytes(), b'untouched')
            self.assertEqual(set(calls), {target})

    def test_lifecycle_activity_blocks_update(self):
        app = SimpleNamespace(tailer=None, local_capture=None, upload_enabled=False, busy=False, pairing=None, expanded=None)
        self.assertFalse(active(app))
        for name in vars(app):
            if name == 'expanded': continue
            setattr(app, name, True)
            self.assertTrue(active(app))
            setattr(app, name, None)
        app.expanded = SimpleNamespace(enabled=False, tailer=None, busy=True, pairing=None)
        self.assertTrue(active(app))

    def test_stale_hash_never_replaces(self):
        with tempfile.TemporaryDirectory() as d:
            a,b = Path(d)/'a.exe', Path(d)/'b.exe'
            a.write_bytes(b'old'); b.write_bytes(b'new')
            with self.assertRaises(ValueError): u.replace_and_launch(a,b,'0'*64,u.digest(b))
            self.assertEqual(a.read_bytes(), b'old')

if __name__ == '__main__': unittest.main()
