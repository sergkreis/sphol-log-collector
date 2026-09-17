"""Site-issued consent, restart-safe proof; no plaintext storage fallback."""
import re
import secrets
from .credentials import crypt
from .core import safe_open
from .diagnostics import atomic, observed
from .transport import encode, decode, validate_credentials, ProtocolError, HTTPS
from .version import VERSION

ROUTE = '/api/collector/v1/site-codes/redeem'
ERROR_CODES = {400: {'invalid_request'}, 403: {'not_member', 'scope_consent_required'},
    409: {'invalid_grant', 'active_installation_limit'}, 410: {'expired_token'},
    429: {'slow_down'}, 503: {'membership_unavailable', 'collector_capacity',
    'temporarily_unavailable', 'pairing_recovery_unavailable', 'authority_busy', 'authority_unavailable'}, 404: {'not_found'}}


def validate_request(data):
    if not isinstance(data, dict) or set(data) != {'user_code', 'verifier', 'scope', 'device_label', 'client_version'}:
        raise ProtocolError('Invalid pending request')
    if not isinstance(data['user_code'], str) or not re.fullmatch(r'[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}(?:-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}){4}', data['user_code']):
        raise ProtocolError('Invalid site code')
    if not isinstance(data['verifier'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', data['verifier']):
        raise ProtocolError('Invalid proof')
    if data['scope'] not in ('combat:write', 'gamelogs:write'):
        raise ProtocolError('Invalid scope')
    for key, limit in (('device_label', 80), ('client_version', 40)):
        if not isinstance(data[key], str) or not 1 <= len(data[key]) <= limit or not data[key].isprintable():
            raise ProtocolError('Invalid metadata')
    return data


class PendingStore:
    def __init__(self, path):
        self.path = path

    @observed('site.pending.load')
    def load(self):
        try:
            with safe_open.__wrapped__(self.path) as stream:
                raw = stream.read(65537)
        except FileNotFoundError:
            return None
        if not raw or len(raw) > 65536:
            raise ProtocolError('Invalid pending file')
        return validate_request(decode(crypt(raw, decrypt=True)))

    @observed('site.pending.save')
    def save(self, payload):
        raw = crypt(encode(validate_request(payload)))
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic(self.path, raw)
        if self.load() != payload:
            raise ProtocolError('Pending verification failed')

    @observed('site.pending.archive')
    def archive(self):
        """Reviewed abandonment, bounded and encrypted; never evict a proof.

        Copy/readback precedes unlink. A crash between these operations is
        idempotent: the same encrypted bytes find their existing archive slot.
        """
        with safe_open.__wrapped__(self.path) as stream:
            raw = stream.read(65537)
        if not raw or len(raw) > 65536:
            raise ProtocolError('Invalid pending archive size')
        directory = self.path.parent / 'site-recovery-archive'
        directory.mkdir(mode=0o700, exist_ok=True)
        for index in range(16):
            target = directory / ('recovery-%02d.dpapi' % index)
            try:
                with safe_open.__wrapped__(target) as stream:
                    existing = stream.read(65537)
            except FileNotFoundError:
                atomic(target, raw)
                with safe_open.__wrapped__(target) as stream:
                    existing = stream.read(65537)
                if existing != raw:
                    raise ProtocolError('Archive verification failed')
            if existing == raw:
                self.clear()
                return
        raise ProtocolError('Recovery archive full; pending proof preserved')

    def clear(self):
        self.path.unlink(missing_ok=True)


class Redemption:
    def __init__(self, pending, store, http=None):
        self.pending, self.store = pending, store
        self.http = http or HTTPS()

    def prepare(self, code, scope):
        previous = self.pending.load()
        if previous is not None:
            return previous  # Never replace an uncertain proof, even after clock changes.
        payload = validate_request(dict(user_code=code.strip().upper(), verifier=secrets.token_urlsafe(32),
            scope=scope, device_label='SPHOL Windows collector', client_version=VERSION))
        self.pending.save(payload)  # Must finish and verify before any network operation.
        return payload

    @observed('site.redeem')
    def redeem(self):
        payload = self.pending.load()
        if payload is None:
            raise ProtocolError('Missing recovery request')
        status, data = self.http.post(ROUTE, payload)
        if status != 200:
            raise ProtocolError('Unexpected redemption response')
        result = validate_credentials(data)
        if result['scope'] != payload['scope'] or len(result['characters']) != 1:
            raise ProtocolError('Consent mismatch')
        current = self.store.load()
        if current is not None and current != result:
            # Retain proof for recovery/revocation; never overwrite another binding.
            raise ProtocolError('Existing binding preserved')
        self.store.save(result)
        if self.store.load() != result:
            raise ProtocolError('Credential verification failed')
        self.pending.clear()
        return result
