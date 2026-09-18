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
        return  # Keep original credentials and pending rows; no new requests.

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
        names = ', '.join(c['name'] for c in self.uploader.credentials['characters']) if self.uploader else 'прежняя привязка недоступна'
        problem = self.problem or (self.uploader and (self.uploader.failures or self.uploader.paused))
        state = 'ошибка доставки; данные сохранены' if problem else ('отправка разрешена' if self.enabled else 'сохранено локально, отправка отключена')
        self.label.config(text=f'Прежние наблюдения · {names}: {self.queue.count()} · {state} · принято за запуск: {self.ack_count}. Новые события сюда не добавляются.')
        self.app.window.after(1000, self.tick)

    def close(self):
        self.closed = True
        self.stop()
        self.queue.close()
