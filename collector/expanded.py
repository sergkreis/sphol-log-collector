"""Explicit cloud Gamelogs mode, independent of local-only session recording."""
from datetime import datetime, timezone
import re
from .core import MAX_LINE, PendingQueue, Tailer

SCOPE = 'gamelogs:write'
CONSENT = ('Отправлять на sphol.com новые строки Gamelogs ВСЕХ типов (combat, notify, None и другие)? '
           'Они могут содержать личные и чувствительные уведомления. Chatlogs и диагностика не читаются. '
           'Исходный текст хранится приватно; корпорации доступны только разрешённые сервером сигналы. '
           'Классификация и связь с боями выполняются сервером автоматически. '
           'Это отдельное согласие на отправку, НЕ локальная запись. История до включения не копируется. '
           'Нужно новое подтверждение области gamelogs:write в браузере; старая привязка и очереди сохранятся.')
UNSUPPORTED = ('Расширенный режим сервером не подтверждён или пока не поддерживается. '
               'Отправка всех Gamelogs выключена; старая привязка и обе очереди сохранены.')
LINE = re.compile(r'^\s*\[\s*(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s*\]\s*\(([A-Za-z0-9_-]{1,32})\) ?(.*)$')


def parse_game_line(raw, listener=''):
    if len(raw) > MAX_LINE:
        return None
    try:
        match = LINE.fullmatch(raw.decode('utf-8-sig').rstrip('\r\n'))
        if not match:
            return None
        when = datetime.strptime(match[1], '%Y.%m.%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except (UnicodeError, ValueError):
        return None
    text = match[3]
    if not text.strip() or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in text):
        return None
    return {'schema': 2, 'time': when.isoformat(), 'type': 'game-event',
            'category': match[2], 'listener': listener, 'text': text}


class ExpandedQueue(PendingQueue):
    """Separate bounded file; never migrate IDs or read local-captures."""
    def __init__(self, directory, **limits):
        super().__init__(directory / 'pending-v2.sqlite3', **limits)

    def put(self, event_id, event):
        if event.get('schema') != 2:
            raise ValueError('Expanded queue requires schema 2')
        super().put(event_id, event)


def capture(root, queue, *, consent=False, credentials=None, started=None):
    if consent is not True or not credentials or credentials.get('scope') != SCOPE:
        raise ValueError('Explicit cloud consent and browser-approved scope required')
    return Tailer(root, queue, started=started, parser=parse_game_line)
