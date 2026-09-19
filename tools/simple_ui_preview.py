"""Fresh actual-Tk binding fixtures. Synthetic state only, sockets blocked."""
import gc
from pathlib import Path
import socket
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
from unittest.mock import Mock, patch
from collector.core import PendingQueue
from collector.network_gui import ConnectedApp
from collector.transport import Uploader

CREDENTIALS = dict(access_token='s'*43, token_type='Bearer', scope='gamelogs:write',
                   installation_id='a'*32, expires_at='2099-01-01T00:00:00Z',
                   characters=[dict(id=42, name='Synthetic Pilot')])


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def assert_main_bounds(window, app):
    window.update()
    x, y = window.winfo_rootx(), window.winfo_rooty()
    for widget in descendants(window):
        if not widget.winfo_viewable() or str(widget).startswith(str(app.settings_host)):
            continue  # Settings content is intentionally clipped by its scroll viewport.
        if isinstance(widget, (ttk.Label, ttk.Button, ttk.Entry, ttk.Checkbutton)):
            assert widget.winfo_height() >= widget.winfo_reqheight(), str(widget)
            assert widget.winfo_width() >= widget.winfo_reqwidth(), str(widget)
            assert x <= widget.winfo_rootx() and y <= widget.winfo_rooty(), str(widget)
            assert widget.winfo_rootx()+widget.winfo_width() <= x+window.winfo_width(), str(widget)
            assert widget.winfo_rooty()+widget.winfo_height() <= y+window.winfo_height(), str(widget)
            if isinstance(widget, ttk.Button):
                hit = window.winfo_containing(widget.winfo_rootx()+widget.winfo_width()//2,
                                               widget.winfo_rooty()+widget.winfo_height()//2)
                assert hit == widget, ('occluded', str(widget), str(hit))


def render(destination):
    from PIL import ImageGrab
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for scene in ('unbound', 'bound-idle', 'legacy-bound', 'recovery'):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')), \
                patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('Network forbidden')), \
                patch('collector.network_gui.CredentialStore.load', return_value=None), \
                patch('collector.connection_status.probe', return_value=None), \
                patch('webbrowser.open', return_value=True) as browser:
            root = Path(temp)
            logs = root / 'Gamelogs'
            logs.mkdir()
            q = PendingQueue(root / 'pending.sqlite3')
            q.put('synthetic-legacy', {'listener': 'unknown'})
            original = list(q.db.execute('SELECT * FROM pending'))
            pending = root / 'site-redemption.dpapi'
            if scene == 'recovery':
                pending.write_bytes(b'SYNTHETIC pending fixture; not DPAPI')
            w = tk.Tk()
            errors = []
            w.report_callback_exception = lambda *args: errors.append(args)
            with patch.object(w, 'after'), patch('collector.site_code_gui.PendingStore.load', return_value={'scope': 'combat:write'}):
                app = ConnectedApp(w, logs, q)
                try:
                    ttk.Label(app.frame, text='PREVIEW · синтетические данные · сеть заблокирована', style='Small.TLabel').pack(before=app.header, fill='x')
                    ui = app.site_codes
                    if scene != 'unbound':
                        credentials = dict(CREDENTIALS, scope='combat:write' if scene == 'legacy-bound' else 'gamelogs:write')
                        app.uploader = Uploader(credentials)
                        app.show_identity()
                    app.refresh_controls()
                    w.update()
                    assert not any(isinstance(c, ttk.Combobox) for c in descendants(w))
                    assert not app.support_button.winfo_viewable()
                    assert not app.dashboard.warning.winfo_viewable()
                    assert not app.expanded.summary.winfo_viewable()
                    if scene == 'unbound':
                        assert ui.scope.get() == 'gamelogs:write'
                        assert ui.login_button.winfo_viewable()
                        assert not ui.entry.winfo_viewable()
                        (destination / 'primary').mkdir(exist_ok=True)
                        x, y = w.winfo_rootx(), w.winfo_rooty()
                        ImageGrab.grab(bbox=(x, y, x+w.winfo_width(), y+w.winfo_height())).save(destination / 'primary' / 'unbound.png')
                        ui.fallback_button.invoke()
                        w.update()
                        assert ui.entry.winfo_viewable() and ui.get_code_button.winfo_viewable()
                        assert not ui.review_button.winfo_viewable() and not ui.abandon_button.winfo_viewable()
                        ui.get_code_button.invoke()
                        browser.assert_called_once_with('https://sphol.com/collector/pair')
                        assert not app.main_button.winfo_viewable()
                        assert app.tailer is None and not app.upload_enabled
                    elif scene == 'recovery':
                        assert ui.uncertain and ui.scope.get() == 'combat:write'
                        assert ui.review_button.winfo_viewable() and ui.abandon_button.winfo_viewable()
                        assert ui.entry.instate(['disabled'])
                        assert not ui.get_code_button.winfo_viewable()
                        assert not ui.legacy_button.winfo_viewable()
                    else:
                        assert not ui.frame.winfo_viewable()
                        assert all(not c.winfo_viewable() for c in descendants(ui.frame))
                        assert app.uploader.credentials == credentials
                        if scene == 'legacy-bound':
                            assert 'Только боевые события' in app.dashboard.subtitle.cget('text')
                    for width, height in ((560, 440),):
                        w.geometry(f'{width}x{height}')
                        w.update()
                        widgets = [app.identity, app.settings_button, app.open_site]
                        if scene not in ('unbound', 'recovery'):
                            widgets += [app.main_button, app.last_ack, app.dashboard.sent, app.dashboard.waiting]
                        else:
                            assert not app.main_button.winfo_viewable()
                        if scene in ('unbound', 'recovery'):
                            widgets += [ui.button, ui.label]
                            if scene == 'unbound':
                                widgets += [ui.entry]
                            widgets += [ui.review_button, ui.abandon_button] if scene == 'recovery' else [ui.get_code_button]
                        for c in widgets:
                            assert c.winfo_viewable(), str(c)
                            assert c.winfo_height() >= c.winfo_reqheight(), (scene, width, str(c), c.winfo_height(), c.winfo_reqheight(), c.winfo_rooty(), c.cget('text'))
                            assert c.winfo_rooty()+c.winfo_height() <= w.winfo_rooty()+height, str(c)
                    assert_main_bounds(w, app)
                    x, y = w.winfo_rootx(), w.winfo_rooty()
                    ImageGrab.grab(bbox=(x, y, x+w.winfo_width(), y+w.winfo_height())).save(destination / (scene+'.png'))
                    if scene == 'unbound':
                        # A completed site-code callback removes every binding control.
                        ui.uncertain = True
                        ui.results.put((CREDENTIALS, None))
                        ui.tick()
                        w.update()
                        assert not ui.frame.winfo_viewable()
                        assert all(not c.winfo_viewable() for c in descendants(ui.frame))
                        assert app.tailer is None and not app.upload_enabled
                        assert app.dashboard.ack_at is None
                    if scene == 'recovery':
                        ui.redemption = Mock()
                        # Exercise both consent decisions without a blocking Tk modal.
                        with patch('collector.site_code_gui.messagebox.askyesno', return_value=False) as consent, \
                                patch('collector.site_code_gui.threading.Thread') as worker:
                            ui.button.invoke()
                            consent.assert_called_once()
                            ui.redemption.prepare.assert_not_called()
                            worker.assert_not_called()
                        assert pending.read_bytes() == b'SYNTHETIC pending fixture; not DPAPI'
                        assert app.uploader.credentials == credentials
                        with patch('collector.site_code_gui.messagebox.askyesno', return_value=True) as consent, \
                                patch('collector.site_code_gui.threading.Thread') as worker:
                            ui.button.invoke()
                            consent.assert_called_once()
                            worker.assert_called_once()
                            worker.call_args.kwargs['target']()
                        ui.redemption.prepare.assert_called_once_with('', 'combat:write')
                        ui.redemption.redeem.assert_called_once_with(website_consent=True)
                        assert app.uploader.credentials == credentials
                        assert pending.read_bytes() == b'SYNTHETIC pending fixture; not DPAPI'
                    assert list(q.db.execute('SELECT * FROM pending')) == original
                    assert not errors, errors
                    print('PASS actual Tk '+scene+': fixed scope, visibility, callbacks, bounds, preserved state')
                finally:
                    app.expanded.close()
                    app.legacy.close()
                    q.close()
                    w.destroy()
            del app, ui, w
            gc.collect()


if __name__ == '__main__':
    render(sys.argv[1])
