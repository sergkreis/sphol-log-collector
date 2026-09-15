"""Main-thread update consent/lifecycle; downloader never touches app state."""
import os
import queue
import sys
import threading
import time
from .gui import ttk, messagebox
from . import updater
from .version import VERSION


def active(app):
    expanded = getattr(app, 'expanded', None)
    legacy = getattr(app, 'legacy', None)
    return bool((legacy and (legacy.enabled or legacy.busy)) or app.tailer or app.local_capture or app.upload_enabled or app.busy or app.pairing or getattr(app, 'recovered_token', None) or (expanded and (expanded.enabled or expanded.tailer or expanded.busy or expanded.pairing)))


class UpdateControls:
    def __init__(self, app):
        self.app, self.busy = app, False
        self.results = queue.Queue()
        self.label = ttk.Label(app.footer, text=f'Версия {VERSION} · обновления только вручную', wraplength=660, style='Muted.TLabel')
        self.label.pack(anchor='w', pady=(0, 10))
        self.button = ttk.Button(app.footer, text='Проверить обновление', command=self.check)
        self.button.pack(side='left')
        if os.name != 'nt' or not getattr(sys, 'frozen', False):
            self.button.config(state='disabled')
            self.label.config(text=f'Версия {VERSION} · обновление доступно в Windows EXE')
        app.window.after(200, self.poll)

    def refresh_button(self):
        available = os.name == 'nt' and getattr(sys, 'frozen', False)
        self.button.config(state='normal' if available and not self.busy and not active(self.app) else 'disabled')

    def check(self):
        if self.busy:
            return
        if active(self.app):
            messagebox.showwarning('Сначала остановите сбор', 'Нажмите «Остановить сбор», остановите локальную запись; дождитесь завершения запросов. Во время вылета обновление не выполняется.')
            return
        self.busy = True
        self.button.config(state='disabled')
        self.label.config(text=f'Версия {VERSION} · проверка GitHub и загрузка…')
        state = self.app.queue.path.parent
        def run():
            try:
                self.results.put((updater.prepare(VERSION, state), None))
            except Exception as exc:
                self.results.put((None, type(exc).__name__))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        try:
            result, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.button.config(state='normal')
            if error:
                self.label.config(text=f'Версия {VERSION} · обновление не выполнено ({error}); файлы программы не изменены.')
            elif result is None:
                self.label.config(text=f'Версия {VERSION} · более новой стабильной версии нет.')
            elif active(self.app):
                self.label.config(text='Обновление отложено: сбор или запрос снова включён. Остановите и повторите.')
            else:
                stage, candidate, sha = result
                if messagebox.askyesno('Перезапустить и обновить?', f'{VERSION} → {candidate}. SHA-256 проверен, цифровой подписи нет. Доверие — репозиторию sergkreis/sphol-log-collector и GitHub. Сохранить очереди и перезапустить сейчас? Сбор после запуска выключен.'):
                    # Recheck after the modal's nested Tk event loop.
                    if not active(self.app):
                        try:
                            self.process = updater.start_helper(stage, sha)
                            self.stage, self.deadline = stage, time.monotonic() + 30
                            self.busy = True
                            self.button.config(state='disabled')
                            self.app.window.after(100, self.wait_helper)
                        except Exception:
                            self.label.config(text='Не удалось запустить помощник. Программа не изменена.')
        self.refresh_button()
        self.app.window.after(200, self.poll)

    def wait_helper(self):
        if (self.stage / 'ready').exists() and not active(self.app):
            # No active workers or local file handles. Committed queue data remains.
            self.app.stop()
            self.app.expanded.close()
            if getattr(self.app, 'legacy', None):
                self.app.legacy.close()
            self.app.queue.close()
            updater.private_write(self.stage / 'apply', b'apply')
            self.app.window.destroy()
            return
        if self.process.poll() is not None or time.monotonic() > self.deadline or active(self.app):
            self.busy = False
            self.button.config(state='normal')
            self.label.config(text='Перезапуск отменён/помощник не готов. Текущая программа не изменена.')
            return
        self.app.window.after(100, self.wait_helper)
