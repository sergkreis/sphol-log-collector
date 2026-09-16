"""Isolated real-Tk synthetic fixtures; sockets blocked, no Windows acceptance."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import socket
import sys
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch
from collector.core import PendingQueue
from collector.network_gui import ConnectedApp
from collector.transport import Uploader


class SyntheticHTTP:
    offline = False

    def post(self, path, payload, token=None):
        assert path in ('/api/collector/v1/events', '/api/collector/v2/events')
        if self.offline:
            raise OSError('Synthetic offline')
        return 200, {'accepted_ids': [e['id'] for e in payload['events']], 'rejected': []}


def render(destination):
    from PIL import ImageGrab
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for scene in ('active-expanded', 'needs-approval', 'offline', 'settings'):
        # Every screenshot starts from fresh queues, app, workers and updater.
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')), \
                patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('Network forbidden')), \
                patch('collector.network_gui.CredentialStore.load', return_value=None), \
                patch('collector.connection_status.probe', return_value=None), \
                patch('webbrowser.open'):
            root = Path(temp)
            logs = root / 'Gamelogs'
            logs.mkdir()
            queue = PendingQueue(root / 'pending.sqlite3')
            for i in range(4):
                queue.put(f'legacy-{i}', {'listener': 'unknown'})
            legacy = list(queue.db.execute('SELECT * FROM pending'))
            window = tk.Tk()
            errors = []
            window.report_callback_exception = lambda *args: errors.append(args)
            with patch.object(window, 'after'):
                app = ConnectedApp(window, logs, queue)
                try:
                    ttk.Label(app.frame, text='PREVIEW · синтетические данные · сеть заблокирована', style='Small.TLabel').pack(before=app.header, fill='x', pady=(0, 8))
                    http, expanded_http = SyntheticHTTP(), SyntheticHTTP()
                    credentials = {'access_token': 'synthetic-token-' * 3, 'token_type': 'Bearer',
                        'scope': 'gamelogs:write', 'installation_id': 'a' * 32,
                        'characters': [{'id': 1, 'name': 'Synthetic Pilot'}], 'expires_at': '2099-01-01T00:00:00Z'}
                    app.uploader = Uploader(credentials, http=http)
                    app.show_identity()
                    app.refresh_controls()
                    # Consent decline must neither pair nor start observations.
                    with patch('collector.expanded_gui.messagebox.askyesno', return_value=False), patch('collector.expanded_gui.Pairing') as pairing:
                        app.expanded.approve_button.invoke()
                        pairing.assert_not_called()
                    assert app.expanded.tailer is None
                    if scene == 'needs-approval':
                        app.uploader = Uploader({**credentials, 'scope': 'combat:write'}, http=http)
                        with patch.object(app, 'pair') as pair:
                            app.main_button.invoke()
                        pair.assert_called_once()
                        assert app.tailer is None and app.expanded.tailer is None
                        app.uploader = Uploader(credentials, http=http)
                    app.main_button.invoke()
                    app.expanded.uploader.http = expanded_http
                    assert app.tailer and app.upload_enabled
                    assert app.expanded.tailer
                    with patch('collector.update_gui.os.name', 'nt'), patch('collector.update_gui.sys.frozen', True, create=True):
                        app.updates.refresh_button()
                        assert app.updates.button.instate(['disabled'])
                        with patch('collector.update_gui.messagebox.showwarning') as warning:
                            app.updates.check()
                            warning.assert_called_once()
                    when = (datetime.now(timezone.utc) + timedelta(seconds=2)).strftime('%Y.%m.%d %H:%M:%S')
                    text = 'Listener: Synthetic Pilot\n-----\n'
                    text += ''.join(f'[ {when} ] (combat) Synthetic damage {i}\n' for i in range(12))
                    text += ''.join(f'[ {when} ] (notify) {s}\n' for s in ('Переход в варп-режим по приказу Synthetic Commander', 'Цель неуязвима.', 'private unknown'))
                    (logs / 'synthetic.txt').write_text(text, encoding='utf-8')
                    app.tick()

                    def settle(owner, tick):
                        clock = [time.monotonic()]
                        owner.uploader.clock = lambda: clock[0]
                        tick()
                        if not owner.busy:
                            assert owner.uploader.flush_at is not None
                            clock[0] += 10  # Deterministically cross the coalescing deadline.
                            tick()
                        deadline = time.monotonic() + 3
                        while owner.busy and owner.results.empty() and time.monotonic() < deadline:
                            time.sleep(.01)
                        assert not owner.results.empty(), 'worker timed out'
                        tick()

                    settle(app, app.network_tick)
                    assert app.dashboard.confirmed == 12
                    ack = app.dashboard.ack_at
                    app.network_tick()
                    assert app.dashboard.ack_at == ack
                    settle(app.expanded, app.expanded.tick)
                    assert app.expanded.ack_count == 2
                    assert app.expanded.queue.count() == 0
                    if scene == 'offline':
                        # Successful combat ACK cannot mask a failed observation stream.
                        expanded_http.offline = True
                        (logs / 'offline.txt').write_text('Listener: Synthetic Pilot\n-----\n' + f'[ {when} ] (notify) Цель неуязвима.\n', encoding='utf-8')
                        settle(app.expanded, app.expanded.tick)
                        assert app.expanded.queue.count() == 1
                        assert app.expanded.ack_count == 2
                        assert 'не отправляются: наблюдения' in app.dashboard.heading.cget('text')
                        # Conversely observation ACK cannot mask combat failure.
                        app.uploader.failures = 1
                        app.expanded.uploader.failures = 0
                        app.dashboard.refresh()
                        assert 'не отправляются: боевые' in app.dashboard.heading.cget('text')
                        app.uploader.failures = 0
                        app.expanded.uploader.failures = 1
                    app.expanded.refresh_summary()
                    window.update()
                    assert not app.expanded.approve_button.winfo_viewable()
                    assert [int(x.cget('text')) for x in app.dashboard.metrics] == ([15, 14, 1] if scene == 'offline' else [14, 14, 0])
                    assert list(queue.db.execute('SELECT * FROM pending')) == legacy
                    if scene == 'settings':
                        app.settings_button.invoke()
                        window.update()
                        assert app.settings.winfo_viewable()
                        assert app.settings_canvas.yview()[1] < 1
                        app.settings_canvas.yview_moveto(1)
                        window.update()
                        assert app.settings_canvas.yview()[1] == 1
                    window.update()
                    x, y = window.winfo_rootx(), window.winfo_rooty()
                    assert window.winfo_width() <= 780 and window.winfo_height() <= 750
                    for widget in (app.identity, app.settings_button, app.footer, app.open_site, app.updates.button) + (() if scene == 'settings' else (app.main_button,)):
                        assert widget.winfo_viewable(), str(widget)
                        assert widget.winfo_rooty() + widget.winfo_height() <= y + window.winfo_height(), str(widget)
                        assert widget.winfo_height() >= widget.winfo_reqheight(), str(widget)
                    assert 'OSError' not in app.updates.label.cget('text')
                    ImageGrab.grab(bbox=(x, y, x + window.winfo_width(), y + window.winfo_height())).save(destination / (scene + '.png'))
                    app.main_button.invoke()
                    assert app.tailer is None and not app.upload_enabled
                    assert app.expanded.tailer is None and not app.expanded.enabled
                    assert list(queue.db.execute('SELECT * FROM pending')) == legacy
                    if scene == 'active-expanded':
                        app.main_button.invoke()
                        with patch.object(app.tailer, 'poll', side_effect=OSError('synthetic read error')):
                            app.tick()
                        assert app.tailer is None and not app.upload_enabled
                        assert app.expanded.tailer is None and not app.expanded.enabled
                        assert 'Не удалось читать журналы' in app.dashboard.heading.cget('text')
                    assert errors == [], errors
                    print(f'PASS isolated {scene}: bounds, consent, ACK, stop, update guard, legacy preservation')
                finally:
                    app.expanded.close()
                    app.legacy.close()
                    queue.close()
                    window.destroy()
            # Tk variables must be finalized on this thread, never by a later
            # HTTP integration worker's cyclic garbage collection.
            del app, window, settle
            import gc
            gc.collect()


if __name__ == '__main__':
    render(sys.argv[1])
