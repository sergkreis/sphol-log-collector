"""Independent opt-in schema 2 controls; never consume local-only recordings."""
import queue
import threading
import time
import webbrowser
from .gui import ttk, messagebox
from .expanded import ExpandedQueue, SCOPE, CONSENT, UNSUPPORTED, capture
from .credentials import CredentialStore
from .transport import Pairing, Uploader, PAIR_URI


class ExpandedControls:
    def __init__(self, app):
        self.app = app
        self.queue = ExpandedQueue(app.queue.path.parent)
        self.store = CredentialStore(app.queue.path.parent / 'credentials-v2.dpapi')
        self.uploader = self.pairing = self.tailer = None
        self.busy = self.enabled = self.closed = False
        self.results = queue.Queue()
        frame = ttk.LabelFrame(app.network_area, text='Отдельный режим v2: все Gamelogs', padding=8)
        # Server support is unavailable. Preserve separate consent/state internals,
        # but do not mount technical controls or imply a usable public feature.
        ttk.Label(frame, text='Все типы, включая личные уведомления. Без Chatlogs и диагностики. Локальная запись выше по-прежнему НЕ отправляется.', wraplength=620).pack(anchor='w')
        self.status = ttk.Label(frame, text='Отправка v2 выключена. Нужно отдельное согласие и подтверждение в браузере.', wraplength=620)
        self.status.pack(anchor='w')
        self.pending = ttk.Label(frame)
        self.pending.pack(anchor='w')

        from .gui import tk
        self.code = tk.StringVar(master=app.window, value='')
        self.code_field = ttk.Entry(frame, textvariable=self.code, state='readonly', width=22)
        self.code_field.pack(anchor='w')
        buttons = ttk.Frame(frame)
        buttons.pack(anchor='w')
        self.approve_button = ttk.Button(buttons, text='Подтвердить v2 в браузере…', command=self.approve)
        self.approve_button.pack(side='left')
        self.start_button = ttk.Button(buttons, text='Включить отправку всех Gamelogs…', command=self.start)
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(buttons, text='Остановить v2', command=self.stop)
        self.stop_button.pack(side='left')
        ttk.Button(frame, text='Удалить очередь v2…', command=self.clear).pack(anchor='w')
        try:
            credentials = self.store.load()
            if credentials and credentials['scope'] == SCOPE:
                self.uploader = Uploader(credentials)
                self.status.config(text='Привязка v2: ' + ', '.join(c['name'] for c in credentials['characters']) + '. Отправка выключена.')
        except Exception:
            self.status.config(text='Привязка v2 недоступна; подтвердите заново. Старая боевая привязка не изменена.')
        self.tick()

    def work(self, kind, function):
        self.busy = True
        def run():
            try:
                self.results.put((kind, function(), False))
            except Exception:
                self.results.put((kind, None, True))
        threading.Thread(target=run, daemon=True).start()

    def approve(self):
        if self.busy or self.pairing or self.tailer:
            return
        if not messagebox.askyesno('Новое согласие на отправку v2', CONSENT):
            return
        self.pairing = Pairing(scope=SCOPE)
        self.work('pair', self.pairing.start)

    def start(self):
        if self.busy or self.pairing or self.tailer:
            return
        if not self.uploader or self.uploader.paused:
            self.status.config(text=UNSUPPORTED)
            return
        if not messagebox.askyesno('Отправка всех Gamelogs', CONSENT):
            return
        try:
            self.tailer = capture(self.app.log_root, self.queue, consent=True, credentials=self.uploader.credentials)
            self.enabled = True
            self.status.config(text='Отправка v2 включена: все новые Gamelogs привязанного персонажа и очередь v2. Подтверждение сервера ещё ожидается.')
        except Exception:
            self.status.config(text='Сбор v2 не начат: проверьте папку Gamelogs. Очередь сохранена.')

    def stop(self):
        self.enabled = False
        self.tailer = None
        self.status.config(text='v2 выключен; очередь сохранена. Уже начатый запрос может завершиться.')

    def clear(self):
        if self.busy:
            return
        if messagebox.askyesno('Удалить очередь v2?', 'Удалить только неотправленные расширенные события? Боевая очередь и локальные записи сохранятся.'):
            self.stop()
            self.queue.clear()

    def tick(self):
        if self.closed:
            return
        try:
            kind, result, failed = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if failed:
                self.stop()
                self.pairing = None
                self.code.set('')
                self.status.config(text=UNSUPPORTED)
            elif kind == 'pair':
                self.code.set(result)
                self.status.config(text='Введите код на sphol.com и подтвердите именно отправку всех Gamelogs. Здесь пароль не вводится.')
                webbrowser.open(PAIR_URI)
            elif kind == 'token' and result:
                self.code.set('')
                self.pairing = None
                try:
                    if result['scope'] != SCOPE:
                        raise ValueError('Scope mismatch')
                    uploader = Uploader(result)
                    self.store.save(result)
                    self.uploader = uploader
                    self.status.config(text='v2 подтверждён для ' + ', '.join(c['name'] for c in result['characters']) + '. Для отправки отдельно нажмите «Включить».')
                except Exception:
                    self.status.config(text=UNSUPPORTED)
            elif kind == 'upload':
                snapshot, status = result
                self.queue.acknowledge(snapshot.accepted)
                if status:
                    self.status.config(text=status)
                if self.uploader.paused:
                    self.enabled = False
        if self.pairing and time.monotonic() >= self.pairing.deadline and self.code.get():
            self.code.set('')
            self.pairing = None
            self.status.config(text='Код v2 истёк; отправка не включена.')
        if self.tailer:
            try:
                self.tailer.poll()
            except Exception:
                self.tailer = None
                self.status.config(text='Сбор v2 остановлен: лимит очереди или ошибка чтения. Данные сохранены; оригиналы в Gamelogs.')
        if not self.busy:
            if self.pairing:
                self.work('token', self.pairing.poll)
            elif self.enabled and self.uploader and not self.uploader.paused and time.monotonic() >= self.uploader.next_try:
                from .network_gui import Snapshot
                snapshot = Snapshot(self.queue.batch(listeners={c['name'] for c in self.uploader.credentials['characters']}))
                if snapshot.events:
                    uploader = self.uploader
                    self.work('upload', lambda: (snapshot, uploader.upload(snapshot)))
        self.pending.config(text=f'Отдельная очередь v2: {self.queue.count()} событий (до 10 000 / 16 МиБ).')
        self.approve_button.config(state='disabled' if self.busy or self.pairing or self.tailer else 'normal')
        self.start_button.config(state='disabled' if self.busy or self.pairing or self.tailer else 'normal')
        self.app.window.after(1000, self.tick)

    def close(self):
        self.closed = True
        self.enabled = False
        self.tailer = None
        self.queue.close()
