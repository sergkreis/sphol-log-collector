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

import unittest
from unittest.mock import patch

from collector.core import PendingQueue
from collector.network_gui import ConnectedApp
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
                    self.assertIn('NOT CONNECTED', app.connection.cget('text'))
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

                    def button(text):
                        def descendants(widget):
                            for child in widget.winfo_children():
                                yield child
                                yield from descendants(child)
                        return next(w for w in descendants(window)
                                    if w.winfo_class() == 'TButton' and w.cget('text') == text)

                    button('Start capture').invoke()
                    self.assertIsNotNone(app.tailer)
                    # Future whole second avoids filtering by the session's microseconds.
                    stamp = datetime.now(timezone.utc).timestamp() + 2
                    stamp = datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y.%m.%d %H:%M:%S')
                    with log.open('a', encoding='utf-8') as stream:
                        stream.write(f'[ {stamp} ] (combat) Synthetic smoke damage\n')
                    # Exercise scheduled Tk capture and network-idle callbacks.
                    window.after(1200, window.quit)
                    window.mainloop()
                    self.assertEqual(queue.count(), 1)
                    self.assertIn('1 events', app.pending.get())
                    button('Stop capture').invoke()
                    self.assertIsNone(app.tailer)
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
