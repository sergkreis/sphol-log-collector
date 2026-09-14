"""Real Tk synthetic connection fixtures shared by preview and frozen smoke."""
from pathlib import Path
import socket
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from collector.core import PendingQueue
from collector.network_gui import ConnectedApp
from collector.transport import Uploader
from collector.connection_status import ConnectionStatus, TTL, LABELS
from tools.native_single_smoke import credentials, evidence

CREDS = credentials('Synthetic Pilot', 42, 's')


def render(destination=None):
    if destination is not None:
        from PIL import ImageGrab
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
    for scene in ('connected-no-events', 'offline', 'denied', 'stale', 'checking', 'one-stream-failure', 'stopped'):
        with tempfile.TemporaryDirectory() as temp, patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline fixture')):
            root = Path(temp); logs = root / 'logs'; logs.mkdir()
            queue = PendingQueue(root / 'queue.db')
            window = tk.Tk()
            app = None
            try:
                with patch.object(window, 'after'), patch('collector.connection_status.probe', return_value=None):
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
                    if scene == 'stale':
                        app.connection_status.fresh = app.connection_status.clock() - TTL - 1
                        app.connection_status.next_try = float('inf')
                    if scene == 'one-stream-failure':
                        app.expanded.problem = True
                    if scene == 'stopped':
                        app.main_button.invoke()
                    app.refresh_controls(); app.dashboard.refresh()
                    expected = {'connected-no-events': 'connected', 'one-stream-failure': 'connected', 'stale': 'unknown'}.get(scene, scene)
                    if scene == 'checking':
                        with patch.object(app.connection_status, 'tick', return_value='checking'):
                            app.refresh_controls()
                    assert app.connection_badge.cget('text') == LABELS[expected]
                    colors = {'connected': '#80d8a0', 'offline': '#ef8791', 'denied': '#ef8791', 'checking': '#e8be75'}
                    assert str(app.connection_badge.cget('foreground')) == colors.get(expected, '#bcc5d3')
                    if scene == 'connected-no-events':
                        assert app.last_ack.cget('text') == 'Новых событий пока нет'
                        assert app.dashboard.ack_at is None
                    if scene == 'one-stream-failure':
                        assert 'не отправляются: наблюдения' in app.dashboard.heading.cget('text')
                    app.dashboard.acknowledge(['synthetic-ack'])
                    ack = app.dashboard.ack_at
                    app.refresh_controls(); app.dashboard.refresh()
                    assert app.dashboard.ack_at == ack
                    if scene == 'one-stream-failure':
                        assert 'не отправляются: наблюдения' in app.dashboard.heading.cget('text')
                    # Restore zero-event screenshot; no fabricated production ACK.
                    app.dashboard.ack_at = None; app.dashboard.confirmed = 0
                    app.dashboard.refresh(); app.expanded.refresh_summary()
                    if scene == 'checking':
                        with patch.object(app.connection_status, 'tick', return_value='checking'):
                            app.refresh_controls()
                    window.update()
                    for widget in (app.identity, app.connection_badge, app.main_button):
                        assert widget.winfo_viewable()
                        assert widget.winfo_height() >= widget.winfo_reqheight()
                    if destination is not None:
                        x, y = window.winfo_rootx(), window.winfo_rooty()
                        ImageGrab.grab(bbox=(x, y, x+window.winfo_width(), y+window.winfo_height())).save(destination / (scene+'.png'))
                    else:
                        evidence(window, 'connection-' + scene)
            finally:
                if app:
                    app.stop(); app.expanded.close(); app.legacy.close()
                queue.close(); window.destroy()
        print('PASS', scene)


class NativeConnectionSmoke(unittest.TestCase):
    def test_connection_states_and_ack_preservation(self):
        render()


if __name__ == '__main__':
    import sys
    render(sys.argv[1])
