"""Foreground network UI; worker never touches Tk or SQLite."""
import queue as messages
import threading
import time
import webbrowser
from tkinter import ttk, messagebox
from .gui import App, tk
from .credentials import CredentialStore
from .transport import Pairing, Uploader, PAIR_URI


class Snapshot:
    def __init__(self, events):
        self.events, self.accepted = events, []

    def batch(self, limit):
        return self.events[:limit]

    def acknowledge(self, ids):
        self.accepted = list(ids)


class ConnectedApp(App):
    def __init__(self, window, log_root, queue):
        self.results = messages.Queue()
        self.busy = False
        self.pairing = self.uploader = None
        self.upload_enabled = False
        self.store = CredentialStore(queue.path.parent / 'credentials.dpapi')
        super().__init__(window, log_root, queue)
        self.identity = ttk.Label(self.identity_area, text='Персонаж не привязан', font=('Segoe UI', 14, 'bold'), wraplength=620)
        self.identity.pack(anchor='w')
        ttk.Label(self.identity_area, text='Сохранённая привязка не означает текущую связь с сервером.', wraplength=620).pack(anchor='w', pady=(4, 0))
        self.upload_state = ttk.Label(self.network_area, font=('Segoe UI', 10, 'bold'), wraplength=620)
        self.upload_state.pack(anchor='w')
        self.last_ack = ttk.Label(self.network_area, text='Подтверждение сервера: в этом запуске ещё не получено.', wraplength=620)
        self.last_ack.pack(anchor='w', pady=(6, 0))
        self.connection = ttk.Label(self.network_area, text='Для отправки привяжите персонажа, затем разрешите отправку.', wraplength=620)
        self.connection.pack(anchor='w', pady=(6, 8))
        self.code_frame = code_frame = ttk.Frame(self.network_area)
        ttk.Label(code_frame, text='Код привязки:').pack(side='left', padx=(0, 8))
        self.pairing_code = tk.StringVar(master=window, value='')
        self.code_field = ttk.Entry(code_frame, textvariable=self.pairing_code, state='readonly', width=22, exportselection=False)
        self.code_field.pack(side='left')
        self.copy_button = ttk.Button(code_frame, text='Скопировать код', command=self.copy_pairing_code, state='disabled')
        self.copy_button.pack(side='left', padx=8)
        self.copy_feedback = ttk.Label(code_frame, text='')
        self.copy_feedback.pack(side='left')
        buttons = ttk.Frame(self.network_area)
        buttons.pack(anchor='w', pady=(8, 0))
        self.pair_button = ttk.Button(buttons, text='Привязать персонажа…', command=self.pair)
        self.pair_button.pack(side='left')
        self.enable_button = ttk.Button(buttons, text='Разрешить отправку', command=self.enable)
        self.enable_button.pack(side='left', padx=8)
        self.unpair_button = ttk.Button(buttons, text='Удалить привязку…', command=self.unpair)
        self.unpair_button.pack(side='left')
        try:
            credentials = self.store.load()
            if credentials:
                self.uploader = Uploader(credentials)
                self.show_identity()
        except Exception:
            self.connection.config(text='Сохранённая привязка недоступна или истекла. Удалите её и привяжите персонажа заново.')
        self.refresh_controls()
        window.after(250, self.network_tick)

    def refresh_controls(self):
        self.pair_button.config(state='disabled' if self.uploader or self.pairing or self.busy else 'normal')
        self.enable_button.config(state='normal' if self.uploader and not self.uploader.paused else 'disabled', text='Выключить отправку' if self.upload_enabled else 'Разрешить отправку')
        self.unpair_button.config(state='disabled' if self.busy else 'normal')
        self.upload_state.config(text='Отправка включена — только для привязанного персонажа' if self.upload_enabled else 'Отправка выключена — данные остаются на компьютере')
        self.capture_controls()

    def clear_pairing_code(self):
        # Never modify the clipboard automatically.
        self.pairing_code.set('')
        self.copy_button.config(state='disabled')
        self.copy_feedback.config(text='')
        self.code_frame.pack_forget()

    def copy_pairing_code(self):
        if not self.pairing or time.monotonic() >= self.pairing.deadline:
            self.clear_pairing_code()
            return
        code = self.pairing_code.get()
        if not code:
            return
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(code)
        except tk.TclError:
            self.copy_feedback.config(text='Выделите код и нажмите Ctrl+C.')
        else:
            self.copy_feedback.config(text='Код скопирован')

    def show_identity(self):
        c = self.uploader.credentials
        self.identity.config(text='Персонаж: ' + ', '.join(x['name'] for x in c['characters']))
        self.connection.config(text='Привязка действует до ' + c['expires_at'] + '. Отправку нужно разрешить отдельно.')

    def work(self, kind, function):
        if self.busy:
            return
        self.busy = True
        self.refresh_controls()
        def run():
            try:
                self.results.put((kind, function(), None))
            except Exception as error:
                self.results.put((kind, None, type(error).__name__))
        threading.Thread(target=run, daemon=True).start()

    def pair(self):
        if self.busy or self.uploader or self.pairing:
            return
        if self.queue.count():
            messagebox.showwarning('Есть очередь', 'Перед новой привязкой удалите очередь, чтобы исключить передачу данных другому аккаунту.')
            return
        if not messagebox.askyesno('Привязать персонажа?', 'Открыть sphol.com для подтверждения персонажа? Отправлять можно только его события. Пароль здесь не вводится.'):
            return
        self.pairing = Pairing()
        self.connection.config(text='Получаем код привязки…')
        self.work('pair', self.pairing.start)

    def enable(self):
        if self.uploader and not self.uploader.paused:
            self.upload_enabled = not self.upload_enabled
            self.connection.config(text='Ожидание событий. Пустая очередь не подтверждает связь с сервером.' if self.upload_enabled else 'Новая отправка запрещена; уже отправленный запрос может завершиться.')
            self.refresh_controls()

    def stop(self):
        self.upload_enabled = False
        super().stop()
        if hasattr(self, 'connection'):
            self.connection.config(text='Новая отправка остановлена; уже отправленный запрос может завершиться. Очередь сохранена.')
            self.refresh_controls()

    def clear(self):
        if self.busy:
            messagebox.showwarning('Запрос выполняется', 'Дождитесь завершения запроса перед удалением очереди.')
            return
        super().clear()

    def unpair(self):
        if self.busy:
            return
        if not messagebox.askyesno('Удалить привязку?', 'Удалить локальный ключ и ВСЮ очередь событий? Отозвать доступ устройства на сайте нужно отдельно.'):
            return
        self.stop()
        self.store.clear()
        self.queue.clear()
        self.uploader = self.pairing = None
        self.clear_pairing_code()
        self.identity.config(text='Персонаж не привязан')
        self.last_ack.config(text='Подтверждение сервера: в этом запуске ещё не получено.')
        self.connection.config(text='Локальная привязка удалена. Отзыв доступа на сайте выполняется отдельно.')
        self.refresh_controls()

    def network_tick(self):
        try:
            kind, result, error = self.results.get_nowait()
        except messages.Empty:
            pass
        else:
            self.busy = False
            if error:
                self.upload_enabled = False
                self.pairing = None
                self.clear_pairing_code()
                self.connection.config(text='Запрос не выполнен. Очередь сохранена; успех не подтверждён. Проверьте сеть и срок привязки.')
            elif kind == 'pair':
                self.pairing_code.set(result)
                self.code_frame.pack(anchor='w', pady=8)
                self.copy_button.config(state='normal')
                self.copy_feedback.config(text='')
                self.connection.config(text='Введите код на sphol.com и подтвердите привязку.')
                webbrowser.open(PAIR_URI)
            elif kind == 'token' and result:
                self.clear_pairing_code()
                try:
                    self.store.save(result)
                    self.uploader = Uploader(result)
                    self.show_identity()
                except Exception:
                    self.connection.config(text='Не удалось безопасно сохранить привязку Windows. Отправка недоступна.')
                self.pairing = None
            elif kind == 'upload':
                snapshot, status = result
                self.queue.acknowledge(snapshot.accepted)
                if snapshot.accepted:
                    self.last_ack.config(text=f'Сервер подтвердил: {len(snapshot.accepted)} событий · {time.strftime("%d.%m.%Y %H:%M:%S")} (время компьютера)')
                if self.uploader and self.uploader.paused:
                    self.upload_enabled = False
                if status:
                    self.connection.config(text=status)
        if self.pairing_code.get() and (not self.pairing or time.monotonic() >= self.pairing.deadline):
            self.clear_pairing_code()
        if not self.busy:
            if self.pairing:
                self.work('token', self.pairing.poll)
            elif self.upload_enabled and self.uploader:
                snapshot = Snapshot(self.queue.batch(listeners={c['name'] for c in self.uploader.credentials['characters']}))
                uploader = self.uploader
                if snapshot.events:
                    self.work('upload', lambda: (snapshot, uploader.upload(snapshot)))
                else:
                    self.connection.config(text='Очередь пуста — ждём новые события; сервер не проверялся.' if not self.queue.count() else 'Нет событий привязанного персонажа. Остальные события остаются в очереди.')
        self.refresh_controls()
        self.window.after(1000, self.network_tick)
