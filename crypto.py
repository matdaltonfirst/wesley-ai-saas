"""Encryption for stored secrets (OAuth tokens).

Fernet (AES-128-CBC with HMAC) with a key derived from ``TOKEN_ENCRYPTION_KEY``.
If that is unset the app's ``SECRET_KEY`` is used instead, which works but ties
the stored tokens to it: rotating SECRET_KEY then orphans every connection and
people must reconnect. Set a dedicated TOKEN_ENCRYPTION_KEY and keep it stable.
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:"


class CryptoError(Exception):
    """A stored secret could not be decrypted (wrong or rotated key)."""


def _fernet() -> Fernet:
    material = os.getenv("TOKEN_ENCRYPTION_KEY") or os.getenv("SECRET_KEY") or ""
    if not material:
        raise CryptoError("No TOKEN_ENCRYPTION_KEY or SECRET_KEY is set, so tokens cannot be stored.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material.encode("utf-8")).digest()))


def encrypt(value: str) -> str:
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    """Return the plaintext. Values without the prefix are returned unchanged."""
    if not value or not value.startswith(PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError("The stored sign-in could not be read (the encryption key changed). "
                          "Reconnect the integration.") from exc
