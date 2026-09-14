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
        frame = ttk.LabelFrame(app.network_area, text='Наблюдения полёта · отдельное разрешение', padding=8)
        frame.pack(fill='x', pady=(8, 0))
        self.ack_count = 0
        ttk.Label(frame, text='Только распознанные сигналы без личных уведомлений и маршрутов. Локальная запись НЕ отправляется.', wraplength=620).pack(anchor='w')
        self.status = ttk.Label(frame, text='Отправка v2 выключена. Нужно отдельное согласие и подтверждение в браузере.', wraplength=620)
        self.status.pack(anchor='w')
        self.pending = ttk.Label(frame)
        self.pending.pack(anchor='w')

        from .gui import tk
        self.code = tk.StringVar(master=app.window, value='')
        self.code_field = ttk.Entry(frame, textvariable=self.code, state='readonly', width=22)
        # Code is displayed only while browser approval is pending.
        buttons = ttk.Frame(frame)
        buttons.pack(anchor='w')
        self.approve_button = ttk.Button(buttons, text='Разрешить наблюдения в браузере…', command=self.approve)
        self.approve_button.pack(side='left')
        self.start_button = ttk.Button(buttons, text='Включить отправку сигналов…', command=self.start)
        # Main capture button controls both approved streams.
        self.stop_button = ttk.Button(buttons, text='Остановить v2', command=self.stop)
        # Main stop always stops both streams.
        ttk.Button(app.danger_area, text='Удалить очередь наблюдений…', command=self.clear).pack(side='left')
        self.last_ack = ttk.Label(frame, text='Наблюдения: подтверждения сервера ещё не было.', wraplength=620)
        self.last_ack.pack(anchor='w')
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

    def start(self, integrated=False):
        if self.busy or self.pairing or self.tailer:
            return
        if not self.uploader or self.uploader.paused:
            self.status.config(text=UNSUPPORTED)
            return
        if not integrated and not messagebox.askyesno('Отправка разрешённых сигналов', CONSENT):
            return
        try:
            self.tailer = capture(self.app.log_root, self.queue, consent=True, credentials=self.uploader.credentials)
            self.enabled = True
            self.status.config(text='Наблюдения включены: только три разрешённых сигнала привязанного персонажа. Подтверждение сервера ещё ожидается.')
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
                self.code_field.pack(anchor='w')
                self.status.config(text='Введите код на sphol.com: отдельное разрешение gamelogs:write только для трёх сигналов. Поддержка ещё не подтверждена.')
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
                    self.status.config(text='v2 подтверждён для ' + ', '.join(c['name'] for c in result['characters']) + '. Для отправки нажмите «Начать сбор».')
                except Exception:
                    self.status.config(text=UNSUPPORTED)
            elif kind == 'upload':
                snapshot, status = result
                self.queue.acknowledge(snapshot.accepted)
                if snapshot.accepted:
                    self.ack_count += len(snapshot.accepted)
                    self.last_ack.config(text=f'Сервер подтвердил наблюдения: {self.ack_count} за запуск · {time.strftime("%H:%M:%S")} (время компьютера)')
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
        from .queue_status import queue_summary
        names = {c['name'] for c in self.uploader.credentials['characters']} if self.uploader else set()
        self.pending.config(text='Наблюдения · ' + queue_summary(self.queue, names))
        if not self.code.get():
            self.code_field.pack_forget()
        self.approve_button.config(state='disabled' if self.busy or self.pairing or self.tailer else 'normal')
        self.start_button.config(state='disabled' if self.busy or self.pairing or self.tailer else 'normal')
        self.app.window.after(1000, self.tick)

    def close(self):
        self.closed = True
        self.enabled = False
        self.tailer = None
        self.queue.close()
