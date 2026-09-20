"""Use Windows chain validation without changing global SSL behavior."""
import ssl
import sys


def create_context():
    if sys.platform == 'win32':
        import truststore
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    else:
        context = ssl.create_default_context()
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
        raise RuntimeError('TLS verification required')
    return context
