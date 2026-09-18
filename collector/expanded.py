"""Explicit cloud Gamelogs mode, independent of local-only session recording."""
from datetime import datetime, timezone
import re
from .core import MAX_LINE, PendingQueue, Tailer
from .signals import sanitize

SCOPE = 'gamelogs:write'
CONSENT = ('Отправлять на sphol.com только распознанные сигналы варпа флота, дальности модуля и неуязвимости цели? '
           'Имена командиров, названия модулей, расстояния и маршруты не отправляются. '
           'Неизвестные строки, личные уведомления, Chatlogs, ссылки и торговля не отправляются. '
           'Боевые события продолжают передаваться отдельно по прежним правилам. '
           'Это отдельное согласие на отправку, НЕ локальная запись. История до включения не копируется. '
           'Нужно новое подтверждение области gamelogs:write в браузере; старая привязка и очереди сохранятся.')
UNSUPPORTED = ('Расширенный режим сервером не подтверждён или пока не поддерживается. '
               'Отправка разрешённых сигналов выключена; старая привязка и обе очереди сохранены.')
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
    text = sanitize(match[2], match[3])
    if text is None:
        return None
    if not text.strip() or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in text):
        return None
    return {'schema': 2, 'time': when.isoformat(), 'type': 'game-event',
            'category': match[2], 'listener': listener, 'text': text}


class ExpandedQueue(PendingQueue):
    """Separate bounded file; never migrate IDs or read local-captures."""
    def __init__(self, directory, **limits):
        super().__init__(directory / 'pending-v2.sqlite3', **limits)

    def put(self, event_id, event):
        if (event.get('schema') != 2 or not isinstance(event.get('text'), str)
                or sanitize(event.get('category'), event['text']) != event['text']):
            raise ValueError('Expanded queue requires a canonical safe signal')
        super().put(event_id, event)


def capture(root, queue, *, consent=False, credentials=None, started=None):
    if consent is not True or not credentials or credentials.get('scope') != SCOPE:
        raise ValueError('Explicit cloud consent and browser-approved scope required')
    # Keep the old parser for historical inspection, never capture a second
    # stream. Canonical combat capture belongs exclusively to the v1 Tailer.
    return Tailer(root, queue, started=started, parser=lambda raw, listener='': None)
