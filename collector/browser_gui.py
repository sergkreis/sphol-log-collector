"""Durable primary browser flow; old site recovery has priority, never deletion."""
import time
from types import SimpleNamespace
from .browser_recovery import BrowserPendingStore, BrowserRecovery
from .transport import PAIR_URI, Uploader, valid_eve_authorize_url
from .site_code_gui import blocked
from .pairing_ux import active_url


class BrowserGUI:
    def init_browser_recovery(self):
        self.browser_pending = BrowserPendingStore(self.queue.path.parent / 'browser-redemption.dpapi')
        self.browser_recovery = BrowserRecovery(self.browser_pending, self.store)
        self.browser_next = 0
        self.browser_paused = False
        self.browser_required_probe = False
        self.restore_browser()

    def browser_outstanding(self):
        return hasattr(self, 'browser_pending') and self.browser_pending.path.exists()

    def restore_browser(self):
        if not self.browser_outstanding() or blocked(self):
            return
        try:
            claim = self.browser_pending.load()
            if claim is None:
                return
            remaining = max(0, min(300, claim.get('approval_expires', 0) - time.time()))
            browser_uri = claim.get('browser_uri') if valid_eve_authorize_url(claim.get('browser_uri')) else None
            self.pairing = SimpleNamespace(browser=True, durable=True, scope=claim['scope'],
                browser_uri=browser_uri, deadline=time.monotonic() + remaining)
            if browser_uri and remaining > 0:
                self.pairing_notice('Запрос сохранён. Нажмите «Открыть EVE». Сбор выключен.')
            elif remaining <= 0:
                self.pairing_notice('Ссылка истекла. Нажмите «Открыть EVE», чтобы восстановить вход.')
            else:
                self.pairing_notice('Вход не завершён. Нажмите «Открыть EVE», чтобы продолжить.')
        except Exception:
            self.browser_paused = True
            self.pairing_notice('Защищённый запрос не читается. Он сохранён; проверьте устройства на сайте и обратитесь в поддержку. Новая привязка заблокирована.')

    def begin_browser(self):
        self.pairing = SimpleNamespace(browser=True, durable=True, scope='combat:write', browser_uri=None, deadline=0)
        self.pairing_notice('Откроется официальный вход EVE. После выбора персонажа вернитесь в приложение; сбор останется выключен.')
        self.work('browser_start', lambda: self.browser_recovery.start('combat:write'))

    def open_browser(self):
        if active_url(self.pairing):
            self.open_pairing_browser()
            return
        if self.browser_outstanding():
            self.restore_browser()
            if active_url(self.pairing):
                self.open_pairing_browser()
            elif not self.busy and not blocked(self):
                self.pairing_notice('Восстанавливаю официальную ссылку EVE для сохранённого запроса. Новое устройство не создаётся; очередь и привязка не изменены.')
                self.work('browser_recover_url', self.browser_recovery.recover_url)
            return
        if not self.busy and not self.uploader:
            self.pair()
            return
        self.pairing_notice('Официальная ссылка EVE сейчас недоступна. Очередь и привязка не изменены; попробуйте позже или нажмите «Повторить привязку».')

    def retry_browser(self):
        if self.busy or blocked(self):
            return
        self.browser_paused = False
        self.restore_browser()
        if self.pairing and getattr(self.pairing, 'durable', False):
            self.work('browser_token', self.browser_recovery.redeem)

    def browser_result(self, kind, result, error):
        if not kind.startswith('browser_'):
            return False
        self.busy = False
        self.redemption_pending = False
        if error:
            self.browser_paused = True
            if kind == 'browser_start' and not self.browser_outstanding():
                self.pairing = None
            self.pairing_notice('Не удалось завершить вход. Проверьте интернет и нажмите «Открыть EVE». Данные сохранены.')
        elif kind in ('browser_start', 'browser_recover_url'):
            self.browser_paused = False
            if valid_eve_authorize_url(result):
                claim = self.browser_pending.load()
                remaining = max(0, min(300, claim.get('approval_expires', 0) - time.time())) if claim else 0
                self.pairing = SimpleNamespace(browser=True, durable=True, scope=claim['scope'] if claim else 'combat:write',
                    browser_uri=result, deadline=time.monotonic() + remaining)
                self.pairing_notice('Официальный EVE-вход открыт. После подтверждения вернитесь в приложение; сбор останется выключен.')
            else:
                self.restore_browser()
            self.open_pairing_browser()
        elif result:
            # Recovery service already validated durable credential readback.
            self.uploader = Uploader(result)
            self.upload_enabled = False
            self.browser_required_probe = True
            self.pairing = None
            self.show_identity()
            self.pairing_panel.pack_forget()
        self.browser_next = time.monotonic() + 5
        return True

    def tick_browser(self):
        if self.browser_outstanding() and not self.pairing and not blocked(self):
            self.restore_browser()
        if (not self.busy and not blocked(self) and self.pairing
                and getattr(self.pairing, 'durable', False) and not self.browser_paused
                and time.monotonic() >= self.browser_next):
            self.work('browser_token', self.browser_recovery.redeem)
