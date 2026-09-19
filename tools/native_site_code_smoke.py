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
    def test_layout_independent_paste_event_route(self):
        import tkinter as tk
        from tkinter import ttk
        from collector.clipboard import bind_paste
        import ctypes
        from ctypes import wintypes as w

        # SendInput exercises Windows translation and Tk dispatch, not a fabricated
        # keysym (Tk cannot translate Cyrillic_em under an English-only HKL).
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        signatures = {
            'LoadKeyboardLayoutW': ([w.LPCWSTR, w.UINT], w.HANDLE),
            'ActivateKeyboardLayout': ([w.HANDLE, w.UINT], w.HANDLE),
            'GetKeyboardLayout': ([w.DWORD], w.HANDLE),
            'GetKeyboardState': ([ctypes.POINTER(w.BYTE)], w.BOOL),
            'SetKeyboardState': ([ctypes.POINTER(w.BYTE)], w.BOOL),
            'GetAsyncKeyState': ([ctypes.c_int], w.SHORT),
            'GetAncestor': ([w.HWND, w.UINT], w.HWND),
            'GetForegroundWindow': ([], w.HWND),
            'SetForegroundWindow': ([w.HWND], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(user32, name)
            function.argtypes, function.restype = args, result

        class Keyboard(ctypes.Structure):
            _fields_ = [('vk', w.WORD), ('scan', w.WORD), ('flags', w.DWORD),
                        ('time', w.DWORD), ('extra', ctypes.c_size_t)]

        class Mouse(ctypes.Structure):
            _fields_ = [('dx', w.LONG), ('dy', w.LONG), ('data', w.DWORD),
                        ('flags', w.DWORD), ('time', w.DWORD),
                        ('extra', ctypes.c_size_t)]

        class Payload(ctypes.Union):
            _fields_ = [('keyboard', Keyboard), ('mouse', Mouse)]

        class Input(ctypes.Structure):
            _anonymous_ = ('payload',)
            _fields_ = [('type', w.DWORD), ('payload', Payload)]

        user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
        user32.SendInput.restype = w.UINT

        def send(*keys):
            inputs = (Input * len(keys))()
            for item, (vk, up) in zip(inputs, keys):
                item.type = 1  # INPUT_KEYBOARD, virtual keys, not Unicode injection.
                item.keyboard = Keyboard(vk, 0, 2 if up else 0, 0, 0)
            self.assertEqual(user32.SendInput(len(inputs), inputs, ctypes.sizeof(Input)),
                             len(inputs), 'SendInput rejected by desktop/UIPI')

        root = tk.Tk()
        old_layout = user32.GetKeyboardLayout(0)
        old_state = (w.BYTE * 256)()
        state_saved = bool(user32.GetKeyboardState(old_state))
        injected = False

        def pump_until(predicate, label):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                root.update()
                if predicate():
                    return
                time.sleep(.01)
            self.fail(label)

        try:
            self.assertTrue(old_layout)
            self.assertTrue(state_saved)
            # Do not interfere with a real held key. CI must own an idle desktop.
            for vk in (0x10, 0x11, 0x12, 0x56, 0x5B, 0x5C):
                self.assertFalse(user32.GetAsyncKeyState(vk) & 0x8000,
                                 f'Key {vk} already held on runner')
            entry = ttk.Entry(root)
            entry.pack()
            presses = []
            bind_paste(entry)
            production_binding = entry.bind('<Control-KeyPress>')
            production_tags = entry.bindtags()
            # Observe before the widget tag: Tk may choose <<Paste>> instead of
            # the widget's physical binding, and production paste returns break.
            # A separate tag sees the native event without replacing either route.
            observer_tag = f'NativePasteObserver{entry}'
            entry.bindtags((observer_tag,) + production_tags)
            entry.bind_class(observer_tag, '<Control-KeyPress>',
                             lambda event: presses.append((event.keycode, event.state))
                             if event.keycode == 0x56 else None)
            self.assertEqual(entry.bind('<Control-KeyPress>'), production_binding)
            self.assertEqual(entry.bindtags()[1:], production_tags)
            root.update()
            hwnd = user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT
            self.assertTrue(hwnd)
            root.lift()
            user32.SetForegroundWindow(hwnd)
            entry.focus_force()
            pump_until(lambda: user32.GetForegroundWindow() == hwnd
                       and root.focus_get() == entry, 'Own Tk window not foreground')
            root.clipboard_clear()
            root.clipboard_append('SYNTHETIC')
            for klid, language, character in (('00000409', 0x0409, 'v'),
                                              ('00000419', 0x0419, 'м')):
                with self.subTest(layout=klid):
                    layout = user32.LoadKeyboardLayoutW(klid, 0)
                    self.assertTrue(layout, f'Cannot load {klid}')
                    # No KLF_SETFORPROCESS: change only this Tk/UI thread.
                    self.assertTrue(user32.ActivateKeyboardLayout(layout, 0))
                    self.assertEqual(user32.GetKeyboardLayout(0), layout)
                    self.assertEqual(layout & 0xffff, language)
                    # MAPVK_VK_TO_CHAR maps A..Z to Latin capitals regardless
                    # of HKL (Microsoft's documented semantics). Prove the layout
                    # with actual plain native input below, not that API.
                    self.assertEqual(user32.GetForegroundWindow(), hwnd)
                    entry.delete(0, 'end')
                    injected = True
                    send((0x56, False), (0x56, True))
                    pump_until(lambda: bool(entry.get()), 'Plain VK_V not delivered')
                    self.assertEqual(entry.get().lower(), character,
                                     'Actual Tk character disagrees with active HKL')
                    entry.delete(0, 'end')
                    entry.insert(0, 'replace')
                    entry.selection_range(0, 'end')
                    presses.clear()
                    send((0x11, False), (0x56, False), (0x56, True), (0x11, True))
                    pump_until(lambda: bool(presses), 'Ctrl+VK_V not delivered to Tk')
                    # Drain release/virtual events too: a second paste must fail.
                    deadline = time.monotonic() + .2
                    while time.monotonic() < deadline:
                        root.update()
                        time.sleep(.01)
                    self.assertEqual(len(presses), 1, presses)
                    self.assertEqual(presses[0][0], 0x56)
                    self.assertTrue(presses[0][1] & 4)
                    self.assertEqual(entry.get(), 'SYNTHETIC', klid)
                    self.assertEqual(root.clipboard_get(), 'SYNTHETIC')
        finally:
            try:
                if injected:
                    send((0x56, True), (0x11, True))
                    root.update()
            finally:
                try:
                    if old_layout:
                        self.assertTrue(user32.ActivateKeyboardLayout(old_layout, 0))
                        self.assertEqual(user32.GetKeyboardLayout(0), old_layout)
                    if state_saved:
                        self.assertTrue(user32.SetKeyboardState(old_state))
                finally:
                    root.destroy()
                    gc.collect()

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
