"""Exercise source widgets and preserve pending recovery, without real credentials."""
import tempfile
import unittest
from tools.simple_ui_preview import render


class SimpleUITests(unittest.TestCase):
    def test_real_tk_binding_states(self):
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest('Native Tk display unavailable')
        root.destroy()
        with tempfile.TemporaryDirectory() as target:
            render(target)
