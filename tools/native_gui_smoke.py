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
import tkinter as tk
import unittest
from unittest.mock import patch

from collector.core import PendingQueue
from collector.network_gui import ConnectedApp


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
