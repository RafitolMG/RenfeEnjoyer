import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

# Must be set before importing the app: config reads them at import time.
KEY = Ed25519PrivateKey.generate()
_ROOT = Path(tempfile.mkdtemp(prefix="activation_test_"))
os.environ["ACTIVATION_DB_PATH"] = str(_ROOT / "test.db")
os.environ["ADMIN_PASSWORD"] = "correct horse"
# Tests never download the GeoIP database.
os.environ["GEOIP_ENABLED"] = "0"

from app.signing import b64encode  # noqa: E402

os.environ["ACTIVATION_PRIVATE_KEY"] = b64encode(
    KEY.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app import auth, main  # noqa: E402
from app.db import engine  # noqa: E402

INITIAL_PASSWORD = "correct horse"


@pytest.fixture
def client() -> Iterator[TestClient]:
    SQLModel.metadata.drop_all(engine)
    auth._FAILURES.clear()
    with TestClient(main.app) as test_client:
        yield test_client


def log_in(client: TestClient, password: str = INITIAL_PASSWORD) -> None:
    response = client.post("/api/v1/admin/login", json={"password": password})
    assert response.status_code == 204, response.text


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """The same client, signed in as the admin."""
    log_in(client)
    return client
