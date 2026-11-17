"""Encryption at rest for the Renfe passwords saved in profiles.

They cannot be hashed: the bot has to type them into Renfe's login. They are encrypted
with a key kept apart from the database instead, so the `.db` file alone gives nothing
away.

On Windows the key file is sealed with DPAPI, which ties it to the Windows account that
created it: another account, or another computer the data folder is copied to, cannot
open it. Elsewhere it is a plain key file readable only by its owner (0600). Neither
protects against code running as the user, which can always ask for the key.

A password that can no longer be decrypted (key lost, data folder moved to another
Windows account) reads as an empty string, so the app keeps working and only that
profile asks for its password again.
"""

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.engine import Connection
from sqlalchemy.types import TypeDecorator
from sqlmodel import text

from app.config import KEY_PATH

logger = logging.getLogger(__name__)

# Marks a stored value as encrypted; anything without it is a pre-encryption plaintext.
PREFIX = "enc:v1:"

_lock = threading.Lock()
_fernet: Fernet | None = None


class CredentialKeyError(Exception):
    """Raised when the key file exists but cannot be opened."""


# --- the key ---------------------------------------------------------------------------


def _seal(key: bytes) -> bytes:
    if sys.platform == "win32":
        return _dpapi(key, protect=True)
    return key


def _unseal(stored: bytes) -> bytes:
    if sys.platform == "win32":
        return _dpapi(stored, protect=False)
    return stored


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """CryptProtectData / CryptUnprotectData for the current Windows user."""
    if sys.platform != "win32":
        raise NotImplementedError("DPAPI only exists on Windows")

    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = (
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        )

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    ui_forbidden = 0x1

    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not call(
        ctypes.byref(source), None, None, None, None, ui_forbidden, ctypes.byref(result)
    ):
        raise CredentialKeyError(ctypes.FormatError(ctypes.get_last_error()))
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def _create_key(path: Path) -> bytes | None:
    """Write a new key, or return None if another process got there first."""
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # O_EXCL: two processes starting together must not each write their own key.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return None
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(_seal(key))
    return key


def _load_key(path: Path) -> bytes:
    if not path.exists():
        created = _create_key(path)
        if created is not None:
            return created
    try:
        key = _unseal(path.read_bytes())
        Fernet(key)
    except (OSError, ValueError, CredentialKeyError) as exc:
        raise CredentialKeyError(
            f"No se puede abrir la clave de cifrado ({path}): {exc}"
        ) from exc
    return key


def _cipher() -> Fernet:
    global _fernet
    with _lock:
        if _fernet is None:
            _fernet = Fernet(_load_key(KEY_PATH))
        return _fernet


def reset_cipher() -> None:
    """Forget the loaded key, so the next use reads the key file again (tests)."""
    global _fernet
    with _lock:
        _fernet = None


# --- values ----------------------------------------------------------------------------


def encrypt(value: str) -> str:
    return PREFIX + _cipher().encrypt(value.encode()).decode()


def decrypt(stored: str) -> str:
    if not stored.startswith(PREFIX):
        # Saved before encryption existed; `encrypt_plaintext` converts it at startup.
        return stored
    try:
        return _cipher().decrypt(stored[len(PREFIX) :].encode()).decode()
    except (InvalidToken, CredentialKeyError) as exc:
        logger.warning("A stored password cannot be decrypted: %s", exc or "wrong key")
        return ""


class EncryptedString(TypeDecorator[str]):
    """A text column stored encrypted and read back in plain text."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        return None if value is None else encrypt(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        return None if value is None else decrypt(value)


def encrypt_plaintext(connection: Connection) -> int:
    """Encrypt the passwords saved before encryption existed. Returns how many."""
    rows = connection.execute(text("SELECT id, password FROM users")).all()
    pending = [
        {"id": row_id, "password": encrypt(password)}
        for row_id, password in rows
        if password is not None and not password.startswith(PREFIX)
    ]
    if pending:
        connection.execute(
            text("UPDATE users SET password = :password WHERE id = :id"), pending
        )
    return len(pending)
