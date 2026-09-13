"""Real Tk smoke gate, isolated synthetic logs/state; no network or user logs.

Run from the repository root: python -m tools.native_gui_smoke
A desktop display is required (xvfb-run is suitable on Linux).
"""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import tkinter as tk
from types import SimpleNamespace

import unittest
from unittest.mock import patch

from collector.core import PendingQueue
from collector.network_gui import ConnectedApp, Snapshot
from collector.transport import Pairing


class NativeGuiSmoke(unittest.TestCase):
    def test_capture_stop_close_and_durable_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Gamelogs'
            root.mkdir()
            log = root / 'synthetic.txt'
            log.write_text('Listener: Synthetic Smoke Pilot\n', encoding='utf-8')
            db = Path(temp) / 'pending.sqlite3'
            with closing(PendingQueue(db)) as queue, patch.object(
                socket.socket, 'connect', side_effect=AssertionError('Network forbidden in smoke test')
            ):
                window = tk.Tk()
                callback_errors = []
                window.report_callback_exception = lambda *args: callback_errors.append(args)
                try:
                    app = ConnectedApp(window, root, queue)
                    window.update()
                    self.assertTrue(window.winfo_viewable())
                    self.assertEqual('Персонаж не привязан', app.identity.cget('text'))
                    self.assertFalse(app.upload_enabled)
                    self.assertIsNone(app.uploader)
                    self.assertNotIn('server integration is not available', app.status.get())
                    self.assertEqual(app.code_field.get(), '')
                    self.assertTrue(app.copy_button.instate(['disabled']))
                    self.assertTrue(app.code_field.instate(['readonly']))
                    window.clipboard_clear()
                    window.clipboard_append('synthetic previous clipboard')
                    pairing = Pairing()
                    pairing.deadline = float('inf')
                    app.pairing = pairing
                    app.results.put(('pair', 'TEST-1234', None))
                    with patch.object(app, 'work'), patch('collector.network_gui.webbrowser.open'):
                        app.network_tick()
                    self.assertEqual(window.clipboard_get(), 'synthetic previous clipboard')
                    self.assertEqual(app.code_field.get(), 'TEST-1234')
                    app.code_field.selection_range(0, 'end')
                    self.assertTrue(app.code_field.selection_present())
                    app.code_field.insert(0, 'cannot edit')
                    self.assertEqual(app.code_field.get(), 'TEST-1234')
                    callback_threads = []
                    original_append = window.clipboard_append
                    def record_append(value):
                        callback_threads.append(threading.get_ident())
                        original_append(value)
                    with patch.object(window, 'clipboard_append', side_effect=record_append):
                        app.copy_button.invoke()
                    self.assertEqual(callback_threads, [threading.get_ident()])
                    self.assertEqual(window.clipboard_get(), app.code_field.get())
                    self.assertEqual(app.copy_feedback.cget('text'), 'Код скопирован')
                    pairing.deadline = 0
                    with patch.object(app, 'work'):
                        app.network_tick()
                    self.assertEqual(app.code_field.get(), '')
                    self.assertTrue(app.copy_button.instate(['disabled']))
                    self.assertEqual(window.clipboard_get(), 'TEST-1234')
                    app.pairing = None
                    app.uploader = SimpleNamespace(paused=False, credentials={
                        'characters': [{'name': 'Synthetic Smoke Pilot'}], 'expires_at': '2099-01-01T00:00:00Z'})
                    app.show_identity()
                    app.refresh_controls()
                    window.update()
                    identity = app.identity.cget('text')
                    ack = app.last_ack.cget('text')
                    self.assertTrue(app.identity.winfo_viewable())
                    self.assertTrue(app.pair_button.instate(['disabled']))
                    self.assertFalse(app.code_frame.winfo_ismapped())
                    self.assertFalse(app.upload_enabled)
                    app.enable_button.invoke()
                    with patch.object(app, 'work') as work:
                        for _ in range(3):
                            app.network_tick()
                        work.assert_not_called()
                    self.assertEqual(app.identity.cget('text'), identity)
                    self.assertEqual(app.last_ack.cget('text'), ack)
                    self.assertIn('сервер не проверялся', app.connection.cget('text'))
                    snapshot = Snapshot([])
                    snapshot.accepted = ['synthetic-ack']
                    app.results.put(('upload', (snapshot, 'Сервер подтвердил сохранение: 1 событий.'), None))
                    app.network_tick()
                    confirmed = app.last_ack.cget('text')
                    self.assertIn('Сервер подтвердил: 1', confirmed)
                    app.network_tick()
                    self.assertEqual(app.last_ack.cget('text'), confirmed)
                    self.assertEqual(app.identity.cget('text'), identity)
                    app.enable_button.invoke()
                    self.assertFalse(app.upload_enabled)

                    def button(text):
                        def descendants(widget):
                            for child in widget.winfo_children():
                                yield child
                                yield from descendants(child)
                        return next(w for w in descendants(window)
                                    if w.winfo_class() == 'TButton' and w.cget('text') == text)

                    self.assertFalse(app.local_enabled.get())
                    with patch('collector.gui.messagebox.askyesno', return_value=False):
                        app.local_toggle.invoke()
                    self.assertIsNone(app.local_capture)
                    with patch('collector.gui.messagebox.askyesno', return_value=True):
                        app.local_toggle.invoke()
                    self.assertIsNotNone(app.local_capture)
                    local_path = app.local_capture.path
                    button('Начать сбор').invoke()
                    self.assertIsNotNone(app.tailer)
                    self.assertTrue(app.start_button.instate(['disabled']))
                    self.assertTrue(app.stop_button.instate(['!disabled']))
                    # Future whole second avoids filtering by the session's microseconds.
                    stamp = datetime.now(timezone.utc).timestamp() + 2
                    stamp = datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y.%m.%d %H:%M:%S')
                    with log.open('a', encoding='utf-8') as stream:
                        stream.write(f'[ {stamp} ] (combat) Synthetic smoke damage\n')
                        stream.write(f'[ {stamp} ] (notify) Synthetic local-only notification\n')
                    # Exercise scheduled Tk capture and network-idle callbacks.
                    window.after(1200, window.quit)
                    window.mainloop()
                    self.assertEqual(app.local_capture.count, 2)
                    self.assertEqual(queue.count(), 1)
                    self.assertIn('1 событий', app.pending.get())
                    button('Остановить сбор и отправку').invoke()
                    self.assertIsNone(app.tailer)
                    self.assertIsNone(app.local_capture)
                    self.assertFalse(app.local_enabled.get())
                    self.assertTrue(local_path.exists())
                    self.assertFalse(app.upload_enabled)
                    with patch('collector.gui.messagebox.askyesno', return_value=False) as prompt:
                        app.close()
                        prompt.assert_called_once()
                    self.assertTrue(window.winfo_exists())
                    with patch('collector.gui.messagebox.askyesno', return_value=True):
                        app.close()
                    with self.assertRaises(sqlite3.ProgrammingError):
                        queue.count()
                    self.assertEqual(callback_errors, [])
                finally:
                    try:
                        window.destroy()
                    except tk.TclError:
                        pass
            with closing(PendingQueue(db)) as reopened:
                self.assertEqual(reopened.count(), 1)
                self.assertEqual(reopened.batch()[0]['listener'], 'Synthetic Smoke Pilot')
            # TemporaryDirectory cleanup itself verifies no Windows DB handle remains.


if __name__ == '__main__':
    unittest.main(verbosity=2)
