"""Protect push secrets without a plaintext fallback.

Windows: DPAPI encrypted value. macOS/Linux: an opaque reference into an
explicit OS credential-store backend, never keyrings.alt file storage.
"""

import base64
import ctypes
import sys
import uuid
from ctypes import wintypes

SERVICE = "HKTraderDesk"


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi(value, decrypt=False):
    if sys.platform != "win32":
        raise OSError("Windows credentials cannot be used on this platform; save the secret again")
    raw = base64.b64decode(value, validate=True) if decrypt else value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    fn.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError("System could not unlock the saved secret; save it again")
    try:
        result = ctypes.string_at(target.pbData, target.cbData)
        return result.decode("utf-8") if decrypt else base64.b64encode(result).decode("ascii")
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def secure_backend():
    # Select OS backends explicitly so an installed plaintext plugin is never used.
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring
    elif sys.platform.startswith("linux"):
        from keyring.backends.SecretService import Keyring
    else:
        raise OSError("No secure credential backend for this platform")
    backend = Keyring()
    if backend.priority <= 0:
        raise OSError("System keychain is unavailable")
    return backend


def protect(value):
    if sys.platform == "win32":
        return "dpapi:" + _dpapi(value)
    reference = uuid.uuid4().hex
    try:
        secure_backend().set_password(SERVICE, reference, value)
    except Exception:
        raise OSError("System keychain is unavailable; secret was not saved") from None
    return "keyring:" + reference


def unprotect(value):
    if value.startswith("keyring:"):
        try:
            secret = secure_backend().get_password(SERVICE, value[8:])
        except Exception:
            raise OSError("System keychain could not unlock this secret") from None
        if secret is None:
            raise OSError("Secret is missing from the system keychain; save it again")
        return secret
    # Supports a user's existing Windows installation during local migration.
    return _dpapi(value[6:] if value.startswith("dpapi:") else value, True)


def forget(value):
    if not value or not value.startswith("keyring:"):
        return
    try:
        secure_backend().delete_password(SERVICE, value[8:])
    except Exception:
        # A stale keychain entry is preferable to losing an already-saved config.
        pass


def crypt(value, decrypt=False):
    """Compatibility shim; only ciphertext or a keychain reference is returned."""
    return unprotect(value) if decrypt else protect(value)
