"""Foreground local capture; native controls, no background service."""
import ctypes
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
from .diagnostics import initialize, emit
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
        self.diagnostics = None
        try:
            self.diagnostics = initialize(queue.path.parent)
            self.diagnostics.inspect_logs(log_root)
        except Exception:
            pass  # Optional diagnostics must not prevent capture initialization.
        self.tailer = None
        self.local_capture = None
        from .theme import apply_theme
        apply_theme(window)
        window.title('SPHOL — боевые журналы')
        window.minsize(560, 440)
        window.geometry('560x440')
        self.status = tk.StringVar(value='Сбор выключен — новые события не читаются.')
        self.pending = tk.StringVar()
        # Keep horizontal breathing room without spending short-screen height.
        self.frame = frame = ttk.Frame(window, padding=(20, 10))
        frame.pack(fill='both', expand=True)
        self.header = ttk.Frame(frame)
        self.header.pack(fill='x')
        ttk.Label(self.header, text='SPHOL', style='Title.TLabel').pack(side='left')
        self.identity_area = ttk.Frame(frame)
        self.identity_area.pack(fill='x', pady=(8, 8))
        capture = ttk.Frame(frame)
        capture.pack(fill='x')
        self.capture_area = capture
        ttk.Label(capture, textvariable=self.status, wraplength=620)  # diagnostic source, not a duplicate headline
        # Detailed queue breakdown lives in the collapsed diagnostics.
        buttons = ttk.Frame(capture)
        self.capture_buttons = buttons
        buttons.pack(anchor='center', pady=(16, 12))
        self.start_button = ttk.Button(buttons, text='Начать сбор', command=self.start)
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(buttons, text='Остановить сбор и отправку', command=self.stop, state='disabled')
        self.main_button = ttk.Button(buttons, text='Начать сбор', style='Primary.TButton', command=self.toggle_capture)
        self.start_button.pack_forget()
        self.main_button.pack(side='left')
        self.network_area = ttk.Frame(frame)
        self.network_area.pack(fill='x', pady=(8, 4))
        self.footer = ttk.Frame(self.frame)
        self.settings_button = ttk.Button(self.footer, text='Настройки', command=self.toggle_settings)
        self.settings_button.pack(anchor='w', pady=(8, 0))
        self.settings_host = ttk.Frame(frame)
        self.settings_canvas = tk.Canvas(self.settings_host, height=190, highlightthickness=0, background='#171b20')
        scrollbar = ttk.Scrollbar(self.settings_host, orient='vertical', command=self.settings_canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.settings_canvas.pack(side='left', fill='both', expand=True)
        self.settings_canvas.configure(yscrollcommand=scrollbar.set)
        self.settings = ttk.Frame(self.settings_canvas)
        settings_item = self.settings_canvas.create_window((0, 0), window=self.settings, anchor='nw')
        self.settings.bind('<Configure>', lambda e: self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox('all')))
        def resize_settings(event):
            self.settings_canvas.itemconfigure(settings_item, width=event.width)
            def wrap(parent):
                for child in parent.winfo_children():
                    if isinstance(child, ttk.Label) and child.cget('wraplength'):
                        child.configure(wraplength=max(200, event.width - 12))
                    wrap(child)
            wrap(self.settings)
        self.settings_canvas.bind('<Configure>', resize_settings)
        window.bind('<MouseWheel>', lambda e: self.settings_canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units') if self.settings_open else None)
        window.bind('<Button-4>', lambda e: self.settings_canvas.yview_scroll(-1, 'units') if self.settings_open else None)
        window.bind('<Button-5>', lambda e: self.settings_canvas.yview_scroll(1, 'units') if self.settings_open else None)
        self.settings_open = False
        self.danger_area = ttk.Frame(self.settings)
        self.danger_area.pack(fill='x', pady=12)
        ttk.Button(self.danger_area, text='Удалить очередь…', command=self.clear).pack(anchor='w', pady=4)
        capture = self.settings
        ttk.Label(capture, text='Полная локальная запись · никогда не отправляется', style='Muted.TLabel').pack(anchor='w', pady=8)
        self.local_enabled = tk.BooleanVar(value=False)
        self.local_toggle = ttk.Checkbutton(capture, text='Сохранять все игровые события локально', variable=self.local_enabled, command=self.toggle_local)
        self.local_toggle.pack(anchor='w', pady=(12, 0))
        ttk.Label(capture, text='Только новые строки Gamelogs всех типов, НЕ Chatlogs. Возможны чувствительные игровые уведомления. Полные локальные записи никогда не отправляются. Лимит: 64 МиБ на сессию / 128 МиБ всего; при заполнении запись остановится.', wraplength=620).pack(anchor='w')
        self.local_status = tk.StringVar(value='Полная локальная запись выключена. История уже остаётся в исходных Gamelogs.')
        ttk.Label(capture, textvariable=self.local_status, wraplength=620).pack(anchor='w')
        frame = self.settings
        ttk.Separator(frame).pack(fill='x', pady=(4, 10))
        ttk.Label(frame, text='Папка боевых журналов:', font=('Segoe UI', 9, 'bold')).pack(anchor='w')
        ttk.Label(frame, text=str(log_root), wraplength=620).pack(anchor='w', pady=(4, 8))
        ttk.Label(frame, text='Только новые боевые строки. Без чатов, памяти игры, паролей и фоновой службы. Отправка — только по вашему разрешению.', wraplength=620).pack(anchor='w')
        from .dashboard import Dashboard
        self.dashboard = Dashboard(self, self.capture_area)
        self.dashboard.heading.pack(before=self.capture_area.pack_slaves()[0], anchor='center', pady=(12, 6))

        # Reserve footer height before top-packed content can consume it.
        self.footer.pack(side='bottom', fill='x', pady=(8, 0), before=self.header)
        # Recovery can be tall; keep diagnostics reachable before secondary
        # stream summaries consume the remaining vertical space.
        self.settings_button.pack(in_=self.footer, side='left')
        self.support_button = ttk.Button(self.settings, text='Сохранить отчёт для поддержки', command=self.export_support)
        self.support_button.pack(anchor='w', before=self.danger_area, pady=8)
        self.support_status = ttk.Label(self.settings, text='Диагностика хранится локально; игровые тексты и ключи не записываются.', wraplength=680)
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.tick()

    def export_support(self):
        self.support_status.pack(anchor='w')
        try:
            from datetime import datetime
            target = filedialog.asksaveasfilename(parent=self.window,
                title='Сохранить отчёт для поддержки',
                initialfile='SPHOL-support-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json',
                defaultextension='.json', filetypes=[('Отчёт поддержки', '*.json')])
            if not target:
                self.support_status.config(text='Сохранение отменено. Ничего не отправлено.')
                return
            if self.diagnostics is None:
                self.diagnostics = initialize(self.queue.path.parent)
            self.diagnostics.inspect_logs(self.log_root)
            self.diagnostics.export(target)
            self.support_status.config(text='Отчёт сохранён. Можно отправить этот JSON-файл поддержке в Telegram. Автоматической отправки нет.')
        except Exception as exc:
            emit('export', 'error', error=exc)
            self.support_status.config(text='Не удалось сохранить отчёт. Выберите другую папку вне хранилища сборщика и файл .json.')

    def toggle_settings(self):
        self.settings_open = not self.settings_open
        self.settings_button.config(text='Назад' if self.settings_open else 'Настройки')
        if self.settings_open:
            self.capture_area.pack_forget()
            self.network_area.pack_forget()
            self.settings_host.pack(fill='both', expand=True, pady=8)
        else:
            self.settings_host.pack_forget()
            self.capture_area.pack(fill='x', after=self.identity_area)
            self.network_area.pack(fill='x', pady=(8, 4), after=self.capture_area)
        self.window.update_idletasks()
        self.window.geometry('680x620' if self.settings_open else '560x440')
        if hasattr(self, 'site_codes'):
            self.site_codes.refresh()

    def toggle_capture(self):
        if self.stop_button.instate(['!disabled']):
            self.stop()
        else:
            codes = getattr(self, 'site_codes', None)
            if codes and not self.uploader and not self.pairing:
                codes.refresh()
                if not codes.uncertain:
                    codes.label.config(text='Вставьте код с сайта, затем нажмите «Привязать». Сбор выключен.')
                    codes.entry.focus_set()
                return
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
            self.main_button.config(text='Остановить сбор' if self.stop_button.instate(['!disabled']) else 'Начать сбор')
        from .dashboard import refresh
        refresh(self)
        if hasattr(self, 'updates'):
            self.updates.refresh_button()

    def start(self):
        if getattr(self, '_poll_failed', False):
            return
        if self.tailer is not None:
            return
        try:
            self.tailer = Tailer(self.log_root, self.queue)
            emit('capture.start', count=len(self.tailer.files))
            self.status.set('Сбор включён — читаются новые боевые события.')
        except (OSError, ValueError) as exc:
            emit('capture.start', 'error', error=exc)
            from .core import PendingCaptureUnavailable, PENDING_CAPTURE_WARNING
            self.status.set(PENDING_CAPTURE_WARNING if isinstance(exc, PendingCaptureUnavailable) else 'Сбор выключен — проверьте доступ к папке журналов.')
        self.capture_controls()

    def stop(self):
        emit('capture.stop')
        self.stop_local()
        if self.tailer:
            self.tailer.stop()
        self.tailer = None
        self.status.set('Сбор выключен — очередь сохранена на компьютере.')
        self.capture_controls()

    def clear(self):
        if messagebox.askyesno('Удалить очередь?', 'Безвозвратно удалить все ожидающие события? Сбор и отправка будут остановлены.'):
            self.stop()
            self.queue.clear()
            self.pending.set('В очереди на компьютере: 0 событий')

    from .poll_scheduler import scheduled_poll

    @scheduled_poll
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
                count = self.tailer.poll()
                if count or self.tailer.unattributed_files:
                    emit('capture.poll', count=count, unattributed=self.tailer.unattributed_files)
                self.status.set('Сбор включён — читаются новые боевые события.' if not self.tailer.unattributed_files else 'Сбор включён. Отдельные события ждут проверенного заголовка персонажа; остальные журналы читаются.')
            except QueueFull:
                self.status.set('Сбор приостановлен: очередь заполнена. Эти данные ещё не отправлены.')
            except Exception as exc:
                emit('capture.poll', 'error', error=exc)
                self.tailer = None  # Preserve durable recovery envelope on faults.
                self.stop()
                from .core import PendingCaptureUnavailable, PENDING_CAPTURE_WARNING
                self.status.set(PENDING_CAPTURE_WARNING if isinstance(exc, PendingCaptureUnavailable) else 'Сбор выключен после ошибки чтения. Сохраните отчёт для поддержки; очередь сохранена.')
        self.pending.set(f'В очереди на компьютере: {self.queue.count()} событий')
        if hasattr(self, 'main_button'):
            from .queue_status import queue_summary
            uploader = getattr(self, 'uploader', None)
            listeners = {c['name'] for c in uploader.credentials['characters']} if uploader else set()
            self.pending.set(queue_summary(self.queue, listeners))
        from .dashboard import refresh
        refresh(self)


    def close(self):
        if self.queue.count() and not messagebox.askyesno('Есть неотправленные события', 'Сохранить очередь на диске и выйти? Эти события ещё не отправлены. Фоновый процесс не останется.'):
            return False
        self.stop_local()
        if self.tailer:
            self.tailer.stop()
        self.tailer = None
        emit('app.close')
        self.queue.close()
        self.window.destroy()
        return True


def main():
    window = tk.Tk()
    diagnostics_ready = False
    try:
        from .updater import instance_lock
        # Reject duplicates before loading or writing the owner's shared history.
        instance = instance_lock(Path(os.environ['LOCALAPPDATA']) / 'SPHOLLogCollector')
        try:
            initialize(Path(os.environ['LOCALAPPDATA']) / 'SPHOLLogCollector')
            diagnostics_ready = True
        except Exception:
            pass
        root = documents() / 'EVE' / 'logs' / 'Gamelogs'
        state = Path(os.environ['LOCALAPPDATA']) / 'SPHOLLogCollector' / 'pending.sqlite3'
        from .network_gui import ConnectedApp
        ConnectedApp(window, root, PendingQueue(state))
    except Exception as exc:
        if diagnostics_ready:
            emit('app.init', 'error', error=exc)
        messagebox.showerror('Не удалось открыть сборщик', 'Проверьте доступ к папке журналов и локальному хранилищу.')
        window.destroy()
        return
    window.mainloop()
