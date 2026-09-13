"""Render the actual Tk UI with synthetic state, no network or user files.
Usage: xvfb-run -a /usr/bin/python3 -m tools.gui_preview /tmp/sphol-preview
Pillow is only needed for this developer screenshot tool, not the collector.
"""
from pathlib import Path
import socket
import sys
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch
from collector.core import PendingQueue
from collector.network_gui import ConnectedApp


def render(destination):
    from PIL import ImageGrab
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp, patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
        root = Path(temp)
        logs = root / 'Gamelogs'
        logs.mkdir()
        queue = PendingQueue(root / 'pending.sqlite3')
        window = tk.Tk()
        errors = []
        window.report_callback_exception = lambda *args: errors.append(args)
        app = ConnectedApp(window, logs, queue)
        try:
            window.title('SPHOL 0.2.1 — СИНТЕТИЧЕСКИЙ ПРИМЕР')
            app.uploader = SimpleNamespace(paused=False, credentials={'characters': [{'name': 'Тестовый Пилот'}], 'expires_at': '2099-01-01'})
            app.show_identity()
            app.refresh_controls()
            for i in range(4):
                queue.put(str(i), {'listener': 'unknown'})
            app.tick()
            before = list(queue.db.execute('SELECT * FROM pending'))
            def shot(name):
                window.update()
                x, y = window.winfo_rootx(), window.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x + window.winfo_width(), y + window.winfo_height())).save(destination / (name + '.png'))
            assert 'Без персонажа: 4' in app.pending.get()
            assert not app.expanded.enabled and not app.settings.winfo_ismapped()
            shot('idle')
            app.main_button.invoke()
            assert app.tailer and app.upload_enabled
            shot('active')
            with patch('collector.update_gui.messagebox.showwarning') as warning:
                app.updates.check()
                warning.assert_called_once()
            app.results.put(('upload', None, 'OSError'))
            app.network_tick()
            assert not app.upload_enabled
            assert 'успех не подтверждён' in app.connection.cget('text')
            shot('error')
            app.main_button.invoke()
            assert app.tailer is None
            # Enable the platform-gated control only inside this synthetic test.
            # Invoke the actual button and worker/result path; no update is applied.
            app.updates.button.config(state='normal')
            with patch('collector.updater.prepare', return_value=None) as prepare:
                app.updates.button.invoke()
                deadline = time.monotonic() + 3
                while app.updates.results.empty() and time.monotonic() < deadline:
                    time.sleep(.01)
                app.updates.poll()
                prepare.assert_called_once()
            assert 'более новой стабильной версии нет' in app.updates.label.cget('text')
            shot('update')
            with patch('collector.updater.prepare', side_effect=OSError('synthetic')):
                app.updates.button.invoke()
                deadline = time.monotonic() + 3
                while app.updates.results.empty() and time.monotonic() < deadline:
                    time.sleep(.01)
                app.updates.poll()
            assert 'файлы программы не изменены' in app.updates.label.cget('text')
            shot('update-error')
            app.settings_button.invoke()
            window.update()
            assert app.settings.winfo_viewable()
            assert not app.expanded.start_button.winfo_viewable()
            shot('settings')
            app.settings_button.invoke()
            assert not app.settings.winfo_ismapped()
            assert list(queue.db.execute('SELECT * FROM pending')) == before
            assert errors == [], errors
            print('PASS: real Tk start/stop, disclosure, unknown queue, upload error, update button success/failure; six screenshots')
        finally:
            app.expanded.close()
            queue.close()
            window.destroy()


if __name__ == '__main__':
    render(sys.argv[1])
