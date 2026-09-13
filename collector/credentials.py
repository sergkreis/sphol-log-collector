"""Windows CurrentUser DPAPI. No plaintext/non-Windows fallback."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import tempfile
from .core import safe_open
from .transport import decode, encode, validate_credentials


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(raw, decrypt=False):
    if os.name != 'nt':
        raise OSError('Credential storage requires Windows DPAPI')
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = Blob(len(raw), buffer)
    result = Blob()
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN; deliberately NOT LOCAL_MACHINE.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError('Windows credential protection failed')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


class CredentialStore:
    def __init__(self, path):
        self.path = Path(path)

    def save(self, credentials):
        raw = crypt(encode(validate_credentials(credentials)))
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.credential-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def load(self):
        try:
            with safe_open(self.path) as stream:
                raw = stream.read(65537)
        except FileNotFoundError:
            return None
        if not raw or len(raw) > 65536:
            raise OSError('Invalid protected credential file')
        return validate_credentials(decode(crypt(raw, decrypt=True)))

    def clear(self):
        self.path.unlink(missing_ok=True)
