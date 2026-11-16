"""Ed25519 licenses: the server signs, the desktop app only ever verifies.

A license is the JSON payload and its signature, both base64url. The app ships only the
public key, so it can tell a real approval from a forged one but cannot make one.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def load_private_key(encoded: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(b64decode(encoded.strip()))


def public_key_of(key: Ed25519PrivateKey) -> str:
    return b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def issue_license(
    key: Ed25519PrivateKey, machine_id: str, days: int, now: datetime | None = None
) -> dict[str, Any]:
    issued = now or datetime.now(UTC)
    payload = json.dumps(
        {
            "v": 1,
            "machine_id": machine_id,
            "issued_at": issued.isoformat(),
            "expires_at": (issued + timedelta(days=days)).isoformat(),
        },
        separators=(",", ":"),
    ).encode()
    return {"payload": b64encode(payload), "signature": b64encode(key.sign(payload))}
