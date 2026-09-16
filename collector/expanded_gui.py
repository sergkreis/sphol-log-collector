"""Current authorized observations; one Start/Stop with combat capture."""
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
        self.queue = ExpandedQueue(app.queue.path.parent / 'observations' / 'unbound')
        self.store = CredentialStore(app.queue.path.parent / 'observations' / 'credentials.dpapi')
        self.uploader = self.pairing = self.tailer = None
        self.busy = self.enabled = self.closed = False
        self.results = queue.Queue()
        frame = ttk.Frame(app.network_area)
        frame.pack(fill='x', pady=(8, 0))
        self.ack_count = 0
        self.problem = self.capture_problem = False
        self.baseline = self.queue.inserted_count
        ttk.Label(frame, text='Боевые события + наблюдения', style='Muted.TLabel').pack(anchor='w')
        ttk.Label(frame, text='Варп флота · дальность модуля · неуязвимость цели. Без Chatlogs и маршрутов.', style='Small.TLabel', wraplength=700).pack(anchor='w')
        self.summary = ttk.Label(frame, wraplength=700)
        self.summary.pack(anchor='w', pady=(6, 0))
        self.status = ttk.Label(app.settings, text='Наблюдения выключены. «Начать сбор» включает все разрешённые события.', wraplength=620)
        self.status.pack(anchor='w')
        self.pending = ttk.Label(app.settings)
        self.pending.pack(anchor='w')

        from .gui import tk
        self.code = tk.StringVar(master=app.window, value='')
        self.code_field = ttk.Entry(frame, textvariable=self.code, state='readonly', width=22)
        # Code is displayed only while browser approval is pending.
        buttons = ttk.Frame(frame)
        buttons.pack(anchor='w')
        self.approve_button = ttk.Button(buttons, text='Разрешить наблюдения', command=self.approve)
        self.approve_button.pack_forget()
        self.start_button = ttk.Button(buttons, text='Включить отправку сигналов…', command=self.start)
        # Main capture button controls both approved streams.
        self.stop_button = ttk.Button(buttons, text='Остановить v2', command=self.stop)
        # Main stop always stops both streams.
        ttk.Button(app.danger_area, text='Удалить очередь наблюдений…', command=self.clear).pack(side='left')
        self.last_ack = ttk.Label(app.settings, text='Наблюдения: подтверждения сервера ещё не было.', wraplength=620)
        self.last_ack.pack(anchor='w')
        if app.uploader and app.uploader.credentials['scope'] == SCOPE:
            self.bind(app.uploader.credentials)
        self.tick()

    def bind(self, credentials):
        from .transport import validate_credentials
        validate_credentials(credentials)
        installation = credentials['installation_id']
        path = self.app.queue.path.parent / 'observations' / installation
        if self.queue.path.parent != path:
            if self.busy or self.tailer:
                raise RuntimeError('Observation stream is active')
            new_queue = ExpandedQueue(path)
            self.queue.close()
            self.queue = new_queue
            self.baseline = self.queue.inserted_count
        self.uploader = Uploader(credentials)

    def work(self, kind, function):
        self.busy = True
        def run():
            try:
                self.results.put((kind, function(), False))
            except Exception:
                self.results.put((kind, None, True))
        threading.Thread(target=run, daemon=True).start()

    def approve(self):
        from .site_code_gui import blocked
        if blocked(self.app):
            return
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
            self.problem = self.capture_problem = False
            self.status.config(text='Наблюдения включены: только три разрешённых сигнала привязанного персонажа. Подтверждение сервера ещё ожидается.')
        except Exception as exc:
            from .core import PendingCaptureUnavailable, PENDING_CAPTURE_WARNING
            self.problem = True
            self.status.config(text=PENDING_CAPTURE_WARNING if isinstance(exc, PendingCaptureUnavailable) else 'Сбор v2 не начат: проверьте папку Gamelogs. Очередь сохранена.')

    def stop(self):
        self.enabled = False
        if self.tailer:
            self.tailer.stop()
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
                self.problem = True
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
                try:
                    self.queue.complete_upload(snapshot.accepted, snapshot.retry_updates)
                except Exception:
                    self.problem = True
                    self.enabled = False
                    snapshot.accepted = []
                    status = 'Не удалось сохранить подтверждение на диск. Очередь сохранена.'
                else:
                    if snapshot.accepted:
                        from .diagnostics import emit
                        emit('upload.ack', accepted=len(snapshot.accepted), rejected=0)
                if snapshot.accepted:
                    self.problem = False
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
            except Exception as exc:
                from .core import PendingCaptureUnavailable, PENDING_CAPTURE_WARNING
                self.tailer = None
                self.capture_problem = True
                self.problem = True
                self.status.config(text=PENDING_CAPTURE_WARNING if isinstance(exc, PendingCaptureUnavailable) else 'Сбор v2 остановлен: лимит очереди или ошибка чтения. Данные сохранены; оригиналы в Gamelogs.')
        if not self.busy:
            if self.pairing:
                self.work('token', self.pairing.poll)
            elif self.enabled and self.uploader and not self.uploader.paused and time.monotonic() >= self.uploader.next_try:
                from .network_gui import Snapshot
                snapshot = Snapshot(self.queue.batch(listeners={c['name'] for c in self.uploader.credentials['characters']}), self.queue)
                if snapshot.events and self.uploader.ready(snapshot):
                    uploader = self.uploader
                    self.work('upload', lambda: (snapshot, uploader.upload(snapshot)))
        from .queue_status import queue_summary
        names = {c['name'] for c in self.uploader.credentials['characters']} if self.uploader else set()
        self.pending.config(text='Наблюдения · ' + queue_summary(self.queue, names))
        self.refresh_summary()
        if not self.code.get():
            self.code_field.pack_forget()
        self.approve_button.config(state='disabled' if self.busy or self.pairing or self.tailer else 'normal')
        self.start_button.config(state='disabled' if self.busy or self.pairing or self.tailer else 'normal')
        self.app.window.after(1000, self.tick)

    def refresh_summary(self):
        from .queue_status import queue_counts
        names = {c['name'] for c in self.uploader.credentials['characters']} if self.uploader else set()
        counts = queue_counts(self.queue, names)
        if self.uploader and not self.uploader.paused:
            self.approve_button.pack_forget()
            self.summary.config(text=f'Наблюдения за запуск: собрано {self.queue.inserted_count - self.baseline} (все персонажи) · принято {self.ack_count} · к отправке {counts["eligible"]}')
        else:
            self.approve_button.pack_forget()
            self.summary.config(text='Наблюдения не отправляются · нужно разрешение')
        from .dashboard import refresh
        refresh(self.app)

    def close(self):
        self.closed = True
        self.enabled = False
        if self.tailer:
            self.tailer.stop()
        self.tailer = None
        self.queue.close()
