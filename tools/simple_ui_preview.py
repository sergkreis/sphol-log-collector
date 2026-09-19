"""Actual Tk screenshots for the 0.3.11 simple EVE login candidate."""
import gc
from pathlib import Path
import socket
import sys
import tempfile
import tkinter as tk
from unittest.mock import patch

from collector.core import PendingQueue
from collector.network_gui import ConnectedApp
from collector.transport import Uploader


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def assert_main_bounds(window, app):
    window.update_idletasks()
    root_widget = getattr(getattr(app, 'modern', None), 'root', app.frame)
    assert root_widget.winfo_width() <= window.winfo_width()
    assert root_widget.winfo_height() <= window.winfo_height()


CREDENTIALS = dict(access_token='s'*43, token_type='Bearer', scope='combat:write',
                   installation_id='a'*32, expires_at='2099-01-01T00:00:00Z',
                   characters=[dict(id=42, name='Synthetic Pilot')])


def capture(window, destination, name):
    from PIL import ImageGrab
    window.update_idletasks()
    window.update()
    x, y = window.winfo_rootx(), window.winfo_rooty()
    path = destination / (name + '.png')
    ImageGrab.grab(bbox=(x, y, x + window.winfo_width(), y + window.winfo_height())).save(path)
    return path


class DummyTailer:
    unattributed_files = 0
    files = []
    def poll(self):
        return 0
    def stop(self):
        pass


def build_app(temp):
    root = Path(temp)
    logs = root / 'Gamelogs'
    logs.mkdir()
    q = PendingQueue(root / 'pending.sqlite3')
    w = tk.Tk()
    errors = []
    w.report_callback_exception = lambda *args: errors.append(args)
    with patch.object(w, 'after', return_value='after'), \
            patch('collector.network_gui.CredentialStore.load', return_value=None), \
            patch('collector.connection_status.probe', return_value=None), \
            patch('webbrowser.open', return_value=True), \
            patch('collector.site_code_gui.PendingStore.load', return_value={'scope': 'combat:write'}):
        app = ConnectedApp(w, logs, q)
    return w, app, q, errors


def render(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp, \
            patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')), \
            patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('Network forbidden')):
        w, app, q, errors = build_app(temp)
        try:
            capture(w, destination, 'primary')
            app.uploader = Uploader(dict(CREDENTIALS))
            app.show_identity()
            app.modern.show('main')
            capture(w, destination, 'bound-idle')
            app.tailer = DummyTailer()
            app.upload_enabled = True
            app.connection_status.state = 'connected'
            app.modern.show('main')
            capture(w, destination, 'active')
            app.uploader.failures = 1
            app.connection_status.state = 'offline'
            app.modern.show('main')
            capture(w, destination, 'offline')
            app.tailer = None
            app.uploader.failures = 0
            app.connection_status.state = 'checking'
            app.modern.show('settings')
            capture(w, destination, 'settings')
            app.modern.show('help')
            capture(w, destination, 'help')
            app.modern.show('main')
            capture(w, destination, 'waiting')
            assert not errors, errors
            print('PASS actual Tk modern candidate screenshots:', ', '.join(p.name for p in sorted(destination.glob('*.png'))))
        finally:
            app.expanded.close()
            app.legacy.close()
            q.close()
            w.destroy()
            del app, w
            gc.collect()


if __name__ == '__main__':
    render(sys.argv[1])
