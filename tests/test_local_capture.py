"""Synthetic-only local recorder tests, also executed on native Windows CI."""
from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
import unittest
from collector.local_capture import LocalCapture, CaptureStopped


class LocalCaptureTests(unittest.TestCase):
    def test_all_types_utf8_rotation_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Gamelogs'
            root.mkdir()
            p = root / 'synthetic.txt'
            p.write_bytes(b'old history\nold partial')
            base = Path(temp) / 'private'
            with closing(LocalCapture(root, base, consent=True)) as c:
                with p.open('ab') as f:
                    f.write(b' continuation\n')
                    for kind in ('combat', 'notify', 'info', 'warning', 'mining', 'unknown'):
                        f.write(f'[ 2099.01.01 00:00:00 ] ({kind}) Synthetic event\n'.encode())
                    f.write('Синтетика'.encode()[:-1])
                c.poll()
                self.assertEqual(c.count, 6)
                with p.open('ab') as f:
                    f.write('Синтетика'.encode()[-1:] + b'\n')
                c.poll()
                self.assertEqual(c.count, 7)
                p.rename(root / 'rotated.txt')
                p.write_bytes(b'(notify) Synthetic rotated\n')
                c.poll()
                self.assertEqual(c.count, 8)
                p.write_bytes(b'new\n')
                c.poll()
                self.assertEqual(c.count, 9)
                path = c.path
                if os.name != 'nt':
                    self.assertEqual(c.directory.stat().st_mode & 0o777, 0o700)
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            records = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(records[6]['line'], 'Синтетика\n')
            self.assertFalse(any('old' in r['line'] for r in records))
            self.assertEqual(json.loads((path.parent / 'session.json').read_text())['complete_lines'], 9)
            with closing(LocalCapture(root, base, consent=True)) as c:
                c.poll()
                self.assertEqual(c.count, 0)
                self.assertNotEqual(c.path, path)

    def test_consent_chatlogs_and_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Gamelogs'
            root.mkdir()
            base = Path(temp) / 'private'
            with self.assertRaises(ValueError):
                LocalCapture(root, base)
            self.assertFalse(base.exists())
            chat = Path(temp) / 'Chatlogs'
            chat.mkdir()
            with self.assertRaises(ValueError):
                LocalCapture(chat, base, consent=True)
            for options, data in [({'max_bytes': 1}, b'event\n'), ({'max_line': 3}, b'long\n'), ({}, b'\xff\n')]:
                with closing(LocalCapture(root, base, consent=True, **options)) as c:
                    p = root / 'new.txt'
                    with p.open('ab') as f:
                        f.write(data)
                    with self.assertRaises((CaptureStopped, UnicodeError)):
                        c.poll()
                    self.assertTrue(c.closed)
                    self.assertEqual(c.count, 0)
                    self.assertEqual(c.path.stat().st_size, 0)
            with self.assertRaises(CaptureStopped):
                LocalCapture(root, base, consent=True, total_bytes=1)

    @unittest.skipIf(os.name == 'nt', 'Windows symlink creation requires privilege; ACL exercised by native creation')
    def test_private_path_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Gamelogs'
            root.mkdir()
            link = Path(temp) / 'link'
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(OSError):
                LocalCapture(root, link / 'captures', consent=True)


if __name__ == '__main__':
    unittest.main()
