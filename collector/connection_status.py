"""Connection freshness plus separate startup delivery service check.

ConnectionStatus remains the native GET /connection freshness probe with TTL.
DeliveryCheck is a distinct one-shot POST /delivery-check state machine.
"""
import http.client
import queue
import re
import secrets
import ssl
import threading
import time
from .transport import HTTPFailure, ProtocolError, HTTPS, decode, expiry, validate_credentials

TTL = 45
TIMEOUT = 15
CONNECTION_TIMEOUT = 6


def probe(credentials):
    validate_credentials(credentials)
    con = http.client.HTTPSConnection('sphol.com', timeout=CONNECTION_TIMEOUT, context=ssl.create_default_context())
    try:
        con.request('GET', '/api/collector/v1/connection', headers={
            'Authorization': 'Bearer ' + credentials['access_token'],
            'Accept': 'application/json', 'Cache-Control': 'no-cache'})
        response = con.getresponse()
        if response.status != 200:
            raise HTTPFailure(response.status)
        if response.getheader('Content-Type', '').split(';')[0].strip() != 'application/json':
            raise ProtocolError('Expected JSON')
        raw = response.read(8193)
        if len(raw) > 8192:
            raise ProtocolError('Oversized connection response')
        data = decode(raw)
        keys = {'installation_id', 'scope', 'expires_at', 'characters'}
        if not isinstance(data, dict) or set(data) != keys or any(data[k] != credentials[k] for k in keys):
            raise ProtocolError('Connection identity mismatch')
        if expiry(data['expires_at']) <= time.time():
            raise HTTPFailure(401)
    finally:
        con.close()


def strict_utc_timestamp(value, *, now=None):
    if not isinstance(value, str) or not (20 <= len(value) <= 40):
        raise ProtocolError('Invalid delivery check timestamp')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', value):
        raise ProtocolError('Invalid delivery check timestamp')
    when = expiry(value)
    current = time.time() if now is None else now
    if when < 1704067200 or when > current + 300:
        raise ProtocolError('Invalid delivery check timestamp')
    return when


def delivery_probe(credentials, check_id, http=None):
    validate_credentials(credentials)
    if not isinstance(check_id, str) or re.fullmatch(r'[0-9a-f]{64}', check_id) is None:
        raise ProtocolError('Invalid delivery check id')
    status, data = (http or HTTPS()).post('/api/collector/v1/delivery-check', {
        'type': 'startup-delivery-check', 'check_id': check_id,
    }, credentials['access_token'])
    if status != 200:
        raise HTTPFailure(status)
    if (not isinstance(data, dict) or set(data) != {'type', 'check_id', 'received_at'}
            or data['type'] != 'startup-delivery-check' or data['check_id'] != check_id):
        raise ProtocolError('Delivery check ACK mismatch')
    strict_utc_timestamp(data['received_at'])
    if expiry(credentials['expires_at']) <= time.time():
        raise HTTPFailure(401)
    return data['received_at']


class ConnectionStatus:
    def __init__(self, check=None, clock=time.monotonic):
        self.check, self.clock = check or probe, clock
        self.results = queue.Queue()
        self.state = 'stopped'
        self.key = None
        self.generation = 0
        self.worker = None
        self.started = self.fresh = self.next_try = 0
        self.failures = 0

    def tick(self, credentials, enabled):
        now = self.clock()
        key = tuple(str(credentials.get(k)) for k in ('access_token', 'installation_id', 'scope', 'expires_at', 'characters')) if credentials else None
        if not enabled or not credentials:
            if self.key is not None:
                self.generation += 1
            self.key = None
            self.state = 'stopped' if credentials else 'unknown'
            self.fresh = 0
            return self.state
        if key != self.key:
            self.key = key
            self.generation += 1
            self.fresh = self.next_try = 0
            self.failures = 0
            self.state = 'unknown'
        try:
            if expiry(credentials['expires_at']) <= time.time():
                self.state = 'denied'
                self.fresh = 0
                return self.state
        except ProtocolError:
            self.state = 'unknown'
            return self.state
        while not self.results.empty():
            generation, state = self.results.get_nowait()
            if generation == self.generation and now - self.started > CONNECTION_TIMEOUT:
                self.state = 'offline'
                self.next_try = now + 20
            if generation == self.generation and now - self.started <= CONNECTION_TIMEOUT:
                self.state = state
                self.fresh = now if state == 'connected' else 0
                self.failures = 0 if state == 'connected' else min(4, self.failures + 1)
                self.next_try = now + (30 if state == 'connected' else min(120, 10 * 2 ** self.failures))
        if self.state == 'connected' and now - self.fresh >= TTL:
            self.state = 'unknown'
        if self.worker and self.worker.is_alive():
            if now - self.started > CONNECTION_TIMEOUT:
                self.state = 'offline'
                self.fresh = 0
            return self.state
        if self.state == 'checking':
            self.state = 'offline'
            self.next_try = now + 20
        if now >= self.next_try:
            self.started = now
            generation = self.generation
            self.state = 'checking'
            snapshot = dict(credentials)
            def run():
                try:
                    self.check(snapshot)
                    state = 'connected'
                except HTTPFailure as error:
                    state = 'denied' if error.status in (401, 403) else ('unknown' if error.status == 404 else 'offline')
                except (OSError, http.client.HTTPException):
                    state = 'offline'
                except Exception:
                    state = 'unknown'
                self.results.put((generation, state))
            self.worker = threading.Thread(target=run, daemon=True)
            self.worker.start()
        return self.state


class DeliveryCheck:
    def __init__(self, check=None, clock=time.monotonic):
        self.check, self.clock = check or delivery_probe, clock
        self.results = queue.Queue()
        self.state = 'stopped'
        self.key = None
        self.generation = 0
        self.worker = None
        self.started = self.next_try = 0
        self.failures = 0
        self.check_id = None
        self.ack_at = None
        self.ack_received_at = None

    def tick(self, credentials, enabled=True):
        now = self.clock()
        key = tuple(str(credentials.get(k)) for k in ('access_token', 'installation_id', 'scope', 'expires_at', 'characters')) if credentials else None
        if not enabled or not credentials:
            if self.key is not None:
                self.generation += 1
            self.key = None
            self.state = 'stopped' if credentials else 'unknown'
            return self.state
        if key != self.key:
            self.key = key
            self.generation += 1
            self.next_try = now
            self.failures = 0
            self.check_id = secrets.token_hex(32)
            self.ack_at = self.ack_received_at = None
            self.state = 'unknown'
        try:
            if expiry(credentials['expires_at']) <= time.time():
                self.state = 'denied'
                return self.state
        except ProtocolError:
            self.state = 'unknown'
            return self.state
        while not self.results.empty():
            generation, state, received_at = self.results.get_nowait()
            if generation != self.generation:
                continue
            if now - self.started > TIMEOUT:
                self.state = 'offline'
                self.failures = min(20, self.failures + 1)
                self.next_try = now + min(300, 10 * 2 ** min(self.failures, 5))
                continue
            self.state = state
            if state == 'connected':
                self.failures = 0
                self.ack_at = now
                self.ack_received_at = received_at
                self.next_try = float('inf')
            else:
                self.failures = min(20, self.failures + 1)
                self.next_try = now + min(300, 10 * 2 ** min(self.failures, 5))
        if self.worker and self.worker.is_alive():
            if now - self.started > TIMEOUT:
                self.state = 'offline'
            return self.state
        if self.state == 'checking':
            self.state = 'offline'
            self.failures = min(20, self.failures + 1)
            self.next_try = now + min(300, 10 * 2 ** min(self.failures, 5))
        if now >= self.next_try:
            self.started = now
            generation = self.generation
            snapshot = dict(credentials)
            check_id = self.check_id
            self.state = 'checking'
            def run():
                try:
                    received_at = self.check(snapshot, check_id)
                    state = 'connected'
                except HTTPFailure as error:
                    received_at = None
                    state = 'denied' if error.status in (401, 403) else ('unknown' if error.status == 404 else 'offline')
                except (OSError, http.client.HTTPException):
                    received_at = None
                    state = 'offline'
                except Exception:
                    received_at = None
                    state = 'unknown'
                self.results.put((generation, state, received_at))
            self.worker = threading.Thread(target=run, daemon=True)
            self.worker.start()
        return self.state

    def shutdown(self, timeout=1.0):
        self.generation += 1
        self.next_try = float('inf')
        self.key = None
        self.state = 'stopped'
        while not self.results.empty():
            try:
                self.results.get_nowait()
            except queue.Empty:
                break
        worker = self.worker
        if worker and worker.is_alive():
            worker.join(timeout)


LABELS = {
    'checking': 'Проверяем подключение к SPHOL…',
    'connected': '● Подключено к SPHOL',
    'offline': 'Нет связи с SPHOL · повтор автоматически',
    'denied': 'Доступ отклонён: отозван, истёк или нет разрешения',
    'unknown': 'Подключение к SPHOL не подтверждено',
    'stopped': 'Проверка связи остановлена · сбор выключен',
}

DELIVERY_LABELS = {
    'checking': 'Проверка отправки: проверяем…',
    'connected': 'Проверка отправки: пройдена',
    'offline': 'Проверка отправки: нет ответа · повторяем',
    'denied': 'Проверка отправки: доступ отклонён',
    'unknown': 'Проверка отправки: не подтверждена',
    'stopped': 'Проверка отправки: нет привязки',
}
