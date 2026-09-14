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
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=(8, 8), before=app.capture_buttons)
        for i, (title, note) in enumerate([
            ('Боевые · собрано', 'за запуск · все персонажи'),
            ('Сервер принял', 'боевые · за запуск'),
            ('К отправке', 'боевые · ваш персонаж'),
        ]):
            row.columnconfigure(i, weight=1, uniform='metric')
            cell = ttk.Frame(row)
            cell.grid(row=0, column=i, sticky='nsew', padx=(0, 12))
            ttk.Label(cell, text=title, style='Muted.TLabel').pack(anchor='w')
            value = ttk.Label(cell, text='0', style='Metric.TLabel')
            value.pack(anchor='w', pady=(4, 2))
            ttk.Label(cell, text=note, style='Small.TLabel').pack(anchor='w')
            self.metrics.append(value)
        self.warning = ttk.Label(app.frame, style='Warning.TLabel', wraplength=660)
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
        for label, value in zip(self.metrics, (collected, self.confirmed, counts['eligible'])):
            label.config(text=f'{value:,}'.replace(',', ' '))
        status = app.status.get()
        if 'ошибк' in status or 'проверьте' in status:
            heading = 'Не удалось читать журналы'
        elif 'приостановлен' in status:
            heading = 'Сбор приостановлен'
        elif app.tailer:
            heading = 'Сбор включён' if collected else 'Ждём боевые события'
        else:
            heading = 'Сбор выключен'
        expanded = getattr(app, 'expanded', None)
        problems = []
        if getattr(app, 'upload_problem', False) or (getattr(app, 'uploader', None) and (app.uploader.failures or app.uploader.paused)):
            problems.append('боевые')
        if expanded and expanded.uploader and (expanded.uploader.failures or expanded.uploader.paused or getattr(expanded, 'problem', False)):
            problems.append('наблюдения')
        if problems:
            heading += ' · не отправляются: ' + ', '.join(problems)
        elif app.tailer and not getattr(app, 'upload_enabled', False):
            heading += ' · только локально'
        self.heading.config(text=heading, wraplength=710)
        if counts['unknown']:
            self.warning.config(text=f"Хранятся локально: {counts['unknown']} без персонажа · диагностика ▸", style='Small.TLabel')
            self.warning.pack(fill='x', pady=(4, 8), before=app.settings_button)
        else:
            self.warning.pack_forget()
        self.detail.config(text=f"Очередь: для привязки — {counts['eligible']}, без персонажа — {counts['unknown']}, остальные — {counts['other']}.\nЗаписи без персонажа не отправляются и не меняют статус текущего сбора. Автоматического присвоения персонажа нет.")
        if hasattr(app, 'last_ack'):
            text = 'Боевые: сервер ещё не подтвердил события'
            if self.ack_at is not None:
                seconds = max(0, int(time.monotonic() - self.ack_at))
                age = f'{seconds} с' if seconds < 60 else f'{seconds // 60} мин'
                text = f'Боевые: последнее подтверждение {age} назад'
            app.last_ack.config(text=text)


def refresh(app):
    if hasattr(app, 'dashboard'):
        app.dashboard.refresh()
