"""Real compact widgets in source and frozen EXE, synthetic/offline only."""
import os
from pathlib import Path
import tempfile
import unittest
from tools.simple_ui_preview import render as binding
from tools.gui_preview import render as capture

class NativeCompactSmoke(unittest.TestCase):
    def test_compact_binding_capture_settings_and_callbacks(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(os.environ.get('SPHOL_SMOKE_EVIDENCE', temp)) / 'compact'
            binding(destination)
            capture(destination)
            self.assertEqual(len(list(destination.glob('*.png'))), 10)
