"""Opt-in browser recovery protocol. No capture, cancellation or UI side effects."""
import base64
import hashlib
import re
import secrets
import time
from .credentials import crypt
from .version import VERSION
from .core import safe_open
from .diagnostics import atomic
from .transport import HTTPS, PAIR_URI, ProtocolError, encode, decode, validate_credentials, valid_eve_authorize_url


class BrowserApprovalExpired(ProtocolError):
    """Explicit server expiry; only a same-claim recovery may follow a replay probe."""


def validate_claim(value):
    base = {'device_secret', 'verifier', 'scope', 'recovery'}
    proof = base | {'browser_proof', 'approval_expires'}
    proof_with_url = proof | {'browser_uri'}
    if not isinstance(value, dict) or set(value) - {'renewal'} not in (base, proof, proof_with_url):
        raise ProtocolError('Invalid browser recovery claim')
    if value['recovery'] is not True or value['scope'] not in ('combat:write', 'gamelogs:write'):
        raise ProtocolError('Invalid browser consent')
    for key in ('device_secret', 'verifier'):
        if not isinstance(value[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value[key]):
            raise ProtocolError('Invalid browser proof')
    if 'browser_proof' in value:
        if not isinstance(value['browser_proof'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value['browser_proof']):
            raise ProtocolError('Invalid approval proof')
        if type(value['approval_expires']) not in (int, float) or not 0 < value['approval_expires'] < 1e12:
            raise ProtocolError('Invalid approval expiry')
        if 'browser_uri' in value and not valid_eve_authorize_url(value['browser_uri']):
            raise ProtocolError('Invalid EVE approval URL')
    if 'renewal' in value:
        old = validate_credentials(value['renewal'], allow_expired=True)
        if (old['scope'] != value['scope'] or len(old['characters']) != 1
                or not re.fullmatch(r'[0-9a-f]{32}', old['installation_id'])):
            raise ProtocolError('Invalid renewal proof')
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
            owner = ('device_secret', 'verifier', 'scope', 'recovery', 'browser_proof', 'renewal')
            same_owner = all(existing.get(k) == payload.get(k) for k in owner if k in existing or k in payload)
            only_url_update = (same_owner and set(payload) <= {'device_secret', 'verifier', 'scope', 'recovery', 'browser_proof', 'approval_expires', 'browser_uri', 'renewal'}
                               and set(existing) <= {'device_secret', 'verifier', 'scope', 'recovery', 'browser_proof', 'approval_expires', 'browser_uri', 'renewal'})
            if not only_url_update:
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

    def start(self, scope, renewal=None):
        if self.pending.load() is not None:
            raise ProtocolError('Uncertain browser claim preserved; retry redemption')
        existing = self.store.load_proof() if hasattr(self.store, 'load_proof') else self.store.load()
        if renewal is not None:
            validate_credentials(renewal, allow_expired=True)
            if (existing != renewal or renewal['scope'] != scope or len(renewal['characters']) != 1
                    or not re.fullmatch(r'[0-9a-f]{32}', renewal['installation_id'])):
                raise ProtocolError('Invalid renewal proof')
        elif existing is not None:
            raise ProtocolError('Existing binding preserved')
        if scope not in ('combat:write', 'gamelogs:write'):
            raise ProtocolError('Invalid scope')
        verifier = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        request = dict(
            challenge=challenge, challenge_method='S256', scope=scope, recovery=True, direct_eve=True,
            client_version=VERSION, device_label='SPHOL Windows collector')
        if renewal is not None:
            request['renewal'] = dict(installation_id=renewal['installation_id'], old_token=renewal['access_token'])
        status, data = self.http.post('/api/collector/v1/pairings/browser', request)
        if (status not in (200, 201) or not isinstance(data, dict)
                or set(data) != {'device_secret', 'user_code', 'verification_uri', 'expires_in', 'interval'}
                or not valid_eve_authorize_url(data['verification_uri'])
                or not isinstance(data['user_code'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', data['user_code'])
                or type(data['expires_in']) is not int or not 1 <= data['expires_in'] <= 300
                or type(data['interval']) is not int or not 1 <= data['interval'] <= 30):
            raise ProtocolError('Invalid browser response')
        claim = validate_claim(dict(device_secret=data['device_secret'], verifier=verifier, scope=scope, recovery=True,
                                    browser_proof=data['user_code'], approval_expires=time.time() + data['expires_in'],
                                    browser_uri=data['verification_uri']))
        if renewal is not None:
            claim['renewal'] = renewal
        self.pending.save(claim)
        if self.pending.load() != claim:
            raise ProtocolError('Pending verification failed')
        return data['verification_uri']

    def recover_url(self):
        payload = validate_claim(self.pending.load())
        if 'browser_proof' not in payload:
            raise ProtocolError('Browser approval proof unavailable')
        wire = {k: payload[k] for k in ('device_secret', 'verifier', 'browser_proof', 'scope', 'recovery')}
        status, data = self.http.post('/api/collector/v1/pairings/browser/recover', wire)
        if (status != 200 or not isinstance(data, dict) or set(data) != {'verification_uri', 'expires_in', 'interval'}
                or not valid_eve_authorize_url(data['verification_uri'])
                or type(data['expires_in']) is not int or not 1 <= data['expires_in'] <= 300
                or type(data['interval']) is not int or not 1 <= data['interval'] <= 30):
            raise ProtocolError('Invalid browser recovery response')
        updated = dict(payload, browser_uri=data['verification_uri'], approval_expires=time.time() + data['expires_in'])
        self.pending.save(updated)
        return data['verification_uri']

    def redeem(self):
        payload = validate_claim(self.pending.load())
        wire = {k: payload[k] for k in ('device_secret', 'verifier', 'scope', 'recovery')}
        status, data = self.http.post('/api/collector/v1/pairings/token', wire)
        if status == 400 and data in ({'error': 'authorization_pending'}, {'error': 'slow_down'}):
            return None
        if status == 410 and data == {'error': 'expired_token'}:
            raise BrowserApprovalExpired('Browser approval expired; claim retained')
        if status != 200:
            raise ProtocolError('Browser redemption uncertain; claim retained')
        result = validate_credentials(data)
        if result['scope'] != payload['scope'] or len(result['characters']) != 1:
            raise ProtocolError('Consent mismatch')
        old = payload.get('renewal')
        if old is not None and (any(result[k] != old[k] for k in ('installation_id', 'scope'))
                or result['characters'][0]['id'] != old['characters'][0]['id']):
            raise ProtocolError('Renewal identity mismatch')
        self.unsaved_result = result
        existing = self.store.load_proof() if hasattr(self.store, 'load_proof') else self.store.load()
        if existing != result and (existing != old or (old is None and existing is not None)):
            raise ProtocolError('Existing binding preserved')
        if existing != result:
            self.store.save(result)
        if self.store.load() != result:
            raise ProtocolError('Credential verification failed')
        self.pending.clear()
        self.unsaved_result = None
        return result
