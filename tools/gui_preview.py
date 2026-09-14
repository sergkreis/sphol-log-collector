"""Offline actual Tk acceptance + screenshots. No production files/network.
Run: xvfb-run -a -s '-screen 0 1280x1600x24' /usr/bin/python3 -m tools.gui_preview /tmp/sphol-main-preview
"""
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
from collector.transport import Uploader, ORIGIN


class SyntheticHTTP:
    offline = False
    reject = False

    def post(self, path, payload, token=None):
        assert path == '/api/collector/v1/events'
        if self.offline:
            raise OSError('Synthetic offline')
        events = payload['events']
        return 200, {'accepted_ids': [e['id'] for e in events], 'rejected': []}


def render(destination):
    from PIL import ImageGrab
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp, \
            patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')), \
            patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('Network forbidden')), \
            patch('webbrowser.open') as browser:
        root = Path(temp)
        logs = root / 'Gamelogs'
        logs.mkdir()
        queue = PendingQueue(root / 'pending.sqlite3')
        # Historical queue predates app launch: must not inflate THIS RUN.
        for i in range(4):
            queue.put(f'legacy-{i}', {'listener': 'unknown'})
        window = tk.Tk()
        errors, screenshots = [], []
        window.report_callback_exception = lambda *args: errors.append(args)
        # Deterministic event loop: test invokes real ticks explicitly, no races.
        with patch.object(window, 'after'):
            app = ConnectedApp(window, logs, queue)
            try:
                ttk.Label(app.frame, text='ВНУТРЕННИЙ PREVIEW · синтетические данные · сеть заблокирована', style='Warning.TLabel').pack(before=app.header, fill='x', pady=(0, 14))
                http = SyntheticHTTP()
                app.uploader = Uploader({'access_token': 'synthetic-token-' * 3, 'token_type': 'Bearer',
                    'scope': 'combat:write', 'installation_id': 'synthetic-preview',
                    'characters': [{'id': 1, 'name': 'CoolDoog'}], 'expires_at': '2099-01-01T00:00:00Z'}, http=http)
                app.show_identity()
                app.refresh_controls()
                legacy = list(queue.db.execute('SELECT * FROM pending'))

                def values():
                    return [int(label.cget('text').replace(' ', '')) for label in app.dashboard.metrics]

                def shot(name):
                    window.update()
                    x, y = window.winfo_rootx(), window.winfo_rooty()
                    ImageGrab.grab(bbox=(x, y, x + window.winfo_width(), y + window.winfo_height())).save(destination / (name + '.png'))
                    screenshots.append(name)
                    assert app.identity.cget('text') == 'CoolDoog'
                    assert not app.expanded.start_button.winfo_viewable()
                    # Footer must remain within the real client area.
                    assert app.footer.winfo_rooty() + app.footer.winfo_height() <= y + window.winfo_height()

                def upload():
                    app.network_tick()
                    deadline = time.monotonic() + 3
                    while app.busy and app.results.empty() and time.monotonic() < deadline:
                        time.sleep(.01)
                    assert not app.results.empty(), 'worker timed out'
                    app.network_tick()

                sequence = 0
                def combat(count):
                    nonlocal sequence
                    sequence += 1
                    when = (datetime.now(timezone.utc) + timedelta(seconds=2)).strftime('%Y.%m.%d %H:%M:%S')
                    (logs / f'synthetic-{sequence}.txt').write_text('Listener: CoolDoog\n-----\n' + ''.join(f'[ {when} ] (combat) Синтетический урон {i}\n' for i in range(count)), encoding='utf-8')
                    app.tick()

                assert values() == [0, 0, 4]
                assert not app.settings.winfo_ismapped()
                shot('idle')
                app.main_button.invoke()
                assert app.tailer and app.upload_enabled
                assert app.updates.button.instate(['disabled'])
                with patch('collector.update_gui.os.name', 'nt'), patch('collector.update_gui.sys.frozen', True, create=True):
                    app.updates.refresh_button()
                    assert app.updates.button.instate(['disabled'])
                    with patch('collector.update_gui.messagebox.showwarning') as warning:
                        app.updates.check()
                        warning.assert_called_once()
                app.network_tick()  # no eligible data: not a network success
                assert app.dashboard.ack_at is None
                assert app.dashboard.heading.cget('text') == 'Ждём боевые события'
                shot('waiting')
                combat(12)
                assert values() == [12, 0, 16]
                upload()
                assert values() == [12, 12, 4]
                ack = app.dashboard.ack_at
                assert ack is not None
                with patch('collector.dashboard.time.monotonic', return_value=ack + 125):
                    app.dashboard.refresh()
                    assert '2 мин назад' in app.last_ack.cget('text')
                app.network_tick()
                assert app.dashboard.ack_at == ack
                shot('active')
                combat(3)
                http.offline = True
                upload()
                assert values() == [15, 12, 7]
                assert app.upload_enabled and app.tailer
                assert 'повтор автоматически' in app.upload_state.cget('text')
                assert app.dashboard.ack_at == ack
                shot('offline')
                http.offline = False
                app.uploader.next_try = 0
                upload()
                assert values() == [15, 15, 4]
                assert list(queue.db.execute('SELECT * FROM pending')) == legacy
                shot('legacyqueue')
                app.main_button.invoke()
                assert app.tailer is None and not app.upload_enabled
                app.main_button.invoke()
                with patch.object(app.tailer, 'poll', side_effect=OSError('synthetic read error')):
                    app.tick()
                assert app.dashboard.heading.cget('text') == 'Не удалось читать журналы'
                shot('capture-error')
                app.main_button.invoke()
                app.main_button.invoke()
                # Actual callback + worker + result, but updater preparation stubbed.
                for name, outcome in [('update', None), ('update-error', OSError('synthetic'))]:
                    app.updates.button.config(state='normal')
                    with patch('collector.updater.prepare', side_effect=outcome, return_value=None) as prepare:
                        app.updates.button.invoke()
                        deadline = time.monotonic() + 3
                        while app.updates.results.empty() and time.monotonic() < deadline:
                            time.sleep(.01)
                        app.updates.poll()
                        prepare.assert_called_once()
                    shot(name)
                app.settings_button.invoke()
                window.update()
                assert app.settings.winfo_viewable()
                shot('settings')
                app.settings_button.invoke()
                app.open_site.invoke()
                browser.assert_called_once_with(ORIGIN)
                assert values() == [15, 15, 4]
                assert list(queue.db.execute('SELECT * FROM pending')) == legacy
                # Real integrated main control: scoped observations, independent ACK.
                app.expanded.uploader = Uploader({**app.uploader.credentials, 'scope':'gamelogs:write'}, http=http)
                app.main_button.invoke()
                assert app.expanded.enabled and app.expanded.tailer
                when = (datetime.now(timezone.utc)+timedelta(seconds=2)).strftime('%Y.%m.%d %H:%M:%S')
                (logs/'expanded.txt').write_text('Listener: CoolDoog\n-----\n'+''.join(f'[ {when} ] (notify) {text}\n' for text in ('Переход в варп-режим по приказу Synthetic Commander','Цель неуязвима.','private unknown')),encoding='utf-8')
                app.expanded.tick()
                deadline=time.monotonic()+3
                while app.expanded.results.empty() and time.monotonic()<deadline:
                    time.sleep(.01)
                app.expanded.tick()
                assert app.expanded.ack_count == 2 and app.expanded.queue.count() == 0
                assert values() == [15,15,4]
                shot('expanded-ack')
                app.main_button.invoke()
                assert not app.expanded.enabled and app.expanded.tailer is None
                assert errors == [], errors
                print(f'PASS: real Tk callbacks, parser/queue commits, validated ACK, empty polls, retry recovery, legacy preservation, capture error, updates, disclosure, generic URL; {len(screenshots)} screenshots: {", ".join(screenshots)}')
            finally:
                app.expanded.close()
                queue.close()
                window.destroy()


if __name__ == '__main__':
    render(sys.argv[1])
