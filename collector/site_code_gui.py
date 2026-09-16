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
        frame = ttk.Frame(app.network_area)
        frame.pack(fill='x')
        ttk.Label(frame, text='Ввести код с сайта').pack(anchor='w')
        self.code = StringVar(master=app.window)
        self.entry = ttk.Entry(frame, textvariable=self.code, width=30)
        self.entry.pack(side='left')
        self.scope = StringVar(master=app.window, value='combat:write')
        self.choice = ttk.Combobox(frame, textvariable=self.scope, values=('combat:write', 'gamelogs:write'), state='readonly', width=16)
        self.choice.pack(side='left')
        self.button = ttk.Button(frame, text='Привязать код / повторить', command=self.submit)
        self.button.pack(side='left')
        self.legacy_button = ttk.Button(app.network_area, text='Другой способ: подтверждение в браузере', command=app.pair)
        self.legacy_button.pack(anchor='w')
        self.review_button = ttk.Button(app.network_area, text='Проверить / отозвать устройства на сайте', command=self.review)
        self.review_button.pack(anchor='w')
        self.abandon_button = ttk.Button(app.network_area, text='Завершить восстановление после проверки устройств', command=self.abandon)
        self.abandon_button.pack(anchor='w')
        self.label = ttk.Label(app.network_area, wraplength=680, text='Выберите то же разрешение, что на сайте. combat:write — бой; gamelogs:write — бой и три наблюдения. Сбор не включается.')
        self.label.pack(anchor='w')
        if self.uncertain:
            self.label.config(text='Есть незавершённая привязка. «Повторить» восстановит тот же запрос; новый код не используется. Устройство уже могло быть привязано: проверьте устройства на сайте.')
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
        try:
            self.redemption.prepare(self.code.get(), self.scope.get())
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
                self.results.put((self.redemption.redeem(), None))
            except Exception as exc:
                self.results.put((None, exc))
        threading.Thread(target=run, daemon=True).start()

    def refresh(self):
        self.legacy_button.config(state='disabled' if self.uncertain or self.busy or self.app.uploader else 'normal')
        self.button.config(state='disabled' if self.busy or time.monotonic() < self.next_try else 'normal')
        self.abandon_button.config(state='normal' if self.uncertain and not self.busy else 'disabled')
        self.entry.config(state='disabled' if self.uncertain else 'normal')
        self.choice.config(state='disabled' if self.uncertain else 'readonly')

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
