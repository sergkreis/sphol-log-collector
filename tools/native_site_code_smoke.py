"""Frozen real-Tk/DPAPI site-code smoke; synthetic transport, no external I/O."""
import gc
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from collector.credentials import CredentialStore
from collector.site_code import PendingStore
from collector.site_code_gui import SiteCodeControls


@unittest.skipUnless(os.name == 'nt', 'Native Windows DPAPI required')
class NativeSiteCodeSmoke(unittest.TestCase):
    def test_site_code_button_dpapi_and_lost_response(self):
        import tkinter as tk
        from tkinter import ttk
        with tempfile.TemporaryDirectory() as directory, patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
            root = tk.Tk()
            root.withdraw()
            path = Path(directory)
            app = SimpleNamespace(window=root, network_area=ttk.Frame(root), settings=ttk.Frame(root),
                queue=SimpleNamespace(path=path/'pending.sqlite3'),
                store=CredentialStore(path/'credentials.dpapi'), busy=False,
                pairing=None, tailer=None, uploader=None, recovered_token=None,
                updates=None, upload_enabled=False, pair=lambda: None,
                show_identity=lambda: None)
            control = SiteCodeControls(app)
            app.site_codes = control
            release = threading.Event()
            requests = []
            credential = {'access_token':'SYNTHETIC_'*8, 'token_type':'Bearer',
                'scope':'gamelogs:write', 'installation_id':'a'*32,
                'characters':[{'id':42,'name':'Synthetic Pilot'}],
                'expires_at':'2099-01-01T00:00:00Z'}
            class Transport:
                def post(self, route, payload):
                    requests.append(dict(payload))
                    if len(requests) == 1:
                        release.wait(5)
                        raise ConnectionResetError()
                    return 200, credential
            control.redemption.http = Transport()
            try:
                code = 'ABCD-EFGH-JKLM-NPQR-STUV'
                control.code.set(code)
                control.button.invoke()
                self.assertTrue(control.busy)
                saved = PendingStore(control.pending.path).load()
                assert saved is not None
                self.assertEqual(saved['scope'], 'gamelogs:write')
                self.assertNotIn(code.encode(), control.pending.path.read_bytes())
                self.assertNotIn(saved['verifier'].encode(), control.pending.path.read_bytes())
                release.set()
                deadline = time.monotonic()+10
                while control.busy and time.monotonic()<deadline:
                    root.update()
                    time.sleep(.02)
                self.assertFalse(control.busy)
                self.assertTrue(control.uncertain)
                control.auto_retry = False
                control.next_try = 0
                control.refresh()
                control.button.invoke()
                deadline = time.monotonic()+10
                while control.busy and time.monotonic()<deadline:
                    root.update()
                    time.sleep(.02)
                self.assertFalse(control.busy)
                self.assertFalse(control.uncertain)
                self.assertEqual(requests, [saved,saved])
                self.assertEqual(CredentialStore(app.store.path).load(), credential)
                self.assertFalse(control.pending.path.exists())
                self.assertIsNone(app.tailer)
                self.assertFalse(app.upload_enabled)
                self.assertIn('Привязка сохранена',control.label.cget('text'))
            finally:
                release.set()
                for callback in root.tk.call('after','info'):
                    root.after_cancel(callback)
                root.destroy()
                gc.collect()

if __name__ == '__main__':
    unittest.main()
