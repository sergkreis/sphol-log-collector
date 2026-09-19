"""Offline safety gates; no production pairing or browser launches."""
import time
import types
import unittest
from unittest.mock import Mock, patch
from collector.transport import Pairing, PAIR_URI, ProtocolError, HTTPFailure
from collector.pairing_ux import active_url, failure_text, PairingUX


OFFICIAL_EVE_URL = 'https://login.eveonline.com/v2/oauth/authorize?response_type=code&client_id=client&redirect_uri=https%3A%2F%2Fsphol.com%2Fsso%2Feve%2Fcallback&state=collector_' + 'A'*32


class Recovery(unittest.TestCase):
    def pairing(self):
        return types.SimpleNamespace(browser=True, browser_uri=PAIR_URI + '#' + 'A' * 43,
                                     deadline=time.monotonic() + 60, secret='DEVICE-SECRET')

    def app(self):
        app = PairingUX()
        app.pairing = self.pairing()
        app.pairing_message = Mock()
        app.window = Mock()
        app.browser_results = __import__('queue').Queue()
        app.browser_job = None
        return app

    def test_only_active_fixed_origin_fragment_can_escape(self):
        p = self.pairing()
        self.assertEqual(active_url(p), p.browser_uri)
        p.browser_uri = OFFICIAL_EVE_URL
        self.assertEqual(active_url(p), OFFICIAL_EVE_URL)
        for url in ('https://evil.example/#' + 'A'*43, PAIR_URI + '?token=DEVICE-SECRET',
                    PAIR_URI + '#short', PAIR_URI + '#' + 'A'*43 + '\n',
                    OFFICIAL_EVE_URL + '#fragment'):
            p.browser_uri = url
            self.assertIsNone(active_url(p))
        p = self.pairing()
        p.deadline = 0
        self.assertIsNone(active_url(p))
        self.assertIsNone(active_url(None))

    def test_errors_are_actionable_and_redacted(self):
        for error, category in ((OSError('secret'), 'сеть'), (HTTPFailure(503), '503'),
                                (ProtocolError('secret'), 'ответ'), (RuntimeError('secret'), 'ошибка')):
            text = failure_text(error)
            self.assertIn(category, text.lower())
            self.assertNotIn('secret', text)

    def test_copy_explicit_active_link_not_device_secret(self):
        app = self.app()
        app.copy_pairing_link()
        app.window.clipboard_append.assert_called_once_with(app.pairing.browser_uri)
        app.pairing.deadline = 0
        app.window.reset_mock()
        app.copy_pairing_link()
        app.window.clipboard_clear.assert_not_called()

    def test_browser_false_exception_and_stale(self):
        for result in (False, OSError('secret')):
            app = self.app()
            with patch('collector.pairing_ux.webbrowser.open', side_effect=result if isinstance(result, Exception) else None, return_value=False):
                app.open_pairing_browser()
                deadline = time.monotonic() + 2
                while app.browser_results.empty() and time.monotonic() < deadline:
                    time.sleep(.01)
                app.poll_browser()
            self.assertIn('браузер', app.pairing_message.config.call_args.kwargs['text'].lower())
            self.assertIsNone(app.browser_job)
        app = self.app()
        old = app.pairing
        app.browser_job = (old, time.monotonic() + 10)
        app.pairing = self.pairing()
        app.browser_results.put((old, False))
        app.poll_browser()
        app.pairing_message.config.assert_not_called()

    def test_transport_rejects_malformed_and_network(self):
        valid = dict(device_secret='D'*43, user_code='A'*43, verification_uri=OFFICIAL_EVE_URL,
                     expires_in=300, interval=5)
        for change in (dict(verification_uri='https://evil.example'), dict(user_code='short'),
                       dict(expires_in=0), dict(expires_in=True), dict(extra='bad')):
            http = Mock()
            http.post.return_value = (201, {**valid, **change})
            p = Pairing(http=http, browser=True)
            with self.assertRaises(ProtocolError):
                p.start()
            self.assertIsNone(active_url(p))
        http = Mock()
        http.post.side_effect = OSError('synthetic offline')
        with self.assertRaises(OSError):
            Pairing(http=http, browser=True).start()


if __name__ == '__main__':
    unittest.main()
