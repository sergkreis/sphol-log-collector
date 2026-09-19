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
from collector.network_gui import ConnectedApp, Snapshot
from collector.transport import Pairing


def visible_button(root, text):
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    return next(w for w in descendants(root)
                if w.winfo_class() in ('Button', 'TButton') and w.cget('text') == text and w.winfo_viewable())


class NativeGuiSmoke(unittest.TestCase):
    def tearDown(self):
        # Collect destroyed Tk cycles on their owning thread, before later HTTP
        # fixture workers can trigger cyclic GC and Tcl_AsyncDelete aborts.
        import gc
        gc.collect()

    def test_diagnostics_initialization_failure_does_not_block_capture(self):
        from collector.gui import App
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Gamelogs'
            root.mkdir()
            with closing(PendingQueue(Path(temp) / 'pending.sqlite3')) as queue:
                window = tk.Tk()
                try:
                    with patch('collector.gui.initialize', side_effect=PermissionError('private path secret')):
                        app = App(window, root, queue)
                    app.start()
                    self.assertIsNotNone(app.tailer)
                    with patch('collector.diagnostics._sink') as sink:
                        sink.record.side_effect = OSError('private token secret')
                        app.tick()
                        self.assertIsNotNone(app.tailer)
                    app.stop()
                    self.assertEqual(queue.count(), 0)
                finally:
                    window.destroy()

    def test_capture_stop_close_and_durable_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'Gamelogs'
            root.mkdir()
            log = root / 'synthetic.txt'
            log.write_text('Listener: Synthetic Smoke Pilot\n', encoding='utf-8')
            db = Path(temp) / 'pending.sqlite3'
            with closing(PendingQueue(db)) as queue, patch.object(
                socket.socket, 'connect', side_effect=AssertionError('Network forbidden in smoke test')
            ), patch('collector.connection_status.probe', return_value=None):
                window = tk.Tk()
                app = None
                callback_errors = []
                window.report_callback_exception = lambda *args: callback_errors.append(args)
                try:
                    app = ConnectedApp(window, root, queue)
                    window.update()
                    # Invoke the real export button; native/frozen runner includes this gate.
                    with tempfile.TemporaryDirectory() as exports:
                        report = Path(exports) / 'support.json'
                        window.clipboard_clear()
                        window.clipboard_append('unchanged support clipboard')
                        with patch('collector.gui.filedialog.asksaveasfilename', return_value=str(report)) as dialog:
                            app.support_button.invoke()
                            dialog.assert_called_once()
                            self.assertTrue(dialog.call_args.kwargs['initialfile'].startswith('SPHOL-support-'))
                        self.assertTrue(report.is_file())
                        import json
                        support = json.loads(report.read_text(encoding='utf-8'))
                        self.assertEqual(support['schema'], 1)
                        self.assertNotIn('Synthetic Smoke Pilot', report.read_text())
                        self.assertNotIn(str(root), report.read_text())
                        self.assertEqual(window.clipboard_get(), 'unchanged support clipboard')
                        self.assertIn('Отчёт сохранён', app.support_status.cget('text'))
                        with patch('collector.gui.filedialog.asksaveasfilename', return_value=''):
                            app.support_button.invoke()
                        self.assertIn('отменено', app.support_status.cget('text'))
                        with patch('collector.gui.filedialog.asksaveasfilename', return_value=str(db)):
                            app.support_button.invoke()
                        self.assertIn('Не удалось', app.support_status.cget('text'))
                        self.assertEqual(queue.count(), 0)
                    # Initialization and independent default-off consent in real Tk.
                    self.assertFalse(app.expanded.enabled)
                    self.assertIsNone(app.expanded.tailer)
                    self.assertEqual(app.expanded.queue.count(), 0)
                    self.assertFalse(app.expanded.start_button.winfo_viewable())
                    self.assertFalse(app.settings.winfo_viewable())
                    self.assertFalse(app.main_button.winfo_viewable())
                    self.assertTrue(app.modern.root.winfo_viewable())
                    self.assertTrue(visible_button(window, 'Войти через EVE').winfo_viewable())
                    self.assertEqual(app.expanded.approve_button.cget('text'), 'Разрешить наблюдения')
                    with patch('collector.expanded_gui.messagebox.askyesno', return_value=False), patch.object(app.expanded, 'work') as work:
                        app.expanded.approve_button.invoke()
                        work.assert_not_called()
                    self.assertIsNone(app.expanded.pairing)
                    self.assertFalse(app.expanded.enabled)
                    self.assertTrue(window.winfo_viewable())
                    visible_button(window, 'Настройки').invoke()
                    window.update()
                    self.assertTrue(app.modern.root.winfo_viewable())
                    self.assertTrue(visible_button(window, 'Обновить приложение').winfo_viewable())
                    self.assertTrue(visible_button(window, 'Сменить персонажа').winfo_viewable())
                    self.assertTrue(visible_button(window, 'Помощь').winfo_viewable())
                    visible_button(window, '← Назад').invoke()
                    window.update()
                    self.assertFalse(app.settings.winfo_viewable())
                    # Actual update button/result path, with only transport mocked.
                    app.updates.button.config(state='normal')
                    with patch('collector.updater.prepare', return_value=None) as prepare:
                        app.updates.button.invoke()
                        import time
                        deadline = time.monotonic() + 3
                        while app.updates.results.empty() and time.monotonic() < deadline:
                            time.sleep(.01)
                        app.updates.poll()
                        prepare.assert_called_once()
                    self.assertIn('более новой стабильной версии нет', app.updates.label.cget('text'))
                    app.updates.results.put((None, 'OSError'))
                    app.updates.poll()
                    self.assertIn('файлы программы не изменены', app.updates.label.cget('text'))
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
                    from collector.transport import Uploader
                    app.uploader = Uploader({
                        'scope': 'gamelogs:write', 'access_token': 'a'*43,
                        'installation_id': 'b'*32, 'token_type': 'Bearer',
                        'characters': [{'id': 42, 'name': 'Synthetic Smoke Pilot'}],
                        'expires_at': '2099-01-01T00:00:00Z'}, clock=lambda: 0)
                    app.show_identity()
                    app.refresh_controls()
                    window.update()
                    identity = app.identity.cget('text')
                    ack = app.last_ack.cget('text')
                    self.assertTrue(app.modern.root.winfo_viewable())
                    self.assertTrue(visible_button(window, 'Начать сбор').winfo_viewable())
                    self.assertFalse(app.site_codes.frame.winfo_viewable())
                    self.assertTrue(app.pair_button.instate(['disabled']))
                    self.assertFalse(app.code_frame.winfo_ismapped())
                    self.assertTrue(app.modern.root.winfo_viewable())
                    self.assertTrue(visible_button(window, 'Начать сбор').winfo_viewable())
                    self.assertTrue(visible_button(window, 'Настройки').winfo_viewable())
                    self.assertFalse(app.upload_enabled)
                    visible_button(window, 'Начать сбор').invoke()
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
                    self.assertIn('Последнее подтверждение сервера', confirmed)
                    self.assertEqual(app.dashboard.confirmed, 1)
                    app.network_tick()
                    self.assertEqual(app.last_ack.cget('text'), confirmed)
                    self.assertEqual(app.identity.cget('text'), identity)
                    app.main_button.invoke()
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
                    app.start()
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
                    self.assertEqual(app.local_capture.count, 1)
                    self.assertEqual(queue.count(), 1)
                    self.assertIn('Событий на компьютере: 1', app.pending.get())
                    app.main_button.invoke()
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
                        for callback in window.tk.call('after', 'info'):
                            window.after_cancel(callback)
                        app.close()
                    with self.assertRaises(sqlite3.ProgrammingError):
                        queue.count()
                    self.assertEqual(callback_errors, [])
                finally:
                    if app is not None:
                        app.expanded.close()
                        app.legacy.close()
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
