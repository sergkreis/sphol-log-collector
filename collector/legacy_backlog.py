"""Delivery-only legacy observations: original queue and credential, never capture."""
import queue
import threading
import time
from tkinter import ttk, messagebox
from .expanded import ExpandedQueue
from .credentials import CredentialStore
from .transport import Uploader


class LegacyBacklog:
    def __init__(self, app):
        self.app = app
        self.queue = ExpandedQueue(app.queue.path.parent)
        self.store = CredentialStore(app.queue.path.parent / 'credentials-v2.dpapi')
        self.uploader = None
        self.enabled = self.busy = self.closed = self.problem = False
        self.ack_count = 0
        self.results = queue.Queue()
        self.label = ttk.Label(app.settings, wraplength=660)
        self.label.pack(anchor='w')
        try:
            credentials = self.store.load()
            if credentials and credentials['scope'] == 'gamelogs:write':
                self.uploader = Uploader(credentials)
        except Exception:
            pass  # Preserve unreadable/expired credentials and all rows untouched.
        self.tick()

    def authorize(self):
        self.enabled = False
        if not self.queue.count() or not self.uploader or self.uploader.paused:
            return
        names = ', '.join(c['name'] for c in self.uploader.credentials['characters'])
        self.enabled = messagebox.askyesno(
            'Сохранённые наблюдения',
            f'Отправить сохранённые наблюдения персонажа {names} по его прежней привязке? '
            'Новые события собираются только для текущей привязки. Отказ сохраняет старую очередь '
            'и не мешает текущему сбору. «Остановить сбор» остановит и новые запросы этой очереди.')

    def stop(self):
        self.enabled = False

    def tick(self):
        if self.closed:
            return
        try:
            snapshot, failed = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if failed:
                self.problem = True
                self.enabled = False
            else:
                try:
                    self.queue.complete_upload(snapshot.accepted, snapshot.retry_updates)
                except Exception:
                    self.problem = True
                    self.enabled = False
                    snapshot.accepted = []
                else:
                    if snapshot.accepted:
                        from .diagnostics import emit
                        emit('upload.ack', accepted=len(snapshot.accepted), rejected=0)
                if snapshot.accepted:
                    self.ack_count += len(snapshot.accepted)
                    self.problem = False
        if self.enabled and not self.busy and self.uploader and not self.uploader.paused and time.monotonic() >= self.uploader.next_try:
            from .network_gui import Snapshot
            snapshot = Snapshot(self.queue.batch(listeners={c['name'] for c in self.uploader.credentials['characters']}), self.queue)
            if snapshot.events and self.uploader.ready(snapshot):
                self.busy = True
                uploader = self.uploader
                def run():
                    try:
                        uploader.upload(snapshot)
                        self.results.put((snapshot, False))
                    except Exception:
                        self.results.put((snapshot, True))
                threading.Thread(target=run, daemon=True).start()
        names = ', '.join(c['name'] for c in self.uploader.credentials['characters']) if self.uploader else 'прежняя привязка недоступна'
        problem = self.problem or (self.uploader and (self.uploader.failures or self.uploader.paused))
        state = 'ошибка доставки; данные сохранены' if problem else ('отправка разрешена' if self.enabled else 'хранится локально')
        self.label.config(text=f'Прежние наблюдения · {names}: {self.queue.count()} · {state} · принято за запуск: {self.ack_count}. Новые события сюда не добавляются.')
        self.app.window.after(1000, self.tick)

    def close(self):
        self.closed = True
        self.stop()
        self.queue.close()
