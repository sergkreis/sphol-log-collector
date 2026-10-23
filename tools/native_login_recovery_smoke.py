"""Released-schema modern-button regression; synthetic state, no external I/O."""
import gc
import json
import os
import socket
import sqlite3
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from collector.core import PendingQueue
from collector.network_gui import ConnectedApp
from collector.transport import ProtocolError
from tools.native_single_smoke import visible_button


class NativeLoginRecovery(unittest.TestCase):
    def fixture(self, stack, *, expired=False, pending=False, corrupt=False):
        import tkinter as tk
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('No external network')))
        stack.enter_context(patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('No external network')))
        stack.enter_context(patch('collector.connection_status.probe', return_value=None))
        stack.enter_context(patch('collector.connection_status.delivery_probe', side_effect=OSError('Synthetic offline')))
        # Only Linux uses a synthetic encryption boundary; Windows exercises DPAPI.
        if os.name != 'nt':
            for module in ('credentials', 'site_code', 'browser_recovery'):
                stack.enter_context(patch('collector.' + module + '.crypt', side_effect=lambda raw, **kw: raw))
        from collector.credentials import crypt
        from collector.transport import encode
        credentials = dict(access_token='a'*43, token_type='Bearer', scope='gamelogs:write',
            installation_id='a'*32, expires_at='2000-01-01T00:00:00Z' if expired else '2099-01-01T00:00:00Z',
            characters=[dict(id=42, name='Synthetic Pilot')])
        credential_path = root/'credentials.dpapi'
        credential_path.write_bytes(crypt(encode({'invalid': True} if corrupt else credentials)))
        # Exact v0.3.17 tables/row format, independent of candidate constructors.
        path = root/'queue.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE pending (id TEXT PRIMARY KEY, payload TEXT NOT NULL, size INTEGER NOT NULL);'
                'CREATE TABLE capture_checkpoint (key TEXT PRIMARY KEY, payload TEXT NOT NULL);'
                'CREATE TABLE delivery_retry (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, until REAL NOT NULL);')
            payload = json.dumps(dict(schema=1, time='2026-01-01T00:00:00+00:00', type='combat', listener='Synthetic Pilot', text='Synthetic combat'), separators=(',', ':'))
            db.execute('INSERT INTO pending VALUES (?,?,?)', ('original-event-id', payload, len(payload.encode())))
        db.close()
        if pending:
            claim = dict(user_code='ABCD-EFGH-JKLM-NPQR-STUV', verifier='b'*43,
                scope='gamelogs:write', device_label='SPHOL Windows collector', client_version='0.3.17')
            (root/'site-redemption.dpapi').write_bytes(crypt(encode(claim)))
        q = PendingQueue(path)
        logs = root/'Gamelogs'; logs.mkdir()
        window = tk.Tk()
        app = ConnectedApp(window, logs, q)
        window.update()
        def cleanup():
            for checker in (app.connection_status, app.delivery_check):
                if hasattr(checker, 'shutdown'): checker.shutdown(timeout=.5)
            app.expanded.close(); app.legacy.close()
            for callback in window.tk.call('after', 'info'): window.after_cancel(callback)
            window.destroy(); q.close(); gc.collect()
        stack.callback(cleanup)
        return app, credentials, credential_path.read_bytes(), list(q.db.execute('SELECT * FROM pending'))

    def pump(self, app, predicate):
        deadline = time.monotonic()+5
        while not predicate() and time.monotonic()<deadline:
            app.window.update(); time.sleep(.01)
        self.assertTrue(predicate())
        app.window.update()

    def test_modern_button_renews_original_scope_without_capture(self):
        from collector.update_gui import active
        uri = ('https://login.eveonline.com/v2/oauth/authorize?response_type=code&client_id=client'
               '&redirect_uri=https%3A%2F%2Fsphol.com%2Fsso%2Feve%2Fcallback&state=collector_' + 'B'*32)
        with ExitStack() as stack:
            app, old, saved, rows = self.fixture(stack, expired=True)
            stack.enter_context(patch('collector.network_gui.messagebox.askyesno', return_value=True))
            stack.enter_context(patch('collector.network_gui.webbrowser.open', return_value=True))
            fresh = dict(old, access_token='z'*43, expires_at='2099-01-01T00:00:00Z')
            def response(path, payload):
                if path.endswith('/browser'):
                    self.assertEqual(payload['scope'], old['scope'])
                    self.assertEqual(payload['renewal'], dict(installation_id=old['installation_id'], old_token=old['access_token']))
                    return 200, dict(device_secret='d'*43, user_code='e'*43, verification_uri=uri, expires_in=120, interval=5)
                return 200, fresh
            http = stack.enter_context(patch.object(app.browser_recovery.http, 'post', side_effect=response))
            visible_button(app.window, 'Войти через EVE').invoke()
            self.pump(app, lambda: not app.busy and app.browser_outstanding())
            self.assertEqual(app.browser_pending.load()['renewal'], old)
            original_claim = app.browser_pending.path.read_bytes()
            with patch.object(app.store, 'save', side_effect=OSError('Synthetic denied')):
                app.retry_browser()
                self.pump(app, lambda: not app.busy)
            self.assertTrue(active(app))  # unsaved returned credential blocks replacement
            self.assertEqual(app.browser_pending.path.read_bytes(), original_claim)
            self.assertEqual(app.store.path.read_bytes(), saved)
            # Download remains usable even while replacement is blocked.
            prepare = stack.enter_context(patch('collector.update_gui.updater.prepare', return_value=None))
            app.updates.check()
            self.pump(app, lambda: prepare.called and not app.updates.busy)
            app.modern.refresh(); app.window.update()
            visible_button(app.window, 'Открыть EVE').invoke()
            self.pump(app, lambda: not app.busy and app.uploader is not None)
            self.assertEqual(app.store.load(), fresh)
            self.assertFalse(app.browser_outstanding())
            self.assertFalse(active(app))
            self.assertEqual(list(app.queue.db.execute('SELECT * FROM pending')), rows)
            self.assertIsNone(app.tailer); self.assertFalse(app.upload_enabled)
            self.assertEqual(http.call_count, 3)

    def test_renewal_readback_failure_reopen_preserves_claim_and_queue(self):
        from collector.browser_recovery import BrowserRecovery, BrowserPendingStore
        from collector.credentials import CredentialStore
        with ExitStack() as stack:
            app, old, saved, rows = self.fixture(stack, expired=True)
            fresh = dict(old, access_token='z'*43, expires_at='2099-01-01T00:00:00Z')
            uri = ('https://login.eveonline.com/v2/oauth/authorize?response_type=code&client_id=client'
                   '&redirect_uri=https%3A%2F%2Fsphol.com%2Fsso%2Feve%2Fcallback&state=collector_' + 'B'*32)
            def response(path, body):
                if path.endswith('/browser'):
                    return 200, dict(device_secret='d'*43, user_code='e'*43, verification_uri=uri, expires_in=120, interval=5)
                return 200, fresh
            stack.enter_context(patch.object(app.browser_recovery.http, 'post', side_effect=response))
            stack.enter_context(patch('collector.network_gui.messagebox.askyesno', return_value=True))
            stack.enter_context(patch('collector.network_gui.webbrowser.open', return_value=True))
            visible_button(app.window, 'Войти через EVE').invoke()
            self.pump(app, lambda: not app.busy and app.browser_outstanding())
            app.browser_paused = True
            claim = app.browser_pending.path.read_bytes()
            load = app.store.load
            with patch.object(app.store, 'load', side_effect=lambda **kw: load(**kw) if kw.get('allow_expired') else None):
                app.retry_browser()
                self.pump(app, lambda: not app.busy)
            self.assertEqual(app.browser_pending.path.read_bytes(), claim)
            self.assertEqual(app.store.load(), fresh)  # save succeeded; readback failed
            self.assertEqual(app.browser_recovery.unsaved_result, fresh)
            # Reopen persisted DPAPI stores, without any cached result. Separate
            # interpreter/server restart is gated by the private HTTP source test.
            reopened = BrowserRecovery(BrowserPendingStore(app.browser_pending.path),
                CredentialStore(app.store.path), app.browser_recovery.http)
            self.assertEqual(reopened.redeem(), fresh)
            self.assertIsNone(reopened.pending.load())
            self.assertEqual(list(app.queue.db.execute('SELECT * FROM pending')), rows)
            self.assertIsNone(app.tailer); self.assertFalse(app.upload_enabled)

    def test_visible_pending_retry_preserves_original_owner_queue_and_consent(self):
        with ExitStack() as stack:
            app, credentials, saved, rows = self.fixture(stack, pending=True)
            # Crash after credential.save(), before pending.clear(): both exist.
            original = app.site_codes.pending.path.read_bytes()
            http = stack.enter_context(patch.object(app.site_codes.redemption.http, 'post', return_value=(200, credentials)))
            consent = stack.enter_context(patch('collector.network_gui.messagebox.askyesno', return_value=False))
            visible_button(app.window, 'Войти через EVE').invoke()
            http.assert_not_called()
            self.assertEqual(app.site_codes.pending.path.read_bytes(), original)
            consent.return_value = True
            visible_button(app.window, 'Войти через EVE').invoke()
            self.pump(app, lambda: app.uploader is not None and not app.site_codes.busy)
            self.assertEqual(http.call_count, 1)
            self.assertEqual(http.call_args.args[1], json.loads(__import__('collector.credentials', fromlist=['crypt']).crypt(original, decrypt=True)))
            self.assertEqual(app.uploader.credentials, credentials)
            self.assertFalse(app.site_codes.pending.path.exists())
            self.assertEqual(app.store.load(), credentials)
            self.assertEqual(list(app.queue.db.execute('SELECT * FROM pending')), rows)
            self.assertFalse(app.upload_enabled); self.assertIsNone(app.tailer)

    def test_expired_and_invalid_binding_error_is_visible_and_preserves_bytes(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt), ExitStack() as stack:
                app, credentials, saved, rows = self.fixture(stack, expired=True, corrupt=corrupt)
                self.assertIsNone(app.uploader)
                with self.assertRaises(ProtocolError): app.store.load()
                stack.enter_context(patch('collector.network_gui.messagebox.askyesno', return_value=False))
                visible_button(app.window, 'Войти через EVE').invoke(); app.window.update()
                self.assertTrue(app.modern.login_error_label.winfo_ismapped())
                self.assertTrue(app.modern.login_error_label.cget('text'))
                self.assertEqual(app.store.path.read_bytes(), saved)
                self.assertEqual(list(app.queue.db.execute('SELECT * FROM pending')), rows)
                self.assertIsNone(app.tailer); self.assertFalse(app.upload_enabled)

    def test_pending_network_failure_is_visible_and_retryable(self):
        with ExitStack() as stack:
            app, credentials, saved, rows = self.fixture(stack, pending=True)
            original = app.site_codes.pending.path.read_bytes()
            stack.enter_context(patch('collector.network_gui.messagebox.askyesno', return_value=True))
            http = stack.enter_context(patch.object(app.site_codes.redemption.http, 'post', side_effect=ConnectionResetError()))
            visible_button(app.window, 'Войти через EVE').invoke()
            self.pump(app, lambda: http.called and not app.site_codes.busy)
            self.assertTrue(app.modern.login_error_label.cget('text'))
            self.assertEqual(app.site_codes.pending.path.read_bytes(), original)
            self.assertEqual(app.store.path.read_bytes(), saved)
            self.assertEqual(list(app.queue.db.execute('SELECT * FROM pending')), rows)
            self.assertIsNone(app.tailer)

    def test_pending_rejects_foreign_owner_without_overwriting_credentials(self):
        with ExitStack() as stack:
            app, credentials, saved, rows = self.fixture(stack, pending=True)
            original = app.site_codes.pending.path.read_bytes()
            stack.enter_context(patch('collector.network_gui.messagebox.askyesno', return_value=True))
            other = dict(credentials, installation_id='other-installation', characters=[dict(id=99, name='Other Pilot')])
            http = stack.enter_context(patch.object(app.site_codes.redemption.http, 'post', return_value=(200, other)))
            visible_button(app.window, 'Войти через EVE').invoke()
            self.pump(app, lambda: http.called and not app.site_codes.busy)
            self.assertEqual(app.store.path.read_bytes(), saved)
            self.assertEqual(app.site_codes.pending.path.read_bytes(), original)
            self.assertEqual(list(app.queue.db.execute('SELECT * FROM pending')), rows)
            self.assertEqual(app.uploader.credentials, credentials)
            self.assertFalse(app.upload_enabled); self.assertIsNone(app.tailer)

    def test_fallback_cannot_replace_expired_or_corrupt_binding(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt), ExitStack() as stack:
                app, credentials, saved, rows = self.fixture(stack, expired=True, corrupt=corrupt)
                http = stack.enter_context(patch.object(app.site_codes.redemption.http, 'post'))
                app.site_codes.code.set('ABCD-EFGH-JKLM-NPQR-STUV')
                app.site_codes.submit()
                http.assert_not_called()
                self.assertFalse(app.site_codes.pending.path.exists())
                self.assertIn('заблокирован', app.site_codes.label.cget('text'))
                self.assertEqual(app.store.path.read_bytes(), saved)
                self.assertEqual(list(app.queue.db.execute('SELECT * FROM pending')), rows)

    def test_expired_owner_with_pending_never_sends_or_rebinds_backlog(self):
        with ExitStack() as stack:
            app, credentials, saved, rows = self.fixture(stack, expired=True, pending=True)
            original = app.site_codes.pending.path.read_bytes()
            http = stack.enter_context(patch.object(app.site_codes.redemption.http, 'post'))
            visible_button(app.window, 'Войти через EVE').invoke(); app.window.update()
            http.assert_not_called()
            self.assertEqual(app.site_codes.pending.path.read_bytes(), original)
            self.assertEqual(app.store.path.read_bytes(), saved)
            self.assertEqual(list(app.queue.db.execute('SELECT * FROM pending')), rows)
            self.assertIsNone(app.uploader); self.assertIsNone(app.tailer)

    def test_late_pending_result_double_click_and_no_capture(self):
        import threading
        with ExitStack() as stack:
            app, credentials, saved, rows = self.fixture(stack, pending=True)
            entered, release = threading.Event(), threading.Event()
            stack.enter_context(patch('collector.network_gui.messagebox.askyesno', return_value=True))
            def delayed(*args, **kwargs):
                entered.set()
                if not release.wait(3): raise TimeoutError('Synthetic timeout')
                return 200, credentials
            http = stack.enter_context(patch.object(app.site_codes.redemption.http, 'post', side_effect=delayed))
            try:
                visible_button(app.window, 'Войти через EVE').invoke()
                self.pump(app, entered.is_set)
                visible_button(app.window, 'Войти через EVE').invoke()
                self.assertEqual(http.call_count, 1)
                self.assertTrue(app.site_codes.pending.path.exists())
                self.assertIsNone(app.tailer)
            finally:
                release.set()
                self.pump(app, lambda: not app.site_codes.busy)
            self.assertFalse(app.site_codes.pending.path.exists())
            self.assertEqual(app.uploader.credentials, credentials)
            self.assertEqual(list(app.queue.db.execute('SELECT * FROM pending')), rows)
            self.assertIsNone(app.tailer); self.assertFalse(app.upload_enabled)

    def test_secondary_backlog_blocks_change_and_direct_unpair(self):
        for action in ('modern', 'direct'):
            with self.subTest(action=action), ExitStack() as stack:
                app, credentials, saved, rows = self.fixture(stack)
                # Primary is empty: reproduce the formerly reachable destructive path.
                app.queue.acknowledge([row[0] for row in rows])
                event = dict(schema=2, type='game-event', category='notify',
                    listener='Synthetic Pilot', text='Цель неуязвима.',
                    time='2026-01-01T00:00:00+00:00')
                app.expanded.queue.put('d'*64, event)
                before = list(app.expanded.queue.db.execute('SELECT * FROM pending'))
                app.expanded.store.save(credentials)
                secondary_key = app.expanded.store.path.read_bytes()
                warning = stack.enter_context(patch('collector.network_gui.messagebox.showwarning'))
                confirm = stack.enter_context(patch('collector.network_gui.messagebox.askyesno', return_value=True))
                if action == 'modern':
                    app.modern.change_character()
                else:
                    app.unpair()
                self.assertEqual(list(app.expanded.queue.db.execute('SELECT * FROM pending')), before)
                self.assertEqual(app.expanded.store.path.read_bytes(), secondary_key)
                self.assertEqual(app.store.path.read_bytes(), saved)
                self.assertEqual(app.uploader.credentials, credentials)
                self.assertTrue(warning.called)
                confirm.assert_not_called()
                self.assertIsNone(app.tailer)

    def test_visible_update_result_with_failed_pending_preserves_state(self):
        from collector.update_gui import active
        with ExitStack() as stack:
            app, credentials, saved, rows = self.fixture(stack, expired=True, pending=True)
            original = app.site_codes.pending.path.read_bytes()
            self.assertFalse(active(app))
            app.modern.show('settings'); app.window.update()
            prepare = stack.enter_context(patch('collector.update_gui.updater.prepare', side_effect=OSError('Synthetic offline')))
            visible_button(app.window, 'Обновить приложение').invoke()
            self.pump(app, lambda: prepare.called and not app.updates.busy)
            app.modern.refresh(); app.window.update()
            self.assertTrue(app.modern.update_status_label.winfo_ismapped())
            self.assertIn('обновление не выполнено', app.modern.update_status_label.cget('text'))
            self.assertEqual(app.window.minsize(), (560, 440))
            self.assertEqual((app.window.winfo_width(), app.window.winfo_height()), (560, 440))
            for text in ('Обновить приложение', 'Сменить персонажа', 'Помощь', '← Назад'):
                button = visible_button(app.window, text)
                self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight())
                self.assertIs(app.window.winfo_containing(button.winfo_rootx()+button.winfo_width()//2,
                    button.winfo_rooty()+button.winfo_height()//2), button)
            self.assertEqual(app.site_codes.pending.path.read_bytes(), original)
            self.assertEqual(app.store.path.read_bytes(), saved)
            self.assertEqual(list(app.queue.db.execute('SELECT * FROM pending')), rows)

    def test_settings_overflow_keeps_footer_and_scroll_actions_reachable(self):
        import tkinter.font as tkfont
        with ExitStack() as stack:
            app, credentials, saved, rows = self.fixture(stack, expired=True, pending=True)
            original = app.site_codes.pending.path.read_bytes()
            # Exercise larger platform/DPI metrics as well as multiline results.
            # This is a geometry stress test, not a substitute for Windows CI.
            scaling = app.window.tk.call('tk', 'scaling')
            app.window.tk.call('tk', 'scaling', 1.75)
            stack.callback(app.window.tk.call, 'tk', 'scaling', scaling)
            app.modern.show('settings'); app.window.update()
            back = visible_button(app.window, '← Назад')
            self.assertGreaterEqual(tkfont.Font(font=back.cget('font')).metrics('linespace'), 24)
            canvas = app.modern.update_status_label.master.master
            from tkinter import Canvas
            assert isinstance(canvas, Canvas)
            for result in ('Синтетический результат\n' * 12, 'Синтетический результат'):
                app.updates.label.configure(text=result)
                app.modern.refresh(); app.window.update()
                self.assertEqual(app.window.minsize(), (560, 440))
                self.assertEqual((app.window.winfo_width(), app.window.winfo_height()), (560, 440))
                self.assertGreaterEqual(back.winfo_height(), back.winfo_reqheight())
                self.assertIs(app.window.winfo_containing(back.winfo_rootx()+back.winfo_width()//2,
                    back.winfo_rooty()+back.winfo_height()//2), back)
                canvas.yview_moveto(1); app.window.update()
                help_button = visible_button(app.window, 'Помощь')
                self.assertGreaterEqual(help_button.winfo_height(), help_button.winfo_reqheight())
                self.assertIs(app.window.winfo_containing(help_button.winfo_rootx()+help_button.winfo_width()//2,
                    help_button.winfo_rooty()+help_button.winfo_height()//2), help_button)
                canvas.yview_moveto(0); app.window.update()
                update = visible_button(app.window, 'Обновить приложение')
                self.assertGreaterEqual(update.winfo_height(), update.winfo_reqheight())
                self.assertIs(app.window.winfo_containing(update.winfo_rootx()+update.winfo_width()//2,
                    update.winfo_rooty()+update.winfo_height()//2), update)
            back.invoke(); app.window.update()
            self.assertEqual(app.modern.screen, 'main')
            self.assertEqual(app.site_codes.pending.path.read_bytes(), original)
            self.assertEqual(app.store.path.read_bytes(), saved)
            self.assertEqual(list(app.queue.db.execute('SELECT * FROM pending')), rows)


if __name__ == '__main__':
    unittest.main()
