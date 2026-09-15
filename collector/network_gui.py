"""Foreground network UI; worker never touches Tk or SQLite."""
import queue as messages
import threading
import time
import webbrowser
from tkinter import ttk, messagebox
from .gui import App, tk
from .credentials import CredentialStore
from .transport import Pairing, Uploader, PAIR_URI, ORIGIN
from .pairing_ux import PairingUX, active_url, failure_text


class Snapshot:
    def __init__(self, events):
        self.events, self.accepted = events, []

    def batch(self, limit):
        return self.events[:limit]

    def acknowledge(self, ids):
        self.accepted = list(ids)


class ConnectedApp(PairingUX, App):
    def __init__(self, window, log_root, queue):
        self.results = messages.Queue()
        self.busy = False
        self.pairing = self.uploader = None
        self.upload_enabled = False
        self.upload_problem = False
        self.store = CredentialStore(queue.path.parent / 'credentials.dpapi')
        super().__init__(window, log_root, queue)
        self.identity = ttk.Label(self.identity_area, text='Персонаж не привязан', style='Title.TLabel', wraplength=620)
        self.identity.pack(anchor='w')
        from .connection_status import ConnectionStatus, LABELS
        self.connection_status = ConnectionStatus()
        self.connection_badge = ttk.Label(self.identity_area, text=LABELS['stopped'], wraplength=680)
        self.connection_badge.pack(anchor='w', pady=(4, 0))
        self.init_pairing_ux()
        self.binding_state = ttk.Label(self.identity_area, text='Привязка ещё не настроена', style='Muted.TLabel')

        self.upload_state = ttk.Label(self.network_area, font=('Segoe UI', 10, 'bold'), wraplength=620)

        self.last_ack = ttk.Label(self.network_area, text='Подтверждение сервера: в этом запуске ещё не получено.', wraplength=620)
        self.last_ack.pack(anchor='w', pady=(6, 0))
        self.connection = ttk.Label(self.settings, text='Привяжите персонажа. «Начать сбор» включает сбор и отправку его боевых событий и разрешённых наблюдений на sphol.com.', wraplength=620)
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
        self.pair_button.pack_forget()

        self.unpair_button = ttk.Button(self.danger_area, text='Удалить привязку…', command=self.unpair)
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
        from .expanded_gui import ExpandedControls
        self.expanded = ExpandedControls(self)
        from .legacy_backlog import LegacyBacklog
        self.legacy = LegacyBacklog(self)
        from .update_gui import UpdateControls
        self.updates = UpdateControls(self)
        self.open_site = ttk.Button(self.footer, text='Открыть SPHOL', command=lambda: webbrowser.open(ORIGIN))
        self.open_site.pack(side='right')
        window.update_idletasks()
        window.geometry('780x700')

    def refresh_controls(self):
        self.refresh_pairing_ux()
        if hasattr(self, 'connection_status'):
            from .connection_status import LABELS
            state = self.connection_status.tick(self.uploader.credentials if self.uploader else None, bool(self.tailer))
            colors = {'connected': '#80d8a0', 'offline': '#ef8791', 'denied': '#ef8791', 'checking': '#e8be75'}
            self.connection_badge.config(text=LABELS[state], foreground=colors.get(state, '#bcc5d3'))
        self.pair_button.config(state='disabled' if self.uploader or self.pairing or self.busy else 'normal')
        if hasattr(self, 'dashboard'):
            if self.uploader:
                self.pair_button.pack_forget()
            else:
                self.pair_button.pack_forget()

        self.unpair_button.config(state='disabled' if self.busy or getattr(self, 'recovered_token', None) else 'normal')
        self.upload_state.config(text='Отправка включена — только для привязанного персонажа' if self.upload_enabled else 'Отправка выключена — данные остаются на компьютере')
        if self.upload_enabled and getattr(self.uploader, 'failures', 0):
            self.upload_state.config(text='Сеть недоступна · очередь сохранена, повтор автоматически')
        elif getattr(self, 'upload_problem', False):
            self.upload_state.config(text='Ошибка отправки · очередь сохранена, см. диагностику')
        elif self.uploader and self.uploader.paused:
            self.upload_state.config(text='Отправка приостановлена · очередь сохранена, см. диагностику')
        elif self.uploader and not self.upload_enabled:
            self.upload_state.config(text='Отправка выключена. «Начать сбор» отправляет все разрешённые события на sphol.com')
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
        self.identity.config(text=', '.join(x['name'] for x in c['characters']))
        if hasattr(self, 'binding_state'):
            self.binding_state.config(text='Привязка сохранена')
        self.connection.config(text='Привязка действует до ' + c['expires_at'] + '. «Начать сбор» включает отправку; остановка сохраняет очередь.')

    def work(self, kind, function):
        if self.busy:
            return
        self.busy = True
        attempt = getattr(self, 'pair_attempt', 0) if kind in ('pair', 'token') else None
        if kind == 'token':
            # Redemption is irreversible: keep this worker authoritative until it ends.
            self.redemption_pending = True
        if attempt is not None:
            self.pair_job = (attempt, time.monotonic() + 20)
        self.refresh_controls()
        def run():
            try:
                self.results.put((kind, function(), None, attempt))
            except Exception as error:
                self.results.put((kind, None, error, attempt))
        threading.Thread(target=run, daemon=True).start()

    def pair(self):
        if self.busy or self.pairing or getattr(self, 'recovered_token', None) or getattr(self, 'redemption_uncertain', False):
            return
        if self.queue.count() and not self.uploader:
            self.resume_after_pair = False
            self.pairing_notice('Есть непривязанная очередь. Она сохранена; новая привязка остановлена для защиты данных. Обратитесь в поддержку — не удаляйте очередь для повтора.')
            return
        if not messagebox.askyesno('Привязать персонажа?', 'Открыть sphol.com для подтверждения персонажа? После привязки «Начать сбор» автоматически отправляет его новые боевые события и сохранённую очередь на sphol.com. Остановка прекращает сбор и новые запросы. Пароль здесь не вводится.'):
            self.resume_after_pair = False
            return
        self.pair_attempt = getattr(self, 'pair_attempt', 0) + 1
        self.pairing = Pairing(scope='gamelogs:write', browser=True, credentials=self.uploader.credentials if self.uploader else None)
        self.pairing_notice('Получаем ссылку подтверждения от SPHOL… Это может занять до 20 секунд. Сбор ещё не начат.')
        self.work('pair', self.pairing.start)

    def start(self):
        if active_url(self.pairing):
            self.open_pairing_browser()
            return
        if self.busy or self.pairing or getattr(self, 'recovered_token', None) or getattr(self, 'redemption_uncertain', False):
            return
        if not self.uploader or self.uploader.credentials['scope'] != 'gamelogs:write':
            self.resume_after_pair = True
            self.pair()
            return
        if getattr(self, 'expanded', None):
            if self.expanded.busy:
                self.connection.config(text='Завершается запрос наблюдений; повторите «Начать сбор». Очередь сохранена.')
                return
            self.expanded.bind(self.uploader.credentials)
        self.upload_problem = False
        super().start()
        self.upload_enabled = bool(self.tailer and self.uploader and not self.uploader.paused)
        expanded = getattr(self, 'expanded', None)
        if self.tailer and expanded and expanded.uploader:
            expanded.start(integrated=True)
        if self.tailer and getattr(self, 'legacy', None):
            self.legacy.authorize()
        self.refresh_controls()

    def stop(self):
        self.resume_after_pair = False
        self.upload_enabled = False
        if getattr(self, 'legacy', None):
            self.legacy.stop()
        if getattr(self, 'expanded', None):
            self.expanded.stop()
        super().stop()
        if hasattr(self, 'connection'):
            self.connection.config(text='Новая отправка остановлена; уже отправленный запрос может завершиться. Очередь сохранена.')
            self.refresh_controls()

    def clear(self):
        if self.busy:
            messagebox.showwarning('Запрос выполняется', 'Дождитесь завершения запроса перед удалением очереди.')
            return
        super().clear()

    def close(self):
        if getattr(self, 'redemption_pending', False) or getattr(self, 'recovered_token', None):
            self.stop()
            self.pairing_notice('Сбор остановлен. Дождитесь сохранения ответа SPHOL перед выходом, чтобы не потерять привязку. Если сохранение не удалось — нажмите «Повторить привязку»: повторится только сохранение.')
            return False
        expanded = getattr(self, 'expanded', None)
        legacy = getattr(self, 'legacy', None)
        if ((expanded and expanded.queue.count()) or (legacy and legacy.queue.count())) and not messagebox.askyesno('Есть очередь наблюдений', 'Сохранить неотправленные наблюдения и выйти? Фонового процесса не останется.'):
            return
        # Base close may be cancelled by the independent legacy queue warning.
        closed = super().close()
        if closed and expanded:
            expanded.close()
            if legacy:
                legacy.close()
        return closed

    def unpair(self):
        if self.busy or getattr(self, 'recovered_token', None) or (getattr(self, 'expanded', None) and self.expanded.busy):
            return
        if not messagebox.askyesno('Удалить привязку?', 'Удалить текущий локальный ключ и текущую очередь событий? Прежняя привязка наблюдений и её очередь сохранятся. Отозвать доступ устройства на сайте нужно отдельно.'):
            return
        self.stop()
        self.store.clear()
        self.queue.clear()
        expanded = getattr(self, 'expanded', None)
        if expanded:
            expanded.store.clear()
            expanded.queue.clear()
            expanded.uploader = expanded.pairing = None
            expanded.code.set('')
        self.uploader = self.pairing = None
        self.clear_pairing_code()
        self.identity.config(text='Персонаж не привязан')
        if hasattr(self, 'binding_state'):
            self.binding_state.config(text='Привязка ещё не настроена')
        self.last_ack.config(text='Подтверждение сервера: в этом запуске ещё не получено.')
        self.connection.config(text='Локальная привязка удалена. Отзыв доступа на сайте выполняется отдельно.')
        self.refresh_controls()

    def wait_for_redemption(self):
        self.stop()
        self.pair_job = None
        self.pairing_notice('SPHOL отвечает дольше обычного. Сбор остановлен; ждём результат выдачи ключа. Повтор и выход временно заблокированы, чтобы не потерять привязку. После сохранения нажмите «Начать сбор».')

    def network_tick(self):
        self.poll_browser()
        try:
            item = self.results.get_nowait()
            kind, result, error = item[:3]
            stale = len(item) == 4 and item[3] is not None and item[3] != getattr(self, 'pair_attempt', 0)
        except messages.Empty:
            pass
        else:
            if stale:
                self.window.after(250, self.network_tick)
                return
            if (kind == 'token' and getattr(self, 'pair_job', None)
                    and time.monotonic() >= self.pair_job[1]):
                self.wait_for_redemption()
            self.busy = False
            if kind in ('pair', 'token'):
                self.pair_job = None
            if kind == 'token':
                self.redemption_pending = False
            if error and kind in ('pair', 'token'):
                self.pairing_failed(failure_text(error))
                if kind == 'token':
                    self.redemption_uncertain = True
                    self.stop()
                    self.pairing_notice('Ответ о привязке не получен. SPHOL мог уже выдать ключ. Новая привязка заблокирована: проверьте устройства на сайте и обратитесь в поддержку. Очередь сохранена; можно закрыть приложение.')
            elif error:
                self.upload_problem = True
                self.upload_enabled = False
                self.pairing = None
                self.clear_pairing_code()
                self.connection.config(text='Запрос не выполнен. Очередь сохранена; успех не подтверждён. Проверьте сеть и срок привязки.')
            elif kind == 'pair':
                if getattr(self.pairing, 'browser', False):
                    if active_url(self.pairing):
                        self.pairing_notice('Ссылка готова. Подтвердите сбор боевых событий и трёх безопасных наблюдений в браузере.')
                        self.open_pairing_browser()
                    else:
                        self.pairing_failed('Ссылка подтверждения недействительна или истекла. Нажмите «Повторить привязку».')
                else:  # Legacy protocol compatibility; new Start never selects this path.
                    self.pairing_code.set(result)
                    self.code_frame.pack(anchor='w', pady=8)
                    self.copy_button.config(state='normal')
                    self.copy_feedback.config(text='')
                    webbrowser.open(PAIR_URI)
            elif kind == 'token' and result:
                self.clear_pairing_code()
                saved = False
                self.recovered_token = result
                try:
                    self.store.save(result)
                    self.uploader = Uploader(result)
                    self.upload_enabled = bool(self.tailer)
                    self.show_identity()
                    saved = True
                    self.recovered_token = None
                except Exception:
                    self.resume_after_pair = False
                    self.upload_enabled = False
                    self.pairing_notice('Не удалось безопасно сохранить ключ Windows. Не закрывайте приложение. «Повторить привязку» повторит только сохранение полученного ключа, без нового устройства. Сбор не начат.')
                self.pairing = None
                if saved and hasattr(self, 'pairing_panel'):
                    self.pairing_panel.pack_forget()
                if saved and getattr(self, 'resume_after_pair', False) and self.uploader and self.uploader.credentials['scope'] == 'gamelogs:write':
                    self.resume_after_pair = False
                    self.start()
                self.resume_after_pair = False
            elif kind == 'upload':
                snapshot, status = result
                self.queue.acknowledge(snapshot.accepted)
                if hasattr(self, 'dashboard'):
                    self.dashboard.acknowledge(snapshot.accepted)
                if snapshot.accepted:
                    self.upload_problem = False
                    self.last_ack.config(text=f'Сервер подтвердил: {len(snapshot.accepted)} событий · {time.strftime("%d.%m.%Y %H:%M:%S")} (время компьютера)')
                if self.uploader and self.uploader.paused:
                    self.upload_enabled = False
                if status:
                    self.connection.config(text=status)
        if (getattr(self, 'redemption_pending', False) and self.pairing
                and self.pairing.deadline and time.monotonic() >= self.pairing.deadline):
            self.wait_for_redemption()
        if getattr(self, 'pair_job', None) and time.monotonic() >= self.pair_job[1]:
            if getattr(self, 'redemption_pending', False):
                self.wait_for_redemption()
            else:
                self.busy = False
                self.pairing_failed('SPHOL не ответил вовремя. Очередь и привязка сохранены. Нажмите «Повторить привязку».')
        if self.pairing and self.pairing.deadline and time.monotonic() >= self.pairing.deadline and not getattr(self, 'redemption_pending', False):
            self.busy = False
            self.pairing_failed('Время подтверждения истекло. Сбор не начат; очередь сохранена. Нажмите «Повторить привязку».')
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
