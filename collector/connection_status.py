"""Independent read-only authentication probe; no queue/ACK or Tk worker access."""
import http.client
import queue
import ssl
import threading
import time
from .transport import HTTPFailure, ProtocolError, decode, expiry, validate_credentials

TTL = 45
TIMEOUT = 6


def probe(credentials):
    validate_credentials(credentials)
    con = http.client.HTTPSConnection('sphol.com', timeout=TIMEOUT, context=ssl.create_default_context())
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
            if generation == self.generation and now - self.started > TIMEOUT:
                self.state = 'offline'
                self.next_try = now + 20
            if generation == self.generation and now - self.started <= TIMEOUT:
                self.state = state
                self.fresh = now if state == 'connected' else 0
                self.failures = 0 if state == 'connected' else min(4, self.failures + 1)
                self.next_try = now + (30 if state == 'connected' else min(120, 10 * 2 ** self.failures))
        if self.state == 'connected' and now - self.fresh >= TTL:
            self.state = 'unknown'
        if self.worker and self.worker.is_alive():
            if now - self.started > TIMEOUT:
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


LABELS = {
    'checking': 'Проверяем подключение к SPHOL…',
    'connected': '● Подключено к SPHOL',
    'offline': 'Нет связи с SPHOL · повтор автоматически',
    'denied': 'Доступ отклонён: отозван, истёк или нет разрешения',
    'unknown': 'Подключение к SPHOL не подтверждено',
    'stopped': 'Проверка связи остановлена · сбор выключен',
}
