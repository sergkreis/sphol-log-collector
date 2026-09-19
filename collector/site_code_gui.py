"""Explicit site code entry; workers only return messages, never touch Tk."""
import http.client
import queue
import threading
import time
import webbrowser
from tkinter import ttk, StringVar, messagebox
from .site_code import PendingStore, Redemption
from .transport import Uploader, SiteCodeFailure, PAIR_URI
from .diagnostics import emit
from .clipboard import bind_paste, paste


class SiteCodeControls:
    def __init__(self, app):
        self.app = app
        self.pending = PendingStore(app.queue.path.parent / 'site-redemption.dpapi')
        self.redemption = Redemption(self.pending, app.store)
        self.busy = False
        self.next_try = 0
        self.attempts = 0
        self.auto_retry = False
        self.results = queue.Queue()
        self.uncertain = self.pending.path.exists()
        self.frame = frame = ttk.Frame(app.network_area)
        frame.pack(fill='x')
        self.step = ttk.Label(frame, text='Привяжите персонажа', style='Status.TLabel')
        self.step.pack(anchor='w', pady=(8, 12))
        self.get_code_button = ttk.Button(frame, text='1   Получить код на сайте', command=self.review)
        self.get_code_button.pack(anchor='w', pady=(0, 12))
        self.entry_label = ttk.Label(frame, text='2   Вставьте код с сайта', style='Muted.TLabel')
        self.entry_label.pack(anchor='w')
        self.code = StringVar(master=app.window)
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=6)
        self.entry = ttk.Entry(row, textvariable=self.code, width=24)
        self.entry.pack(side='left')
        bind_paste(self.entry)
        self.paste_button = ttk.Button(row, text='Вставить', width=8, command=lambda: paste(self.entry))
        self.paste_button.pack(side='left', padx=4)
        # New website codes have one supported consent; recovery keeps its saved scope.
        self.scope = StringVar(master=app.window, value='gamelogs:write')
        self.button = ttk.Button(row, text='Привязать', command=self.submit)
        self.button.pack(side='left', padx=8)
        self.links = ttk.Frame(frame)
        self.links.pack(fill='x', pady=4)
        self.legacy_button = ttk.Button(app.settings, text='Другой способ привязки: через браузер', command=app.pair)
        self.legacy_button.pack_forget()
        self.recovery_actions = ttk.Frame(frame)
        self.recovery_actions.pack(fill='x')
        self.review_button = ttk.Button(self.recovery_actions, text='Проверить устройства на сайте', command=self.review)
        self.review_button.pack(anchor='w')
        self.abandon_button = ttk.Button(self.recovery_actions, text='Завершить восстановление…', command=self.abandon)
        self.abandon_button.pack(anchor='w')
        self.label = ttk.Label(frame, wraplength=510, text='Только боевые события. Небоевые данные не собираются.\nПривязка не включает сбор.')
        self.label.pack(anchor='w')
        if self.uncertain:
            self.label.config(text='Привязка не завершена. Повторите сохранённый запрос или проверьте устройства на сайте. Новый код пока не нужен.')
            try:
                saved = self.pending.load()
                if saved:
                    self.scope.set(saved['scope'])
            except Exception:
                self.label.config(text='Защищённый запрос не читается. Проверьте и отзовите возможное устройство на сайте, затем завершите восстановление; зашифрованная копия будет сохранена.')
        self.tick()

    def review(self):
        emit('site.review', 'start')
        try:
            if not webbrowser.open(PAIR_URI):
                raise OSError()
        except Exception as exc:
            emit('site.review', 'error', error=exc)
            self.label.config(text='Откройте https://sphol.com/collector/pair вручную, войдите нужным персонажем и проверьте / отзовите устройство. Код в адрес не вставляйте.')

    def abandon(self):
        # Never detach a worker: late credentials still belong to this request.
        app = self.app
        if self.busy or not self.uncertain or app.busy or app.pairing or app.tailer or getattr(app, 'recovered_token', None):
            return
        if getattr(app, 'updates', None) and app.updates.busy:
            return
        if not messagebox.askyesno('Проверка устройств',
                'Устройство уже могло быть привязано, даже если ответ потерян или код истёк. '
                'Вы проверили устройства на сайте и отозвали устройство от этой попытки (если оно появилось)? '
                'Только после этого можно брать новый код. Продолжить? '
                'Зашифрованная копия запроса останется локально; существующая привязка и очередь не изменятся.', parent=app.window):
            return
        self.auto_retry = False
        try:
            current = app.store.load()
            recovered = Uploader(current) if current is not None else None
            self.pending.archive()
        except Exception as exc:
            emit('site.pending.archive', 'error', error=exc)
            self.label.config(text='Не удалось сохранить архив восстановления (возможно, заполнен). Запрос сохранён. Повторите восстановление или обратитесь в поддержку; очередь не изменена.')
            return
        if recovered is not None and not app.uploader:
            app.uploader = recovered
            app.upload_enabled = False
            app.show_identity()
        self.uncertain = False
        self.next_try = 0
        self.code.set('')
        self.label.config(text='Восстановление завершено после вашей проверки. Зашифрованный архив сохранён. Можно ввести новый код или выбрать браузерный способ, если нет существующей привязки. Сбор выключен.')
        self.refresh()

    def submit(self, automatic=False):
        emit('site.retry' if automatic else 'site.click')
        app = self.app
        if self.busy or time.monotonic() < self.next_try or app.busy or app.pairing or app.tailer or getattr(app, 'recovered_token', None):
            return
        if app.uploader and not self.uncertain:
            self.label.config(text='Существующая привязка сохранена. Для другого устройства используйте отдельную копию приложения.')
            return
        if getattr(app, 'updates', None) and app.updates.busy:
            return
        if not automatic:
            self.attempts = 0
        self.auto_retry = False
        self.label.config(text='Сохраняем защищённый запрос и связываемся с SPHOL… Сбор выключен.')
        website_consent = False
        if self.uncertain and self.scope.get() == 'combat:write':
            if automatic:
                return
            website_consent = messagebox.askyesno('Восстановление кода с сайта',
                'Повторить сохранённый код с разрешением, которое вы подтвердили на сайте? '
                'Приложение собирает и отправляет только боевые события. '
                'Исходный запрос сохраняется; существующая привязка и очередь не меняются.', parent=app.window)
            if not website_consent:
                return
        try:
            self.redemption.prepare(self.code.get(), self.scope.get() if self.uncertain else 'gamelogs:write')
        except Exception as exc:
            emit('site.pending.save', 'error', error=exc)
            self.uncertain = self.pending.path.exists()
            self.label.config(text='Проверьте формат кода и защищённое хранилище Windows. Запрос не отправлен.')
            return
        self.attempts += 1
        self.uncertain = self.busy = True
        self.code.set('')
        self.deadline = time.monotonic() + 20
        self.refresh()
        def run():
            try:
                result = self.redemption.redeem(website_consent=True) if website_consent else self.redemption.redeem()
                self.results.put((result, None))
            except Exception as exc:
                self.results.put((None, exc))
        threading.Thread(target=run, daemon=True).start()

    def refresh(self):
        self.legacy_button.config(state='disabled' if self.uncertain or self.busy or self.app.uploader else 'normal')
        self.button.config(state='disabled' if self.busy or time.monotonic() < self.next_try else 'normal')
        self.abandon_button.config(state='normal' if self.uncertain and not self.busy else 'disabled')
        self.entry.config(state='disabled' if self.uncertain else 'normal')
        self.paste_button.config(state='disabled' if self.uncertain else 'normal')
        self.button.config(text='Повторить восстановление' if self.uncertain else 'Привязать', width=0 if self.uncertain else 10)
        if (self.app.uploader and not self.uncertain) or (hasattr(self.app, 'pairing_panel') and self.app.pairing_panel.winfo_manager() == 'pack'):
            self.frame.pack_forget()
        else:
            self.frame.pack(fill='x', pady=(4, 0))
        for widget in (self.review_button, self.abandon_button):
            if self.uncertain:
                widget.pack(anchor='w', pady=(4, 0))
            else:
                widget.pack_forget()
        if self.uncertain:
            self.step.config(text='Восстановление привязки')
            self.get_code_button.pack_forget()
            self.entry_label.pack_forget()
            self.entry.pack_forget()
            self.paste_button.pack_forget()
            self.legacy_button.pack_forget()
        else:
            self.step.config(text='Привяжите персонажа')
            self.get_code_button.pack(anchor='w', pady=(0, 12), after=self.step)
            self.entry_label.pack(anchor='w', after=self.get_code_button)
            self.entry.pack(side='left', before=self.button)
            self.paste_button.pack(side='left', before=self.button, padx=4)
            if hasattr(self.app, 'danger_area') and isinstance(self.app.danger_area, ttk.Frame):
                self.legacy_button.pack(anchor='w', pady=8, before=self.app.danger_area)
            else:
                self.legacy_button.pack(anchor='w', pady=8)
        # Onboarding replaces the capture view, never competes with Start.
        if hasattr(self.app, 'capture_area') and not self.app.settings_open:
            if not self.app.uploader or self.uncertain:
                self.app.capture_area.pack_forget()
            else:
                self.app.capture_area.pack(fill='x', after=self.app.identity_area)


    def tick(self):
        try:
            result, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if error:
                delay = max(5 * 2 ** min(self.attempts - 1, 3), getattr(error, 'retry_after', 0))
                self.next_try = time.monotonic() + delay
                # Only transport failures, never disk/credential errors, retry automatically.
                transient = isinstance(error, SiteCodeFailure) and error.status in (429, 503)
                transient = transient or isinstance(error, (TimeoutError, ConnectionError, http.client.HTTPException))
                self.auto_retry = transient and self.attempts < 3
                code = error.code if isinstance(error, SiteCodeFailure) else 'connection_or_storage'
                messages = {'not_found': 'Сервер не поддерживает этот способ.', 'expired_token': 'Код или окно восстановления истекли. Устройство уже могло быть привязано: отзовите его на сайте перед новым кодом.',
                    'scope_consent_required': 'Разрешение не совпадает с согласием на сайте.', 'active_installation_limit': 'Достигнут лимит устройств.',
                    'not_member': 'Сайт не подтвердил членство.', 'invalid_grant': 'Код уже занят другим запросом.'}
                action = ' Повторим тот же запрос автоматически после ожидания (не более трёх попыток).' if self.auto_retry else ' Можно повторить вручную или проверить / отозвать устройство на сайте и завершить восстановление кнопкой ниже.'
                self.label.config(text=messages.get(code, 'Привязка не подтверждена.') + ' Защищённый запрос сохранён.' + action)
            else:
                self.auto_retry = False
                self.app.uploader = Uploader(result)
                self.app.upload_enabled = False
                self.uncertain = False
                self.app.show_identity()
                self.label.config(text='Привязка сохранена. Для сбора нажмите «Начать сбор».')
        if self.busy and time.monotonic() >= self.deadline:
            self.label.config(text='Ответ задерживается. Сбор не начат; запрос сохранён для восстановления. Ожидаем завершения сети; новую привязку не создаём.')
        if self.auto_retry and not self.busy and time.monotonic() >= self.next_try:
            self.submit(automatic=True)
        self.refresh()
        self.app.window.after(250, self.tick)


def blocked(app):
    controls = getattr(app, 'site_codes', None)
    return bool(controls and (controls.busy is True or controls.uncertain is True))
