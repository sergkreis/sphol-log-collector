"""Compact SPHOL collector UI candidate matching the approved dark mockup."""
from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
import urllib.request

from PIL import Image

from .version import VERSION
from .connection_status import LABELS, DELIVERY_LABELS

PAGE_BG = '#0b1016'
CARD_BG = '#151e28'
TEXT = '#e7edf5'
MUTED = '#9aa8b8'
LINK = '#8ed0e8'
PRIMARY = '#86c7dc'
PRIMARY_TEXT = '#0d1820'
BUTTON = '#2d4558'
GREEN = '#8ad7aa'
AMBER = '#ffc36b'
DANGER = '#ef8791'

ASSETS = Path(__file__).with_name('assets')


def font(size: int, weight: str = 'normal'):
    return ('Segoe UI' if os.name == 'nt' else 'DejaVu Sans', size, weight)


class ModernShell:
    def __init__(self, app):
        self.app = app
        self.window = app.window
        self.screen = 'main'
        self._portrait_attempted = set()
        self.logo = self._image('sphol-corp-logo.png', 48, 48)
        self.default_portrait = self._placeholder(58, 58)
        self.frame = tk.Frame(self.window, bg=PAGE_BG)
        self.root = self.frame
        self.frame.pack(fill='both', expand=True)
        app.frame.pack_forget()
        self.window.configure(background=PAGE_BG)
        self.window.title('SPHOL — боевые журналы')
        try:
            self.window.iconphoto(True, self.logo)
        except Exception:
            pass
        self.window.minsize(560, 440)
        self.window.geometry('560x440')
        self._after = None
        self._rendered_state = None
        self.refresh()
        self.schedule()

    def _image(self, name, width, height):
        path = ASSETS / name
        try:
            image = tk.PhotoImage(file=str(path))
            if image.width() != width or image.height() != height:
                image = image.subsample(max(1, image.width() // width), max(1, image.height() // height))
            return image
        except Exception:
            return self._placeholder(width, height)

    def _placeholder(self, width, height):
        image = tk.PhotoImage(width=width, height=height)
        image.put('#2d4558', to=(0, 0, width, height))
        image.put('#3b5569', to=(6, 6, width - 6, height - 6))
        return image

    def schedule(self):
        self._after = self.window.after(500, self._tick)

    def _tick(self):
        self.refresh()
        self.schedule()

    def destroy_children(self):
        for child in self.frame.winfo_children():
            child.destroy()

    def card(self):
        self.destroy_children()
        outer = tk.Frame(self.frame, bg=PAGE_BG, padx=10, pady=4)
        outer.pack(fill='both', expand=True)
        card = tk.Frame(outer, bg=CARD_BG, padx=18, pady=7)
        card.pack(fill='both', expand=True)
        header = tk.Frame(card, bg=CARD_BG)
        header.pack(fill='x')
        tk.Label(header, image=self.logo, bg=CARD_BG).pack(side='left')
        title = tk.Frame(header, bg=CARD_BG)
        title.pack(side='left', padx=(10, 0))
        tk.Label(title, text='SPHOL', bg=CARD_BG, fg=TEXT, font=font(21, 'bold')).pack(anchor='w')
        tk.Label(title, text='Боевые журналы', bg=CARD_BG, fg=MUTED, font=font(12)).pack(anchor='w')
        return card

    def label(self, parent, text, size=16, color=TEXT, weight='normal', **pack):
        w = tk.Label(parent, text=text, bg=CARD_BG, fg=color, font=font(size, weight), anchor='w', justify='left', wraplength=470)
        w.pack(**({'anchor': 'w'} | pack))
        return w

    def button(self, parent, text, command, primary=False, **pack):
        bg = PRIMARY if primary else BUTTON
        fg = PRIMARY_TEXT if primary else TEXT
        b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=bg,
                      activeforeground=fg, relief='flat', bd=0, highlightthickness=0,
                      font=font(12, 'bold'), padx=14, pady=6, cursor='hand2')
        b.pack(**({'fill': 'x', 'pady': (12, 6)} | pack))
        return b

    def link(self, parent, text, command, **pack):
        b = tk.Button(parent, text=text, command=command, bg=CARD_BG, fg=LINK, activebackground=CARD_BG,
                      activeforeground=LINK, relief='flat', bd=0, highlightthickness=0,
                      font=font(12), padx=0, pady=0, cursor='hand2')
        b.pack(**({'anchor': 'w'} | pack))
        return b

    def character(self, card, subtitle=True):
        row = tk.Frame(card, bg=CARD_BG)
        row.pack(fill='x', pady=(8, 6))
        portrait = self.portrait_image()
        tk.Label(row, image=portrait, bg=CARD_BG).pack(side='left')
        text = tk.Frame(row, bg=CARD_BG)
        text.pack(side='left', padx=(12, 0))
        name = self.character_name()
        tk.Label(text, text=name, bg=CARD_BG, fg=TEXT, font=font(21, 'bold')).pack(anchor='w')
        if subtitle:
            tk.Label(text, text='Выбранный персонаж', bg=CARD_BG, fg=MUTED, font=font(12)).pack(anchor='w')

    def character_name(self):
        uploader = getattr(self.app, 'uploader', None)
        if uploader:
            chars = uploader.credentials.get('characters') or []
            if chars:
                return chars[0].get('name') or 'Персонаж EVE'
        return 'Персонаж EVE'

    def character_id(self):
        uploader = getattr(self.app, 'uploader', None)
        if uploader:
            chars = uploader.credentials.get('characters') or []
            if chars and isinstance(chars[0].get('id'), int):
                return chars[0]['id']
        return None

    def portrait_image(self):
        cid = self.character_id()
        if not cid:
            return self.default_portrait
        cache = self.app.queue.path.parent / 'portrait-cache'
        path = cache / (str(cid) + '.png')
        if path.exists():
            try:
                image = tk.PhotoImage(file=str(path))
                self._portrait = image
                return image
            except Exception:
                pass
        if cid not in self._portrait_attempted:
            self._portrait_attempted.add(cid)
            def fetch():
                try:
                    cache.mkdir(parents=True, exist_ok=True)
                    data = urllib.request.urlopen(f'https://images.evetech.net/characters/{cid}/portrait?size=256', timeout=5).read(262144)
                    if data.startswith(b'\x89PNG') or data.startswith(b'\xff\xd8'):
                        from io import BytesIO
                        png = Image.open(BytesIO(data)).convert('RGBA').resize((58, 58))
                        tmp = path.with_suffix('.tmp')
                        png.save(tmp, format='PNG')
                        tmp.replace(path)
                except Exception:
                    return
            threading.Thread(target=fetch, daemon=True).start()
        return self.default_portrait

    def counters(self, card):
        sent = getattr(getattr(self.app, 'dashboard', None), 'confirmed', 0)
        try:
            listeners = {c['name'] for c in self.app.uploader.credentials['characters']} if self.app.uploader else set()
            from .queue_status import queue_counts
            pending = queue_counts(self.app.queue, listeners)['eligible']
        except Exception:
            pending = self.app.queue.count() if hasattr(self.app, 'queue') else 0
        row = tk.Frame(card, bg=CARD_BG)
        row.pack(fill='x', pady=(6, 6))
        self.sent_label = tk.Label(row, text=f'Отправлено  {sent}', bg=CARD_BG, fg=TEXT, font=font(13, 'bold'))
        self.sent_label.pack(side='left')
        self.pending_label = tk.Label(row, text=f'Ждёт  {pending}', bg=CARD_BG, fg=TEXT, font=font(13, 'bold'))
        self.pending_label.pack(side='left', padx=(50, 0))

    def update_counters(self):
        if hasattr(self, 'sent_label'):
            self.sent_label.config(text=f'Отправлено  {self.sent_count()}')
        if hasattr(self, 'pending_label'):
            self.pending_label.config(text=f'Ждёт  {self.pending_count()}')

    def state(self):
        if self.screen != 'main':
            return self.screen
        if getattr(self.app, 'pairing', None) or getattr(self.app, 'busy', False) or getattr(self.app, 'recovered_token', None):
            return 'waiting'
        if not getattr(self.app, 'uploader', None):
            return 'first'
        conn = getattr(getattr(self.app, 'connection_status', None), 'state', 'unknown')
        if getattr(self.app, 'tailer', None):
            if conn in ('offline', 'denied', 'stale') or getattr(self.app.uploader, 'failures', 0):
                return 'offline'
            return 'active'
        return 'idle'

    def refresh(self):
        state = self.state()
        if state == self._rendered_state:
            self.update_dynamic(state)
            return
        self._rendered_state = state
        if state == 'settings':
            self.render_settings()
        elif state == 'help':
            self.render_help()
        elif state == 'waiting':
            self.render_waiting()
        elif state == 'first':
            self.render_first()
        else:
            self.render_bound(active=(state == 'active'), offline=(state == 'offline'))

    def update_dynamic(self, state):
        if state in ('active', 'offline', 'idle'):
            conn_color, conn, delivery, sending, detail = self.status_text(state)
            self.conn_label.config(text=conn, fg=conn_color)
            self.delivery_label.config(text=delivery)
            self.send_label.config(text=sending)
            self.detail_label.config(text=detail)
            self.update_counters()

    def pending_count(self):
        try:
            listeners = {c['name'] for c in self.app.uploader.credentials['characters']} if self.app.uploader else set()
            from .queue_status import queue_counts
            return queue_counts(self.app.queue, listeners)['eligible']
        except Exception:
            return self.app.queue.count() if hasattr(self.app, 'queue') else 0

    def sent_count(self):
        return getattr(getattr(self.app, 'dashboard', None), 'confirmed', 0)

    def delivery_text(self):
        check = getattr(self.app, 'delivery_check', None)
        state = getattr(check, 'state', 'unknown')
        text = DELIVERY_LABELS.get(state, DELIVERY_LABELS['unknown'])
        received = getattr(check, 'ack_received_at', None)
        if state == 'connected' and received:
            text += ' · подтверждено ' + self.friendly_received_at(received)
        return text

    def friendly_received_at(self, value):
        try:
            from .connection_status import strict_utc_timestamp
            when = strict_utc_timestamp(value)
            return time.strftime('%H:%M:%S', time.localtime(when))
        except Exception:
            return 'сервером'

    def connection_color(self):
        state = getattr(getattr(self.app, 'connection_status', None), 'state', 'unknown')
        if state == 'connected':
            return GREEN
        if state in ('denied', 'offline'):
            return DANGER
        if state in ('checking', 'unknown'):
            return AMBER
        return MUTED

    def connection_text(self):
        state = getattr(getattr(self.app, 'connection_status', None), 'state', 'unknown')
        compact = {'denied': 'Доступ к SPHOL отклонён', 'offline': 'Нет связи с SPHOL · повторяем',
                   'unknown': 'Связь с SPHOL: проверяется', 'stopped': 'Связь с SPHOL: проверка выключена'}
        text = compact.get(state, LABELS.get(state, LABELS['unknown']))
        return text if text.startswith('●') else '● ' + text

    def status_text(self, state):
        conn_state = getattr(getattr(self.app, 'connection_status', None), 'state', 'unknown')
        pending = self.pending_count()
        ack = getattr(getattr(self.app, 'dashboard', None), 'ack_at', None)
        failures = getattr(getattr(self.app, 'uploader', None), 'failures', 0)
        conn = self.connection_text()
        delivery = self.delivery_text()
        if state == 'offline':
            color = DANGER if conn_state == 'denied' else AMBER
            return color, conn, delivery, 'Отправка: очередь сохранена', 'Автоповтор при восстановлении связи.'
        if state == 'active':
            color = self.connection_color()
            if failures:
                return color, conn, delivery, 'Отправка: повторяем после ошибки', 'Очередь не потеряна.'
            if pending:
                return color, conn, delivery, f'Отправка: ждёт {pending}', self.last_ack_text()
            if ack is None:
                return color, conn, delivery, 'Отправка: ждёт первой записи', 'Подтверждённых отправок ещё не было.'
            return color, conn, delivery, 'Отправка: всё подтверждено', self.last_ack_text()
        return self.connection_color(), conn, delivery, 'Отправка: выключена', 'Нажмите «Начать сбор» перед боем.'

    def render_first(self):
        card = self.card()
        spacer = tk.Frame(card, bg=CARD_BG, height=12)
        spacer.pack()
        self.label(card, 'Войдите своим персонажем EVE', 21, TEXT, 'bold', pady=(0, 10))
        self.label(card, 'Вход откроется на официальном сайте EVE.', 14, '#a9b5c4')
        self.primary_button = self.button(card, 'Войти через EVE', self.app.pair, primary=True, pady=(22, 12))
        self.label(card, 'Собираем только боевые журналы. Сбор включается отдельно.', 13, '#a9b5c4', pady=(0, 8))
        self.settings_button = self.link(card, 'Настройки', lambda: self.show('settings'))

    def render_waiting(self):
        card = self.card()
        self.label(card, 'Вход через EVE', 21, TEXT, 'bold', pady=(16, 10))
        try:
            message = self.app.pairing_message.cget('text')
        except Exception:
            message = ''
        if not message:
            message = 'Откройте официальный EVE-вход и вернитесь в приложение.'
        if len(message) > 96:
            message = message[:93] + '…'
        self.label(card, message, 13, MUTED, pady=(0, 10))
        self.primary_button = self.button(card, 'Открыть EVE', self.app.open_browser, primary=True, pady=(8, 6))
        self.label(card, 'Сбор не начнётся автоматически.', 13, MUTED, pady=(0, 8))
        self.settings_button = self.link(card, 'Настройки', lambda: self.show('settings'))

    def render_bound(self, active=False, offline=False):
        card = self.card()
        self.character(card, subtitle=not offline)
        state = 'active' if active else ('offline' if offline else 'idle')
        color, conn, delivery, sending, detail = self.status_text(state)
        self.conn_label = self.label(card, conn, 13, color, 'bold', pady=(0, 4))
        self.delivery_label = self.label(card, delivery, 11, MUTED, 'normal', pady=(0, 2))
        self.send_label = self.label(card, sending, 13, TEXT, 'normal')
        self.detail_label = self.label(card, detail, 11, MUTED, pady=(2, 2))
        if active or offline:
            self.primary_button = self.button(card, 'Остановить сбор', self.app.stop, pady=(6, 5))
        else:
            self.primary_button = self.button(card, 'Начать сбор', self.app.start, primary=True, pady=(6, 5))
        self.settings_button = self.link(card, 'Настройки', lambda: self.show('settings'))
        self.counters(card)

    def last_ack_text(self):
        dash = getattr(self.app, 'dashboard', None)
        ack = getattr(dash, 'ack_at', None)
        if ack is None:
            return 'Последняя отправка — подтверждений пока нет'
        seconds = max(0, int(time.monotonic() - ack))
        if seconds < 60:
            age = f'{seconds} секунд назад'
        else:
            age = f'{seconds // 60} минут назад'
        return 'Последняя отправка — ' + age

    def render_settings(self):
        card = self.card()
        self.label(card, f'Версия {VERSION}', 14, MUTED, pady=(24, 8))
        self.button(card, 'Обновить приложение', self.update_app, pady=(12, 7))
        self.button(card, 'Сменить персонажа', self.change_character, pady=(7, 7))
        self.button(card, 'Помощь', lambda: self.show('help'), pady=(7, 22))
        self.label(card, 'В помощи — короткая инструкция.', 14, MUTED)
        tk.Frame(card, bg=CARD_BG).pack(fill='both', expand=True)
        self.link(card, '← Назад', lambda: self.show('main'))

    def render_help(self):
        card = self.card()
        self.label(card, 'Помощь', 22, TEXT, 'bold', pady=(24, 12))
        self.label(card, '1. Войдите через EVE и выберите основного персонажа.\n2. Нажмите «Начать сбор» перед боем.\n3. Если интернета нет, записи останутся на компьютере и отправятся позже.\n4. «Остановить сбор» прекращает чтение новых строк.', 14, '#a9b5c4')
        tk.Frame(card, bg=CARD_BG).pack(fill='both', expand=True)
        self.link(card, '← Назад', lambda: self.show('settings'))

    def update_app(self):
        updates = getattr(self.app, 'updates', None)
        if updates and hasattr(updates, 'check'):
            updates.check()
        elif updates and hasattr(updates, 'button'):
            try:
                updates.button.invoke()
            except Exception:
                pass

    def change_character(self):
        if self.app.queue.count():
            try:
                messagebox.showwarning('Очередь не пуста', 'Есть неотправленные записи. Смена персонажа заблокирована, чтобы не отправить очередь от чужого имени.', parent=self.window)
            except Exception:
                pass
            return
        self.app.unpair()
        self.show('main')

    def show(self, screen):
        self.screen = screen
        self._rendered_state = None
        self.refresh()
