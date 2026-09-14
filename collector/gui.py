"""Foreground local capture; native controls, no background service."""
import ctypes
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from .core import PendingQueue, Tailer, QueueFull
from .local_capture import LocalCapture, CaptureStopped


def documents():
    if os.name != 'nt':
        raise OSError('Приложение предназначено для Windows')
    import uuid
    guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID('FDD39AD0-238F-46AF-ADB4-6C85480369C7').bytes_le)
    result = ctypes.c_wchar_p()
    hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(result))
    if hr != 0:
        raise OSError('Не удалось найти папку «Документы»')
    try:
        return Path(result.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(result)


class App:
    def __init__(self, window, log_root, queue):
        self.window, self.log_root, self.queue = window, log_root, queue
        self.tailer = None
        self.local_capture = None
        from .theme import apply_theme
        apply_theme(window)
        window.title('SPHOL — боевые журналы')
        window.minsize(760, 660)
        window.geometry('780x700')
        self.status = tk.StringVar(value='Сбор выключен — новые события не читаются.')
        self.pending = tk.StringVar()
        self.frame = frame = ttk.Frame(window, padding=20)
        frame.pack(fill='both', expand=True)
        self.header = ttk.Frame(frame)
        self.header.pack(fill='x')
        ttk.Label(self.header, text='SPHOL', style='Title.TLabel').pack(side='left')
        ttk.Label(self.header, text='БОЕВЫЕ ЖУРНАЛЫ', style='Muted.TLabel').pack(side='left', padx=16)
        self.identity_area = ttk.Frame(frame)
        self.identity_area.pack(fill='x', pady=(12, 16))
        capture = ttk.Frame(frame)
        capture.pack(fill='x')
        self.capture_area = capture
        ttk.Label(capture, textvariable=self.status, wraplength=620).pack(anchor='w')
        # Detailed queue breakdown lives in the collapsed diagnostics.
        buttons = ttk.Frame(capture)
        self.capture_buttons = buttons
        buttons.pack(anchor='w', pady=(12, 0))
        self.start_button = ttk.Button(buttons, text='Начать сбор', command=self.start)
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(buttons, text='Остановить сбор и отправку', command=self.stop, state='disabled')
        self.main_button = ttk.Button(buttons, text='Начать сбор', style='Primary.TButton', command=self.toggle_capture)
        self.start_button.pack_forget()
        self.main_button.pack(side='left')
        self.network_area = ttk.Frame(frame)
        self.network_area.pack(fill='x', pady=(16, 12))
        self.settings_button = ttk.Button(frame, text='Настройки и диагностика ▸', command=self.toggle_settings)
        self.settings_button.pack(anchor='w', pady=(8, 0))
        self.settings = ttk.Frame(frame)
        self.settings_open = False
        self.danger_area = ttk.Frame(self.settings)
        self.danger_area.pack(fill='x', pady=12)
        ttk.Button(self.danger_area, text='Удалить очередь…', command=self.clear).pack(side='left', padx=(0, 8))
        capture = self.settings
        ttk.Label(capture, text='Расширенный журнал — пока недоступен на сервере', style='Muted.TLabel').pack(anchor='w', pady=8)
        self.local_enabled = tk.BooleanVar(value=False)
        self.local_toggle = ttk.Checkbutton(capture, text='Сохранять все игровые события локально', variable=self.local_enabled, command=self.toggle_local)
        self.local_toggle.pack(anchor='w', pady=(12, 0))
        ttk.Label(capture, text='Только новые строки Gamelogs всех типов, НЕ Chatlogs. Возможны чувствительные игровые уведомления. Расширенные записи никогда не отправляются. Лимит: 64 МиБ на сессию / 128 МиБ всего; при заполнении запись остановится.', wraplength=620).pack(anchor='w')
        self.local_status = tk.StringVar(value='Полная локальная запись выключена. История уже остаётся в исходных Gamelogs.')
        ttk.Label(capture, textvariable=self.local_status, wraplength=620).pack(anchor='w')
        frame = self.settings
        ttk.Separator(frame).pack(fill='x', pady=(4, 10))
        ttk.Label(frame, text='Папка боевых журналов:', font=('Segoe UI', 9, 'bold')).pack(anchor='w')
        ttk.Label(frame, text=str(log_root), wraplength=620).pack(anchor='w', pady=(4, 8))
        ttk.Label(frame, text='Только новые боевые строки. Без чатов, памяти игры, паролей и фоновой службы. Отправка — только по вашему разрешению.', wraplength=620).pack(anchor='w')
        from .dashboard import Dashboard
        self.dashboard = Dashboard(self, self.capture_area)
        self.dashboard.heading.pack(before=self.capture_area.winfo_children()[0], anchor='w', pady=(0, 8))
        ttk.Label(self.network_area, text='Только боевые события · без чатов и маршрутов', style='Muted.TLabel').pack(anchor='w', pady=(0, 10))
        self.footer = ttk.Frame(self.frame)
        self.footer.pack(side='bottom', fill='x', pady=(16, 0))
        ttk.Separator(self.footer).pack(fill='x', pady=(0, 12))
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.tick()

    def toggle_settings(self):
        self.settings_open = not self.settings_open
        self.settings_button.config(text='Настройки и диагностика ▾' if self.settings_open else 'Настройки и диагностика ▸')
        if self.settings_open:
            self.settings.pack(fill='x', pady=8)
        else:
            self.settings.pack_forget()
        self.window.update_idletasks()
        self.window.geometry(f'780x{max(700, self.frame.winfo_reqheight())}')

    def toggle_capture(self):
        if self.stop_button.instate(['!disabled']):
            self.stop()
        else:
            self.start()

    def toggle_local(self):
        if not self.local_enabled.get():
            self.stop_local()
            return
        if not messagebox.askyesno('Локальная запись всех событий', 'Сохранять новые строки всех типов из Gamelogs? Они могут содержать чувствительные уведомления. Chatlogs не читаются. Эти записи не отправляются на сервер. История до включения не копируется.'):
            self.local_enabled.set(False)
            return
        try:
            self.local_capture = LocalCapture(self.log_root, self.queue.path.parent / 'local-captures', consent=True)
            self.show_local()
        except Exception:
            self.local_enabled.set(False)
            self.local_status.set('Локальная запись не начата: проверьте доступ и лимит хранилища. Оригиналы остаются в Gamelogs.')
        self.capture_controls()

    def show_local(self):
        c = self.local_capture
        self.local_status.set(f'Локально: {c.count} строк · {c.directory} · без отправки')

    def stop_local(self):
        c = getattr(self, 'local_capture', None)
        if c:
            try:
                c.close()
                self.show_local()
                self.local_status.set('Остановлено. ' + self.local_status.get() + ' · Незавершённые строки остаются в Gamelogs.')
            except Exception:
                self.local_status.set('Ошибка завершения локальной записи. Проверьте session.json; оригиналы в Gamelogs.')
            self.local_capture = None
            self.local_enabled.set(False)
        self.capture_controls()

    def capture_controls(self):
        self.start_button.config(state='disabled' if self.tailer else 'normal')
        expanded = getattr(self, 'expanded', None)
        self.stop_button.config(state='normal' if self.tailer or getattr(self, 'local_capture', None) or getattr(self, 'upload_enabled', False) or (expanded and (expanded.enabled or expanded.tailer)) else 'disabled')
        if hasattr(self, 'main_button'):
            self.main_button.config(text='Остановить сбор и отправку' if self.stop_button.instate(['!disabled']) else 'Начать сбор')
        from .dashboard import refresh
        refresh(self)
        if hasattr(self, 'updates'):
            self.updates.refresh_button()

    def start(self):
        if self.tailer is not None:
            return
        try:
            self.tailer = Tailer(self.log_root, self.queue)
            self.status.set('Сбор включён — читаются новые боевые события.')
        except (OSError, ValueError):
            self.status.set('Сбор выключен — проверьте доступ к папке журналов.')
        self.capture_controls()

    def stop(self):
        self.stop_local()
        self.tailer = None
        self.status.set('Сбор выключен — очередь сохранена на компьютере.')
        self.capture_controls()

    def clear(self):
        if messagebox.askyesno('Удалить очередь?', 'Безвозвратно удалить все ожидающие события? Сбор и отправка будут остановлены.'):
            self.stop()
            self.queue.clear()
            self.pending.set('В очереди на компьютере: 0 событий')

    def tick(self):
        if getattr(self, 'local_capture', None):
            try:
                self.local_capture.poll()
                self.show_local()
            except Exception as exc:
                self.stop_local()
                reason = str(exc) if isinstance(exc, CaptureStopped) else 'Ошибка чтения, UTF-8 или записи.'
                self.local_status.set('Локальная запись остановлена: ' + reason + ' Оригиналы остаются в Gamelogs.')
        if self.tailer:
            try:
                self.tailer.poll()
                self.status.set('Сбор включён — читаются новые боевые события.' if not self.tailer.unattributed_files else 'Сбор включён. Отдельные события ждут проверенного заголовка персонажа; остальные журналы читаются.')
            except QueueFull:
                self.status.set('Сбор приостановлен: очередь заполнена. Эти данные ещё не отправлены.')
            except Exception:
                self.stop()
                self.status.set('Сбор выключен после ошибки чтения. Проверьте папку журналов.')
        self.pending.set(f'В очереди на компьютере: {self.queue.count()} событий')
        if hasattr(self, 'main_button'):
            from .queue_status import queue_summary
            uploader = getattr(self, 'uploader', None)
            listeners = {c['name'] for c in uploader.credentials['characters']} if uploader else set()
            self.pending.set(queue_summary(self.queue, listeners))
        from .dashboard import refresh
        refresh(self)
        self.window.after(1000, self.tick)

    def close(self):
        if self.queue.count() and not messagebox.askyesno('Есть неотправленные события', 'Сохранить очередь на диске и выйти? Эти события ещё не отправлены. Фоновый процесс не останется.'):
            return False
        self.stop_local()
        self.tailer = None
        self.queue.close()
        self.window.destroy()
        return True


def main():
    window = tk.Tk()
    try:
        from .updater import instance_lock
        instance = instance_lock(Path(os.environ['LOCALAPPDATA']) / 'SPHOLLogCollector')
        root = documents() / 'EVE' / 'logs' / 'Gamelogs'
        state = Path(os.environ['LOCALAPPDATA']) / 'SPHOLLogCollector' / 'pending.sqlite3'
        from .network_gui import ConnectedApp
        ConnectedApp(window, root, PendingQueue(state))
    except Exception:
        messagebox.showerror('Не удалось открыть сборщик', 'Проверьте доступ к папке журналов и локальному хранилищу.')
        window.destroy()
        return
    window.mainloop()
