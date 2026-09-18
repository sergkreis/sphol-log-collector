"""Read-only main-screen projection. Counters never infer delivery from queue depth."""
import time
from tkinter import ttk
from .queue_status import queue_counts


class Dashboard:
    def __init__(self, app, parent):
        self.app = app
        self.baseline = app.queue.inserted_count
        self.confirmed = 0
        self.ack_at = None
        self.heading = ttk.Label(parent, text='Сбор выключен', style='Status.TLabel')
        self.heading.pack(anchor='w', pady=(0, 8))
        self.metrics = []
        row = ttk.Frame(app.settings)
        row.pack(fill='x', pady=8)
        self.subtitle = ttk.Label(parent, style='Muted.TLabel', wraplength=510, justify='center')
        self.subtitle.pack(before=app.capture_buttons, anchor='center')
        self.notice = ttk.Label(parent, style='Warning.TLabel', wraplength=510, justify='center')
        self.notice.pack(anchor='center')
        self.sent = ttk.Label(parent)
        self.sent.pack(anchor='center', pady=(10, 4))
        self.waiting = ttk.Label(parent, style='Muted.TLabel')
        self.waiting.pack(anchor='center')
        for i, (title, note) in enumerate([
            ('Собрано', 'за запуск · все персонажи'),
            ('Сервер принял', 'все события · за запуск'),
            ('К отправке', 'все события · ваш персонаж'),
        ]):
            row.columnconfigure(i, weight=1, uniform='metric')
            cell = ttk.Frame(row)
            cell.grid(row=0, column=i, sticky='nsew', padx=(0, 12))
            ttk.Label(cell, text=title, style='Muted.TLabel').pack(anchor='w')
            value = ttk.Label(cell, text='0', style='Metric.TLabel')
            value.pack(anchor='w', pady=(4, 2))
            ttk.Label(cell, text=note, style='Small.TLabel').pack(anchor='w')
            self.metrics.append(value)
        self.warning = ttk.Label(app.settings, style='Warning.TLabel', wraplength=660)
        self.detail = ttk.Label(app.settings, style='Muted.TLabel', wraplength=660)
        self.detail.pack(anchor='w', pady=8)

    def acknowledge(self, ids):
        # Called only after validated transport ACK and durable queue deletion.
        if ids:
            self.confirmed += len(set(ids))
            self.ack_at = time.monotonic()

    def refresh(self):
        app = self.app
        collected = app.queue.inserted_count - self.baseline
        listeners = {c['name'] for c in app.uploader.credentials['characters']} if getattr(app, 'uploader', None) else set()
        counts = queue_counts(app.queue, listeners)
        expanded = getattr(app, 'expanded', None)
        confirmed, eligible = self.confirmed, counts['eligible']
        if expanded:
            collected += expanded.queue.inserted_count - expanded.baseline
            confirmed += expanded.ack_count
            names = {c['name'] for c in expanded.uploader.credentials['characters']} if expanded.uploader else set()
            eligible += queue_counts(expanded.queue, names)['eligible']
        for label, value in zip(self.metrics, (collected, confirmed, eligible)):
            label.config(text=f'{value:,}'.replace(',', ' '))
        status = app.status.get()
        if getattr(app, 'capture_problem', False) or 'ошибк' in status.lower() or 'проверьте' in status:
            heading = 'Не удалось читать журналы'
        elif 'приостановлен' in status:
            heading = 'Сбор приостановлен'
        elif app.tailer:
            heading = 'Сбор включён' if collected else 'Ждём события'
        else:
            heading = 'Сбор выключен'
        expanded = getattr(app, 'expanded', None)
        problems = []
        if getattr(app, 'upload_problem', False) or (getattr(app, 'uploader', None) and (app.uploader.failures or app.uploader.paused)):
            problems.append('боевые')
        if expanded and (getattr(expanded, 'capture_problem', False) or getattr(expanded, 'problem', False) or (expanded.uploader and (expanded.uploader.failures or expanded.uploader.paused))):
            problems.append('наблюдения')
        legacy = getattr(app, 'legacy', None)
        if legacy and legacy.queue.count() and (legacy.problem or (legacy.enabled and legacy.uploader and (legacy.uploader.failures or legacy.uploader.paused))):
            problems.append('прежние наблюдения')
        uploader = getattr(app, 'uploader', None)
        full = uploader and uploader.credentials['scope'] == 'gamelogs:write'
        self.subtitle.config(text=('Боевые события и обезличенные наблюдения' if full else 'Только боевые события') if uploader else '')
        state = getattr(getattr(app, 'connection_status', None), 'state', '')
        paused = any(u and u.paused for u in (uploader, getattr(expanded, 'uploader', None)))
        notice = ''
        if state == 'denied' or paused:
            notice = 'Отправка приостановлена. Данные сохранены. Проверьте доступ в настройках.'
        elif problems:
            retrying = (app.upload_enabled and not getattr(app, 'upload_problem', False)
                        and not (expanded and (expanded.problem or expanded.capture_problem or not expanded.enabled)))
            notice = 'Не отправляются: ' + ', '.join(problems) + '. Данные сохранены.'
            notice += ' Повтор автоматически.' if retrying else ' Проверьте настройки.'
        elif state == 'offline':
            notice = 'Данные сохранены. Повтор подключения автоматически.'
        elif app.tailer and not getattr(app, 'upload_enabled', False):
            notice = 'Отправка выключена. Данные сохраняются локально.'
        self.notice.config(text=notice)
        self.sent.config(text=f'Отправлено: {confirmed:,}  · за запуск'.replace(',', ' '))
        self.waiting.config(text=f'Ожидают отправки: {eligible:,}'.replace(',', ' '))
        self.heading.config(text=heading, wraplength=510)
        if counts['unknown']:
            self.warning.config(text=f"Хранятся локально: {counts['unknown']} без персонажа · диагностика ▸", style='Small.TLabel')
            self.warning.pack(fill='x', pady=(4, 8))
        else:
            self.warning.pack_forget()
        self.detail.config(text=f"Очередь: для привязки — {counts['eligible']}, без персонажа — {counts['unknown']}, остальные — {counts['other']}.\nЗаписи без персонажа не отправляются и не меняют статус текущего сбора. Автоматического присвоения персонажа нет.")
        if hasattr(app, 'last_ack'):
            text = 'Подтверждений сервера за этот запуск пока нет'
            ack_times = [v for v in (self.ack_at, getattr(expanded, 'ack_at', None)) if v is not None]
            if ack_times:
                seconds = max(0, int(time.monotonic() - max(ack_times)))
                age = f'{seconds} с' if seconds < 60 else f'{seconds // 60} мин'
                text = f'Последнее подтверждение сервера: {age} назад'
            app.last_ack.config(text=text)


def refresh(app):
    if hasattr(app, 'dashboard'):
        app.dashboard.refresh()
