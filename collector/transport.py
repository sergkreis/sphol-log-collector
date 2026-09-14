"""Fixed-origin HTTPS protocol. Never log bodies or credentials."""
import base64
from datetime import datetime, timezone
import hashlib
import http.client
import json
import random
import re
import secrets
import ssl
import time

ORIGIN = 'https://sphol.com'
PAIR_URI = ORIGIN + '/collector/pair'
MAX_BODY = 262144


class ProtocolError(Exception):
    pass


class HTTPFailure(ProtocolError):
    def __init__(self, status, retry_after=0):
        super().__init__(f'Server HTTP {status}; pending events retained')
        self.status, self.retry_after = status, retry_after


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def decode(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ProtocolError('Duplicate JSON key')
            out[key] = value
        return out
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, TypeError):
        raise ProtocolError('Invalid server JSON') from None


class HTTPS:
    def post(self, path, payload, token=None):
        if path not in ('/api/collector/v1/pairings', '/api/collector/v1/pairings/token', '/api/collector/v1/events'):
            raise ProtocolError('Disallowed endpoint')
        body = encode(payload)
        if len(body) > MAX_BODY:
            raise ProtocolError('Request exceeds byte limit')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if token is not None:
            if not opaque(token):
                raise ProtocolError('Invalid credential')
            headers['Authorization'] = 'Bearer ' + token
        connection = http.client.HTTPSConnection('sphol.com', timeout=15, context=ssl.create_default_context())
        try:
            connection.request('POST', path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise ProtocolError('Response exceeds byte limit')
            status = response.status
            if status not in (200, 201, 400):
                retry = response.getheader('Retry-After', '')
                raise HTTPFailure(status, min(300, max(1, int(retry))) if retry.isdigit() and len(retry) < 10 else 0)
            if response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                raise ProtocolError('Expected JSON response')
            return status, decode(raw)
        finally:
            connection.close()


def opaque(value):
    return isinstance(value, str) and re.fullmatch(r'[\x21-\x7e]{32,512}', value) is not None


def expiry(value):
    try:
        when = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if when.utcoffset() is None or when.utcoffset().total_seconds() != 0:
            raise ValueError()
        return when.timestamp()
    except (ValueError, TypeError, AttributeError):
        raise ProtocolError('Invalid credential expiry') from None


def validate_credentials(data):
    keys = {'access_token', 'token_type', 'scope', 'installation_id', 'expires_at', 'characters'}
    if not isinstance(data, dict) or set(data) != keys:
        raise ProtocolError('Invalid credentials response')
    if not opaque(data['access_token']) or data['token_type'] != 'Bearer' or data['scope'] not in ('combat:write', 'gamelogs:write'):
        raise ProtocolError('Invalid credential scope')
    if not isinstance(data['installation_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', data['installation_id']):
        raise ProtocolError('Invalid installation')
    if expiry(data['expires_at']) <= time.time():
        raise ProtocolError('Credentials expired; pair again')
    chars = data['characters']
    if not isinstance(chars, list) or not 1 <= len(chars) <= 32:
        raise ProtocolError('Invalid character bindings')
    ids, names = set(), set()
    for char in chars:
        if not isinstance(char, dict) or set(char) != {'id', 'name'}:
            raise ProtocolError('Invalid character binding')
        i, name = char['id'], char['name']
        if type(i) is not int or i <= 0 or not isinstance(name, str) or not 1 <= len(name) <= 128 or not name.isprintable() or i in ids or name in names:
            raise ProtocolError('Ambiguous character binding')
        ids.add(i)
        names.add(name)
    return data


class Pairing:
    def __init__(self, http=None, scope='combat:write'):
        if scope not in ('combat:write', 'gamelogs:write'):
            raise ProtocolError('Invalid requested scope')
        self.scope = scope
        self.http = http or HTTPS()
        self.verifier = secrets.token_urlsafe(32)
        self.deadline = self.next_poll = 0

    def start(self):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode('ascii')).digest()).rstrip(b'=').decode()
        status, data = self.http.post('/api/collector/v1/pairings', {
            'challenge': challenge, 'challenge_method': 'S256', 'client_version': '0.2.0',
            'device_label': 'SPHOL Windows collector',
            **({'scope': self.scope} if self.scope != 'combat:write' else {})})
        keys = {'device_secret', 'user_code', 'verification_uri', 'expires_in', 'interval'}
        if status not in (200, 201) or not isinstance(data, dict) or set(data) != keys:
            raise ProtocolError('Invalid pairing response')
        if not opaque(data['device_secret']) or data['verification_uri'] != PAIR_URI or not isinstance(data['user_code'], str) or not re.fullmatch('[A-Z0-9-]{4,32}', data['user_code']):
            raise ProtocolError('Invalid pairing origin or code')
        if type(data['expires_in']) is not int or not 1 <= data['expires_in'] <= 300 or type(data['interval']) is not int or not 1 <= data['interval'] <= 30:
            raise ProtocolError('Invalid pairing timing')
        self.secret, self.interval = data['device_secret'], data['interval']
        self.deadline = time.monotonic() + data['expires_in']
        self.next_poll = time.monotonic() + self.interval
        return data['user_code']

    def poll(self):
        now = time.monotonic()
        if now >= self.deadline:
            raise ProtocolError('Pairing expired; start again')
        if now < self.next_poll:
            return None
        self.next_poll = now + self.interval
        status, data = self.http.post('/api/collector/v1/pairings/token', {'device_secret': self.secret, 'verifier': self.verifier})
        if status == 400 and data in ({'error': 'authorization_pending'}, {'error': 'slow_down'}):
            if data['error'] == 'slow_down':
                self.interval = min(60, self.interval + 5)
                self.next_poll = now + self.interval
            return None
        if status != 200:
            raise HTTPFailure(status)
        result = validate_credentials(data)
        if result['scope'] != self.scope:
            raise ProtocolError('Requested scope was not approved; expanded mode unavailable')
        self.deadline = 0  # Redemption must never be repeated.
        self.secret = self.verifier = ''
        return result


def build_batch(queue, credentials):
    expanded = credentials['scope'] == 'gamelogs:write'
    schema = 2 if expanded else 1
    names = {c['name'] for c in credentials['characters']}
    events = []
    for event in queue.batch(100):
        if event.get('listener') not in names:
            continue
        if not isinstance(event.get('id'), str) or not re.fullmatch('[0-9a-f]{64}', event['id']):
            raise ProtocolError('Malformed pending event; delete pending to recover')
        v2 = expanded and event.get('schema') == 2
        keys = {'id', 'schema', 'time', 'type', 'listener', 'text'} | ({'category'} if v2 else set())
        if (set(event) != keys
                or type(event['schema']) is not int or event['schema'] != (2 if v2 else 1)
                or event['type'] != ('game-event' if v2 else 'combat')
                or (v2 and (not isinstance(event.get('category'), str) or not re.fullmatch('[A-Za-z0-9_-]{1,32}', event['category'])))
                or not isinstance(event['text'], str) or not event['text'] or len(event['text'].encode('utf-8')) > 8192
                or not event['text'].isprintable() or not isinstance(event['listener'], str)
                or not 1 <= len(event['listener']) <= 128 or not event['listener'].isprintable()):
            raise ProtocolError('Malformed pending event; delete pending to recover')
        if v2:
            from .signals import sanitize
            if sanitize(event['category'], event['text']) != event['text']:
                raise ProtocolError('Unsafe legacy expanded record retained locally; not uploaded')
        expiry(event['time'])  # Strict UTC format, without treating old events as credential expiry.
        candidate = {'schema': schema, 'events': events + [event]}
        if len(encode(candidate)) > MAX_BODY:
            if not events:
                raise ProtocolError('Oversized pending event; delete pending to recover')
            break
        events.append(event)
    return {'schema': schema, 'events': events}


def validate_ack(data, events):
    if not isinstance(data, dict) or set(data) != {'accepted_ids', 'rejected'} or not isinstance(data['accepted_ids'], list) or not isinstance(data['rejected'], list):
        raise ProtocolError('Malformed acknowledgement; nothing deleted')
    ids = list(data['accepted_ids'])
    for item in data['rejected']:
        if not isinstance(item, dict) or set(item) != {'id', 'reason'} or item['reason'] not in ('invalid_event', 'unapproved_character', 'id_conflict', 'timestamp_out_of_range'):
            raise ProtocolError('Malformed rejection; nothing deleted')
        ids.append(item['id'])
    if any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)) or set(ids) != {e['id'] for e in events}:
        raise ProtocolError('Incomplete or foreign acknowledgement; nothing deleted')
    return data['accepted_ids'], data['rejected']


class Uploader:
    def __init__(self, credentials, http=None):
        self.credentials = validate_credentials(credentials)
        self.http = http or HTTPS()
        self.next_try = 0
        self.failures = 0
        self.paused = False

    def upload(self, queue):
        if self.paused or time.monotonic() < self.next_try:
            return None
        try:
            validate_credentials(self.credentials)
            payload = build_batch(queue, self.credentials)
            if not payload['events']:
                return 'Нет событий привязанного персонажа; остальные остаются в очереди.'
            status, data = self.http.post('/api/collector/v1/events', payload, self.credentials['access_token'])
            if status != 200:
                raise HTTPFailure(status)
            accepted, rejected = validate_ack(data, payload['events'])
            queue.acknowledge(accepted)
            self.failures = 0
            if rejected:
                self.paused = True
                return f'Сервер отклонил события: {len(rejected)}. Они сохранены; отправка приостановлена.'
            return f'Сервер подтвердил сохранение: {len(accepted)} событий.'
        except (OSError, http.client.HTTPException, HTTPFailure) as error:
            if isinstance(error, HTTPFailure) and error.status != 429 and error.status < 500:
                self.paused = True
                raise
            self.failures = min(9, self.failures + 1)
            delay = min(300, 2 ** self.failures + random.random())
            if isinstance(error, HTTPFailure):
                delay = max(delay, error.retry_after)
            self.next_try = time.monotonic() + delay
            return 'Сеть или сервер недоступны; очередь сохранена, повтор запланирован.'
        except ProtocolError:
            self.paused = True
            raise
