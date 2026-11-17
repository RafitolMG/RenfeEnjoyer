import stat
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, text

from app import credentials
from app.bot.events import JobState
from app.db.models import User
from tests.conftest import FUTURE_DATE


@pytest.fixture
def fresh_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A key of its own, so a test can lose it without touching the shared one."""
    path = tmp_path / "credentials.key"
    monkeypatch.setattr(credentials, "KEY_PATH", path)
    credentials.reset_cipher()
    yield path
    credentials.reset_cipher()


def _stored_password(session: Session) -> str:
    return session.connection().execute(text("SELECT password FROM users")).scalar_one()


def test_the_database_never_holds_the_plain_password(
    session: Session, profile: dict
) -> None:
    stored = _stored_password(session)
    assert stored.startswith(credentials.PREFIX)
    assert "secreto" not in stored


def test_the_bot_gets_the_plain_password(
    client: TestClient, profile: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[str] = []
    done = threading.Event()

    def capture(request, reporter, interaction) -> None:
        received.append(request.password)
        done.set()

    monkeypatch.setattr("app.bot.runner.run_search", capture)
    response = client.post(
        "/api/jobs/current",
        json={"user_id": profile["id"], "journey_type": "ida", "date": FUTURE_DATE},
    )
    assert response.status_code == 202
    assert done.wait(timeout=5)
    assert received == ["secreto"]


def test_passwords_saved_before_encryption_are_encrypted_at_startup(
    session: Session,
) -> None:
    session.connection().execute(
        text(
            "INSERT INTO users (username, email, password, abono) "
            "VALUES ('viejo', 'v@example.com', 'en claro', 'A')"
        )
    )
    assert credentials.encrypt_plaintext(session.connection()) == 1
    assert _stored_password(session).startswith(credentials.PREFIX)
    session.expire_all()
    assert session.get(User, 1).password == "en claro"
    # Already encrypted rows are left alone on the next start.
    assert credentials.encrypt_plaintext(session.connection()) == 0


def test_the_key_survives_a_restart(fresh_key: Path) -> None:
    stored = credentials.encrypt("secreto")
    credentials.reset_cipher()
    assert credentials.decrypt(stored) == "secreto"


def test_a_password_under_a_lost_key_must_be_typed_again(
    client: TestClient,
    profile: dict,
    fresh_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The profile saved under the shared key; this computer now has another one."""
    assert client.get("/api/users").status_code == 200

    response = client.post(
        "/api/jobs/current",
        json={"user_id": profile["id"], "journey_type": "ida", "date": FUTURE_DATE},
    )
    assert response.status_code == 409
    assert "vuelve a escribirla" in response.json()["detail"]

    client.patch(f"/api/users/{profile['id']}", json={"password": "nueva"})
    started = threading.Event()

    def blocking(request, reporter, interaction) -> None:
        reporter.state(JobState.POLLING, "Buscando plazas")
        started.set()

    monkeypatch.setattr("app.bot.runner.run_search", blocking)
    response = client.post(
        "/api/jobs/current",
        json={"user_id": profile["id"], "journey_type": "ida", "date": FUTURE_DATE},
    )
    assert response.status_code == 202
    assert started.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_only_the_owner_can_read_the_key(fresh_key: Path) -> None:
    credentials.encrypt("x")
    assert stat.S_IMODE(fresh_key.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI only exists on Windows")
def test_the_key_is_sealed_with_dpapi_on_windows(fresh_key: Path) -> None:
    stored = credentials.encrypt("secreto")
    sealed = fresh_key.read_bytes()
    with pytest.raises(ValueError):
        Fernet(sealed)
    credentials.reset_cipher()
    assert credentials.decrypt(stored) == "secreto"
