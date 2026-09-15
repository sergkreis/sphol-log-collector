"""Regression for the real-EXE migration child's synthetic Windows profile."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.native_cross_version_smoke import child_env, check_child_documents


class CrossVersionProfileTests(unittest.TestCase):
    def test_child_profile_has_documents_and_keeps_private_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(os.environ, {'_PYI_PARENT_PROCESS_LEVEL': '1'}):
                env = child_env(root)
            self.assertTrue((root / 'Documents').is_dir())
            self.assertFalse((root / 'Documents' / 'EVE').exists())
            for key in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'HOME', 'TEMP', 'TMP'):
                self.assertEqual(env[key], str(root))
            self.assertNotIn('_PYI_PARENT_PROCESS_LEVEL', env)
            self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'], '1')
            self.assertEqual(child_env(root), env)

    @unittest.skipUnless(os.name == 'nt', 'Windows known-folder API required')
    def test_native_known_folder_under_exact_child_environment(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = child_env(root)
            (root / 'Documents').rmdir()
            try:
                check_child_documents(env)
            except subprocess.CalledProcessError:
                print('Reproduced: empty synthetic profile fails native Documents lookup', flush=True)
            else:
                print('Native Documents lookup is redirected outside synthetic profile', flush=True)
            check_child_documents(child_env(root))

    def test_preflight_uses_child_env_and_fails_closed(self):
        env = {'USERPROFILE': 'synthetic'}
        with patch('tools.native_cross_version_smoke.subprocess.run') as run:
            check_child_documents(env)
        self.assertIs(run.call_args.kwargs['env'], env)
        self.assertTrue(run.call_args.kwargs['check'])
        self.assertIn('documents()', run.call_args.args[0][2])
        self.assertIn("'EVE' / 'logs'", run.call_args.args[0][2])
