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
            binding_dir = destination / 'binding'
            capture_dir = destination / 'capture'
            binding(binding_dir)
            capture(capture_dir)
            self.assertEqual({p.name for p in binding_dir.glob('*.png')}, {
                'active.png', 'bound-idle.png', 'help.png', 'offline.png',
                'primary.png', 'settings.png', 'waiting.png',
            })
            self.assertEqual({p.name for p in capture_dir.glob('*.png')}, {
                'active-expanded.png', 'denied.png', 'needs-approval.png',
                'offline.png', 'settings.png', 'updater-feedback.png',
            })
