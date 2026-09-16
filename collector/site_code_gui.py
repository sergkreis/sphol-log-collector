"""Explicit site code entry; workers only return messages, never touch Tk."""
import queue
import threading
import time
from tkinter import ttk, StringVar
from .site_code import PendingStore, Redemption
from .transport import Uploader, SiteCodeFailure
from .diagnostics import emit


class SiteCodeControls:
    def __init__(self, app):
        self.app = app
        self.pending = PendingStore(app.queue.path.parent / 'site-redemption.dpapi')
        self.redemption = Redemption(self.pending, app.store)
        self.busy = False
        self.next_try = 0
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
        self.label = ttk.Label(app.network_area, wraplength=680, text='Выберите то же разрешение, что на сайте. combat:write — бой; gamelogs:write — бой и три наблюдения. Сбор не включается.')
        self.label.pack(anchor='w')
        if self.uncertain:
            self.label.config(text='Есть незавершённая привязка. «Повторить» восстановит тот же запрос; новый код не используется.')
        self.tick()

    def submit(self):
        emit('site.click')
        app = self.app
        if self.busy or time.monotonic() < self.next_try or app.busy or app.pairing or app.tailer or getattr(app, 'recovered_token', None):
            return
        if app.uploader and not self.uncertain:
            self.label.config(text='Существующая привязка сохранена. Для другого устройства используйте отдельную копию приложения.')
            return
        if getattr(app, 'updates', None) and app.updates.busy:
            return
        self.label.config(text='Сохраняем защищённый запрос и связываемся с SPHOL… Сбор выключен.')
        try:
            self.redemption.prepare(self.code.get(), self.scope.get())
        except Exception as exc:
            emit('site.pending.save', 'error', error=exc)
            self.uncertain = self.pending.path.exists()
            self.label.config(text='Проверьте формат кода и защищённое хранилище Windows. Запрос не отправлен.')
            return
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
                self.next_try = time.monotonic() + max(5, min(60, getattr(error, 'retry_after', 0)))
                code = error.code if isinstance(error, SiteCodeFailure) else 'connection_or_storage'
                messages = {'not_found': 'Сервер не поддерживает этот способ.', 'expired_token': 'Код или окно восстановления истекли.',
                    'scope_consent_required': 'Разрешение не совпадает с согласием на сайте.', 'active_installation_limit': 'Достигнут лимит устройств.',
                    'not_member': 'Сайт не подтвердил членство.', 'invalid_grant': 'Код уже занят другим запросом.'}
                self.label.config(text=messages.get(code, 'Привязка не подтверждена.') + ' Запрос сохранён; повторите позже. При отказе проверьте устройства на сайте; не удаляйте очередь.')
            else:
                self.app.uploader = Uploader(result)
                self.app.upload_enabled = False
                self.uncertain = False
                self.app.show_identity()
                self.label.config(text='Привязка сохранена. Для сбора нажмите «Начать сбор».')
        if self.busy and time.monotonic() >= self.deadline:
            self.label.config(text='Ответ задерживается. Сбор не начат; запрос сохранён для восстановления. Ожидаем завершения сети.')
        self.refresh()
        self.app.window.after(250, self.tick)


def blocked(app):
    controls = getattr(app, 'site_codes', None)
    return bool(controls and (controls.busy is True or controls.uncertain is True))
