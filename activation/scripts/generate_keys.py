"""Generate the Ed25519 key pair for the activation server.

    python scripts/generate_keys.py

The private key goes into the server's ACTIVATION_PRIVATE_KEY secret and nowhere else;
the public key goes into backend/app/activation.py, which is compiled into the .exe.
Changing the pair invalidates every license already issued: each computer then simply
asks the server again on its next start.
"""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from app.signing import b64encode, public_key_of

key = Ed25519PrivateKey.generate()
private = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
print(f"ACTIVATION_PRIVATE_KEY={b64encode(private)}")
print(f"PUBLIC_KEY={public_key_of(key)}")
