"""Opt-in browser recovery protocol. No capture, cancellation or UI side effects."""
import base64
import hashlib
import re
import secrets
from .credentials import crypt
from .core import safe_open
from .diagnostics import atomic
from .transport import HTTPS, PAIR_URI, ProtocolError, encode, decode, validate_credentials


def validate_claim(value):
    if not isinstance(value, dict) or set(value) != {'device_secret', 'verifier', 'scope', 'recovery'}:
        raise ProtocolError('Invalid browser recovery claim')
    if value['recovery'] is not True or value['scope'] not in ('combat:write', 'gamelogs:write'):
        raise ProtocolError('Invalid browser consent')
    for key in ('device_secret', 'verifier'):
        if not isinstance(value[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value[key]):
            raise ProtocolError('Invalid browser proof')
    return value


class BrowserPendingStore:
    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with safe_open.__wrapped__(self.path) as stream:
                raw = stream.read(65537)
        except FileNotFoundError:
            return None
        if not raw or len(raw) > 65536:
            raise ProtocolError('Invalid pending file')
        return validate_claim(decode(crypt(raw, decrypt=True)))

    def save(self, payload):
        existing = self.load()
        if existing is not None and existing != payload:
            raise ProtocolError('Uncertain browser claim preserved')
        raw = crypt(encode(validate_claim(payload)))
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic(self.path, raw)
        if self.load() != payload:
            raise ProtocolError('Pending verification failed')

    def clear(self):
        self.path.unlink(missing_ok=True)


class BrowserRecovery:
    def __init__(self, pending, store, http=None):
        self.pending, self.store = pending, store
        self.http = http or HTTPS()

    def start(self, scope):
        if self.pending.load() is not None:
            raise ProtocolError('Uncertain browser claim preserved; retry redemption')
        if self.store.load() is not None:
            raise ProtocolError('Existing binding preserved')
        if scope not in ('combat:write', 'gamelogs:write'):
            raise ProtocolError('Invalid scope')
        verifier = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        status, data = self.http.post('/api/collector/v1/pairings/browser', dict(
            challenge=challenge, challenge_method='S256', scope=scope, recovery=True,
            client_version='0.3.9', device_label='SPHOL Windows collector'))
        if (status not in (200, 201) or not isinstance(data, dict)
                or set(data) != {'device_secret', 'user_code', 'verification_uri', 'expires_in', 'interval'}
                or data['verification_uri'] != PAIR_URI
                or not isinstance(data['user_code'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', data['user_code'])
                or type(data['expires_in']) is not int or not 1 <= data['expires_in'] <= 300
                or type(data['interval']) is not int or not 1 <= data['interval'] <= 30):
            raise ProtocolError('Invalid browser response')
        claim = validate_claim(dict(device_secret=data['device_secret'], verifier=verifier, scope=scope, recovery=True))
        self.pending.save(claim)
        if self.pending.load() != claim:
            raise ProtocolError('Pending verification failed')
        return PAIR_URI + '#' + data['user_code']

    def redeem(self):
        payload = validate_claim(self.pending.load())
        status, data = self.http.post('/api/collector/v1/pairings/token', payload)
        if status == 400 and data in ({'error': 'authorization_pending'}, {'error': 'slow_down'}):
            return None
        if status != 200:
            raise ProtocolError('Browser redemption uncertain; claim retained')
        result = validate_credentials(data)
        if result['scope'] != payload['scope'] or len(result['characters']) != 1:
            raise ProtocolError('Consent mismatch')
        existing = self.store.load()
        if existing is not None and existing != result:
            raise ProtocolError('Existing binding preserved')
        if existing is None:
            self.store.save(result)
        if self.store.load() != result:
            raise ProtocolError('Credential verification failed')
        self.pending.clear()
        return result
