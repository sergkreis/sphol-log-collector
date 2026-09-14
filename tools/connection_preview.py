"""Real Tk, synthetic connection states, outbound sockets forbidden."""
from pathlib import Path
import socket
import tempfile
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch
from collector.core import PendingQueue
from collector.network_gui import ConnectedApp
from collector.transport import Uploader
from collector.connection_status import ConnectionStatus
from tests.test_connection_status import CREDS


def render(destination):
    from PIL import ImageGrab
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for scene in ('connected-no-events', 'offline', 'denied', 'one-stream-failure', 'stopped'):
        with tempfile.TemporaryDirectory() as temp, patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline fixture')):
            root = Path(temp); logs = root / 'logs'; logs.mkdir()
            queue = PendingQueue(root / 'queue.db')
            window = tk.Tk()
            with patch.object(window, 'after'):
                app = ConnectedApp(window, logs, queue)
                app.uploader = Uploader(CREDS)
                app.show_identity()
                app.connection_status = ConnectionStatus(check=lambda _: None)
                ttk.Label(app.identity_area, text='СИНТЕТИЧЕСКИЙ PREVIEW · сеть заблокирована', style='Small.TLabel').pack()
                app.main_button.invoke()
                app.connection_status.worker.join(1)
                app.refresh_controls()
                if scene in ('offline', 'denied'):
                    app.connection_status.state = scene
                    app.connection_status.next_try = float('inf')
                if scene == 'one-stream-failure':
                    app.expanded.problem = True
                if scene == 'stopped':
                    app.main_button.invoke()
                app.refresh_controls(); app.dashboard.refresh()
                if scene in ('connected-no-events', 'one-stream-failure'):
                    assert 'Подключено к SPHOL' in app.connection_badge.cget('text')
                    assert str(app.connection_badge.cget('foreground')) == '#80d8a0'
                if scene == 'connected-no-events':
                    assert app.last_ack.cget('text') == 'Новых событий пока нет'
                if scene == 'one-stream-failure':
                    assert 'не отправляются: наблюдения' in app.dashboard.heading.cget('text')
                app.dashboard.acknowledge(['synthetic-ack'])
                ack = app.dashboard.ack_at
                app.refresh_controls(); app.dashboard.refresh()
                assert app.dashboard.ack_at == ack
                # Restore zero-event view without inventing ACKs in the screenshot.
                app.dashboard.ack_at = None; app.dashboard.confirmed = 0
                app.dashboard.refresh()
                app.expanded.refresh_summary()
                window.update()
                for widget in (app.identity, app.connection_badge, app.main_button):
                    assert widget.winfo_viewable()
                    assert widget.winfo_height() >= widget.winfo_reqheight()
                x, y = window.winfo_rootx(), window.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x+window.winfo_width(), y+window.winfo_height())).save(destination / (scene+'.png'))
                app.stop(); app.expanded.close(); app.legacy.close(); queue.close()
                window.destroy()
        print('PASS', scene)

if __name__ == '__main__':
    import sys
    render(sys.argv[1])
