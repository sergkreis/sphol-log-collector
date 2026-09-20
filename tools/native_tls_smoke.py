import json
import ssl
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from collector.diagnostics import exception_fields, clean
from collector.transport import HTTPS
from collector.browser_gui import BrowserGUI
from collector.modern_ui import ModernShell

class TLSDiagnostics(unittest.TestCase):
    def test_native_context_and_fail_closed(self):
        import sys
        from collector.tls_context import create_context
        context = create_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        if sys.platform == 'win32':
            import truststore
            self.assertIsInstance(context, truststore.SSLContext)
        with patch('collector.tls_context.sys.platform', 'win32'):
            backend = Mock()
            backend.SSLContext.side_effect = RuntimeError('unavailable')
            with patch.dict('sys.modules', {'truststore': backend}), patch('collector.tls_context.ssl.create_default_context') as fallback:
                with self.assertRaises(RuntimeError): create_context()
                fallback.assert_not_called()
            with patch.dict('sys.modules', {'truststore': None}):
                with self.assertRaises(ImportError): create_context()

    def test_ssl_precedes_errno_and_subclasses(self):
        class PrivateTLS(ssl.SSLError): pass
        class PrivateCert(ssl.SSLCertVerificationError): pass
        for cls in (ssl.SSLError, ssl.SSLCertVerificationError, PrivateTLS, PrivateCert):
            e = cls(1, 'SECRET URL token path')
            d = exception_fields(e)
            self.assertEqual(d['error'], 'tls')
            self.assertIn(d['exception'], ('SSLError', 'SSLCertVerificationError'))
            self.assertNotIn('SECRET', json.dumps(d))
            self.assertNotIn('Private', json.dumps(d))

    def test_certificate_code_roundtrip(self):
        for value in (20, True, 'SECRET', -1, 2147483648):
            e = ssl.SSLCertVerificationError(1, 'SECRET')
            e.verify_code = value
            d = clean(dict(event='http.connect_tls', time=1, outcome='error', **exception_fields(e)))
            self.assertEqual(d.get('verify_code'), 20 if type(value) is int and value == 20 else None)
            self.assertEqual(d['exception'], 'SSLCertVerificationError')

    def test_non_certificate_attributes_ignored(self):
        for e, expected in ((ssl.SSLError(1, 'SECRET'), 'tls'), (OSError(5, 'SECRET'), 'os_error'), (PermissionError(13, 'SECRET'), 'permission')):
            e.verify_code = 20
            d = exception_fields(e)
            self.assertEqual(d['error'], expected)
            self.assertIsNone(d['verify_code'])

    def test_transport_failure_not_reported_success(self):
        conn = Mock()
        conn.connect.side_effect = ssl.SSLCertVerificationError(1, 'SECRET')
        with patch('collector.transport.http.client.HTTPSConnection', return_value=conn), patch('collector.transport.emit') as emit:
            with self.assertRaises(ssl.SSLError): HTTPS().post('/api/collector/v1/pairings', {})
        complete = [c for c in emit.call_args_list if c.args[0] == 'http.complete']
        self.assertEqual(complete[0].args[1], 'error')
        conn.close.assert_called_once()

    def test_browser_error_reaches_first_screen(self):
        app = SimpleNamespace(busy=True, redemption_pending=False, browser_paused=False, pairing=object(), browser_outstanding=lambda:False, pairing_notice=Mock(), uploader=None)
        self.assertTrue(BrowserGUI.browser_result(app, 'browser_start', None, ssl.SSLCertVerificationError(1, 'SECRET')))
        self.assertIsNone(app.pairing)
        self.assertIn('сертификат', app.login_error)
        self.assertIn('Войти через EVE', app.login_error)
        self.assertNotIn('SECRET', app.login_error)
        shell = SimpleNamespace(app=app, screen='main', login_error_label=Mock())
        self.assertEqual(ModernShell.state(shell), 'first')
        ModernShell.update_dynamic(shell, 'first')
        shell.login_error_label.config.assert_called_once_with(text=app.login_error)


    def test_visible_tls_error_and_retry_at_minimum_size(self):
        import tkinter as tk
        import os
        from pathlib import Path
        from PIL import ImageGrab
        root = tk.Tk()
        app = SimpleNamespace(window=root, frame=tk.Frame(root), pairing=None, uploader=None, busy=False,
                              login_error='', pair=Mock(), pairing_notice=Mock(), browser_outstanding=lambda:False)
        shell = ModernShell(app)
        try:
            BrowserGUI.browser_result(app, 'browser_start', None, ssl.SSLCertVerificationError(1, 'SECRET'))
            shell.refresh()
            root.update()
            label = shell.login_error_label
            self.assertTrue(label.winfo_ismapped())
            self.assertEqual(label.cget('text'), app.login_error)
            self.assertLessEqual(label.winfo_y()+label.winfo_height(), label.master.winfo_height())
            button = shell.primary_button
            x=button.winfo_rootx()+button.winfo_width()//2
            y=button.winfo_rooty()+button.winfo_height()//2
            self.assertIs(root.winfo_containing(x,y),button)
            button.invoke()
            app.pair.assert_called_once()
            if os.environ.get('SPHOL_SMOKE_EVIDENCE'):
                out=Path(os.environ['SPHOL_SMOKE_EVIDENCE']);out.mkdir(parents=True,exist_ok=True)
                ImageGrab.grab().crop((root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())).save(out/'tls-login-error.png')
        finally:
            root.after_cancel(shell._after)
            root.destroy()

if __name__ == '__main__': unittest.main()
