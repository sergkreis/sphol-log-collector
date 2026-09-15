"""Pairing recovery presentation. Never display exception bodies or device keys."""
import http.client
import queue
import re
import threading
import time
import webbrowser
from tkinter import ttk, TclError
from .transport import PAIR_URI, HTTPFailure, ProtocolError


def active_url(pairing):
    if not pairing or not getattr(pairing, 'browser', False):
        return None
    url = getattr(pairing, 'browser_uri', None)
    if (time.monotonic() >= pairing.deadline or not isinstance(url, str)
            or re.fullmatch(re.escape(PAIR_URI) + r'#[A-Za-z0-9_-]{43}', url) is None):
        return None
    return url


def failure_text(error):
    if isinstance(error, HTTPFailure):
        reason = f'Сервер отклонил запрос (HTTP {error.status}).'
    elif isinstance(error, (OSError, http.client.HTTPException)):
        reason = 'Не удалось связаться с SPHOL: проверьте сеть или повторите позже.'
    elif isinstance(error, ProtocolError):
        reason = 'SPHOL вернул неожиданный ответ. Повторите позже; если повторяется — сообщите поддержке.'
    else:
        reason = 'Внутренняя ошибка привязки. Повторите; если повторяется — сообщите поддержке.'
    return reason + ' Очередь и сохранённая привязка не изменены. Нажмите «Повторить привязку».'


class PairingUX:
    def init_pairing_ux(self):
        self.browser_results = queue.Queue()
        self.browser_job = None
        self.pair_attempt = 0
        self.pair_job = None
        self.pairing_panel = ttk.Frame(self.identity_area)
        self.pairing_message = ttk.Label(self.pairing_panel, wraplength=660, foreground='#e8be75')
        self.pairing_message.pack(anchor='w')
        actions = ttk.Frame(self.pairing_panel)
        actions.pack(anchor='w', pady=(4, 0))
        self.browser_button = ttk.Button(actions, text='Открыть подтверждение в браузере', command=self.open_pairing_browser)
        self.browser_button.pack(side='left')
        self.link_button = ttk.Button(actions, text='Скопировать ссылку', command=self.copy_pairing_link)
        self.link_button.pack(side='left', padx=(8, 0))
        self.retry_button = ttk.Button(self.pairing_panel, text='Повторить привязку', command=self.retry_pairing)
        self.retry_button.pack(anchor='w', pady=(4, 0))

    def pairing_notice(self, text):
        if hasattr(self, 'pairing_panel'):
            self.pairing_message.config(text=text)
            self.pairing_panel.pack(fill='x', pady=(6, 0))
        else:
            self.connection.config(text=text)

    def refresh_pairing_ux(self):
        if not hasattr(self, 'pairing_panel'):
            return
        valid = active_url(self.pairing)
        self.browser_button.config(state='normal' if valid and not self.browser_job else 'disabled')
        self.link_button.config(state='normal' if valid else 'disabled')
        self.retry_button.config(state='disabled' if self.busy or self.pairing or getattr(self, 'redemption_uncertain', False) else 'normal')

    def copy_pairing_link(self):
        url = active_url(self.pairing)
        if not url:
            return
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(url)
        except TclError:
            self.pairing_message.config(text='Не удалось скопировать ссылку. Нажмите «Открыть подтверждение в браузере».')
        else:
            self.pairing_message.config(text='Ссылка скопирована. Вставьте её в адресную строку браузера. Не передавайте ссылку другим людям.')

    def open_pairing_browser(self):
        url = active_url(self.pairing)
        if not url or self.browser_job:
            return
        owner = self.pairing
        self.browser_job = (owner, time.monotonic() + 10)
        def run():
            try:
                opened = bool(webbrowser.open(url))
            except Exception:
                opened = False
            self.browser_results.put((owner, opened))
        threading.Thread(target=run, daemon=True).start()

    def poll_browser(self):
        if not hasattr(self, 'browser_results'):
            return
        try:
            owner, opened = self.browser_results.get_nowait()
        except queue.Empty:
            if not self.browser_job or time.monotonic() < self.browser_job[1]:
                return
            owner, opened = self.browser_job[0], False
        if not self.browser_job or self.browser_job[0] is not owner:
            return
        self.browser_job = None
        if owner is not self.pairing or not active_url(owner):
            return
        self.pairing_message.config(text=(
            'Подтвердите привязку на sphol.com. Если окно не появилось — откройте браузер кнопкой или скопируйте ссылку.' if opened else
            'Ссылка готова, но браузер не открылся. Нажмите «Открыть подтверждение в браузере» или «Скопировать ссылку» и вставьте её в браузер.'))

    def pairing_failed(self, text):
        self.pair_attempt = getattr(self, 'pair_attempt', 0) + 1
        self.pair_job = None
        self.browser_job = None
        self.pairing = None
        self.resume_after_pair = False
        self.clear_pairing_code()
        self.pairing_notice(text)

    def retry_pairing(self):
        if getattr(self, 'recovered_token', None) and not self.busy:
            self.results.put(('token', self.recovered_token, None, self.pair_attempt))
            self.busy = True
            return
        if not self.busy and not self.pairing and not getattr(self, 'redemption_uncertain', False):
            self.start()
