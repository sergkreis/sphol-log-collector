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
from collector.transport import Uploader, HTTPFailure


class SyntheticHTTP:
    offline = False

    def post(self, path, payload, token=None):
        assert path == '/api/collector/v1/events'
        if self.offline:
            raise OSError('Synthetic offline')
        return 200, {'accepted_ids': [e['id'] for e in payload['events']], 'rejected': []}


def render(destination):
    from PIL import ImageGrab
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for scene in ('active-expanded', 'needs-approval', 'offline', 'denied', 'settings', 'updater-feedback'):
        # Every screenshot starts from fresh queues, app, workers and updater.
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')), \
                patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('Network forbidden')), \
                patch('collector.network_gui.CredentialStore.load', return_value=None), \
                patch('collector.connection_status.probe', side_effect=(OSError('Synthetic offline') if scene == 'offline' else HTTPFailure(403) if scene == 'denied' else None), return_value=None), \
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
                    http = SyntheticHTTP()
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
                        pair.assert_not_called()
                        assert app.tailer is not None and app.expanded.tailer is None
                        app.stop()
                        app.uploader = Uploader(credentials, http=http)
                    app.main_button.invoke()
                    assert app.expanded.uploader is None
                    assert app.tailer and app.upload_enabled
                    assert app.expanded.tailer is None
                    with patch('collector.update_gui.os.name', 'nt'), patch('collector.update_gui.sys.frozen', True, create=True):
                        app.updates.refresh_button()
                        assert app.updates.button.instate(['!disabled'])
                        with patch('collector.update_gui.threading.Thread') as worker:
                            app.updates.check()
                            worker.assert_called_once()
                        app.updates.results.put((None, None))
                        app.updates.poll()
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
                    app.expanded.tick()
                    assert app.expanded.ack_count == 0
                    assert app.expanded.ack_at is None
                    assert 'Последнее подтверждение сервера' in app.last_ack.cget('text')
                    assert app.expanded.queue.count() == 0
                    if scene == 'offline':
                        http.offline = True
                        (logs / 'offline.txt').write_text('Listener: Synthetic Pilot\n-----\n' + f'[ {when} ] (combat) Synthetic offline damage\n', encoding='utf-8')
                        app.tick()
                        settle(app, app.network_tick)
                        assert app.uploader.failures == 1
                        assert app.dashboard.confirmed == 12
                        app.dashboard.refresh()
                        assert 'Не отправляются: боевые' in app.dashboard.notice.cget('text')
                    if scene == 'denied':
                        app.uploader.paused = True
                        app.upload_enabled = False
                    if app.connection_status.worker:
                        app.connection_status.worker.join(1)
                    app.refresh_controls()
                    if scene == 'denied':
                        assert app.connection_status.state == 'denied'
                        assert 'автоматически' not in app.dashboard.notice.cget('text')
                    app.expanded.refresh_summary()
                    window.update()
                    assert not app.expanded.approve_button.winfo_viewable()
                    assert not app.site_codes.frame.winfo_viewable()
                    assert not app.support_button.winfo_viewable()
                    assert not app.expanded.summary.winfo_viewable()
                    assert [int(x.cget('text')) for x in app.dashboard.metrics] == ([13, 12, 1] if scene == 'offline' else [12, 12, 0])
                    assert list(queue.db.execute("SELECT * FROM pending WHERE id LIKE 'legacy-%'")) == legacy
                    if scene in ('settings', 'updater-feedback'):
                        if hasattr(app, 'modern'):
                            app.modern.show('settings')
                        else:
                            app.settings_button.invoke()
                        window.update()
                        if hasattr(app, 'modern'):
                            assert app.modern.root.winfo_viewable()
                            app.modern.refresh()
                            window.update()
                            from tools.simple_ui_preview import descendants
                            buttons = [w for w in descendants(app.modern.root) if isinstance(w, tk.Button) and w.winfo_viewable()]
                            assert buttons, 'modern settings has no visible button'
                        else:
                            assert app.settings.winfo_viewable()
                            assert app.settings_canvas.yview()[1] < 1
                            app.settings_canvas.yview_moveto(1)
                            window.update()
                            assert app.settings_canvas.yview()[1] == 1
                            app.settings_canvas.yview_moveto(0)
                            window.update()
                            assert app.support_button.winfo_viewable()
                    if scene == 'updater-feedback':
                        app.stop()
                        with patch('collector.update_gui.updater.prepare', return_value=None) as prepare:
                            app.updates.button.config(state='normal')
                            app.updates.button.invoke()
                            deadline = time.monotonic() + 2
                            while app.updates.results.empty() and time.monotonic() < deadline:
                                time.sleep(.01)
                            app.updates.poll()
                            prepare.assert_called_once()
                        assert 'более новой стабильной версии нет' in app.updates.label.cget('text')
                    window.update()
                    from tools.simple_ui_preview import assert_main_bounds
                    assert_main_bounds(window, app)
                    x, y = window.winfo_rootx(), window.winfo_rooty()
                    assert (window.winfo_width(), window.winfo_height()) == ((680, 620) if app.settings_open else (560, 440))
                    if hasattr(app, 'modern'):
                        modern_widgets = [app.modern.root]
                        for name in ('primary_button', 'settings_button', 'conn_label', 'send_label', 'sent_label', 'pending_label'):
                            widget = getattr(app.modern, name, None)
                            if widget is not None and widget.winfo_exists():
                                modern_widgets.append(widget)
                        widgets = tuple(modern_widgets)
                    else:
                        widgets = (app.identity, app.settings_button, app.footer, app.open_site) + ((app.updates.button,) if scene in ('settings', 'updater-feedback') else (app.main_button, app.last_ack, app.dashboard.notice, app.dashboard.sent, app.dashboard.waiting))
                    for widget in widgets:
                        assert widget.winfo_viewable(), str(widget)
                        assert widget.winfo_rooty() + widget.winfo_height() <= y + window.winfo_height(), str(widget)
                        assert widget.winfo_height() >= widget.winfo_reqheight(), str(widget)
                    if app.settings_open and not hasattr(app, 'modern'):
                        # Every settings control fits horizontally at minimum size;
                        # scrolling is the only permitted clipping direction.
                        from tools.simple_ui_preview import descendants
                        window.geometry('560x440')
                        window.update()
                        for control in descendants(app.settings):
                            if isinstance(control, (ttk.Button, ttk.Entry, ttk.Checkbutton)) and control.winfo_manager():
                                assert control.winfo_width() >= control.winfo_reqwidth(), str(control)
                                assert control.winfo_height() >= control.winfo_reqheight(), str(control)
                                assert control.winfo_rootx()+control.winfo_width() <= app.settings_canvas.winfo_rootx()+app.settings_canvas.winfo_width(), str(control)
                        app.settings_canvas.yview_moveto(1)
                        window.update()
                        assert app.settings_canvas.yview()[1] == 1
                        window.geometry('680x620')
                        app.settings_canvas.yview_moveto(0)
                        window.update()
                    assert 'OSError' not in app.updates.label.cget('text')
                    ImageGrab.grab(bbox=(x, y, x + window.winfo_width(), y + window.winfo_height())).save(destination / (scene + '.png'))
                    if scene != 'updater-feedback':
                        app.main_button.invoke()
                    assert app.tailer is None and not app.upload_enabled
                    assert app.expanded.tailer is None and not app.expanded.enabled
                    assert list(queue.db.execute("SELECT * FROM pending WHERE id LIKE 'legacy-%'")) == legacy
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
