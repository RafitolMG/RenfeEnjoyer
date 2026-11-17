import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from app import activation
from app.activation import ActivationError, Activator, get_activator
from app.main import app
from tests.conftest import FUTURE_DATE

MACHINE = "c" * 64
SERVER_KEY = Ed25519PrivateKey.generate()


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def public_key(key: Ed25519PrivateKey) -> str:
    return b64(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def signed_license(
    machine: str = MACHINE,
    days: float = 7,
    key: Ed25519PrivateKey = SERVER_KEY,
) -> dict[str, str]:
    now = datetime.now(UTC)
    payload = json.dumps(
        {
            "v": 1,
            "machine_id": machine,
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=days)).isoformat(),
        }
    ).encode()
    return {"payload": b64(payload), "signature": b64(key.sign(payload))}


class FakeServer:
    """Answers like the activation server, or fails like an unreachable one."""

    def __init__(self) -> None:
        self.answer: dict[str, Any] | None = {"status": "unknown", "license": None}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, body))
        if self.answer is None:
            raise ActivationError("No se pudo contactar con el servidor de activación")
        return self.answer

    def approves(self, **license_args: Any) -> None:
        self.answer = {"status": "approved", "license": signed_license(**license_args)}


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def make_activator(
    tmp_path: Path, server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Activator]:
    def make(required: bool = True) -> Activator:
        activator = Activator(
            required=required,
            server_url="https://activation.invalid",
            public_key=public_key(SERVER_KEY),
            license_path=tmp_path / "license.json",
            machine=MACHINE,
        )
        monkeypatch.setattr(activator, "_post", server)
        return activator

    return make


def test_a_checkout_needs_no_activation(
    make_activator: Callable[..., Activator], server: FakeServer
) -> None:
    activator = make_activator(required=False)
    assert activator.is_authorized()
    assert activator.state()["authorized"]
    assert server.calls == []


def test_an_unknown_computer_is_not_authorized(
    make_activator: Callable[..., Activator],
) -> None:
    activator = make_activator()
    assert not activator.is_authorized()
    assert activator.state()["status"] == "unknown"


def test_an_approval_is_stored_and_authorizes(
    make_activator: Callable[..., Activator], server: FakeServer
) -> None:
    server.approves()
    activator = make_activator()
    assert activator.is_authorized()
    assert activator.state()["expires_at"] is not None
    assert activator.license_path.is_file()


def test_requests_carry_the_details_and_this_computers_id(
    make_activator: Callable[..., Activator], server: FakeServer
) -> None:
    server.answer = {"status": "pending", "license": None}
    activator = make_activator()
    activator.request_access("Ana", "ana@example.com")
    path, body = server.calls[-1]
    assert path == "/api/v1/requests"
    assert (body["machine_id"], body["name"], body["contact"]) == (
        MACHINE,
        "Ana",
        "ana@example.com",
    )
    assert activator.state()["status"] == "pending"


def test_an_approved_computer_keeps_working_while_the_server_is_down(
    make_activator: Callable[..., Activator], server: FakeServer
) -> None:
    server.approves()
    make_activator().refresh()

    server.answer = None
    activator = make_activator()
    assert activator.is_authorized()
    assert activator.state()["error"]


def test_without_a_license_an_unreachable_server_means_no_access(
    make_activator: Callable[..., Activator], server: FakeServer
) -> None:
    server.answer = None
    assert not make_activator().is_authorized()


def test_a_revocation_drops_the_stored_license(
    make_activator: Callable[..., Activator], server: FakeServer
) -> None:
    server.approves()
    activator = make_activator()
    assert activator.is_authorized()

    server.answer = {"status": "revoked", "license": None}
    activator.refresh(force=True)
    assert not activator.is_authorized()
    assert not activator.license_path.exists()


def test_an_expired_license_does_not_authorize_offline(
    make_activator: Callable[..., Activator], server: FakeServer
) -> None:
    activator = make_activator()
    activator.license_path.write_text(json.dumps(signed_license(days=-1)))
    server.answer = None
    assert not activator.is_authorized()


@pytest.mark.parametrize(
    "license_",
    [
        signed_license(key=Ed25519PrivateKey.generate()),
        signed_license(machine="d" * 64),
        {"payload": signed_license()["payload"], "signature": "AAAA"},
    ],
    ids=["other key", "other computer", "bad signature"],
)
def test_a_license_that_is_not_genuine_is_refused(
    make_activator: Callable[..., Activator],
    server: FakeServer,
    license_: dict[str, str],
) -> None:
    server.answer = {"status": "approved", "license": license_}
    activator = make_activator()
    assert not activator.is_authorized()
    assert activator.state()["error"]


def test_a_copied_license_file_does_not_work_elsewhere(
    make_activator: Callable[..., Activator], server: FakeServer
) -> None:
    activator = make_activator()
    activator.license_path.write_text(json.dumps(signed_license(machine="e" * 64)))
    server.answer = None
    assert not activator.is_authorized()


def test_the_server_is_asked_again_only_once_its_answer_is_stale(
    make_activator: Callable[..., Activator],
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server.approves()
    activator = make_activator()
    activator.is_authorized()
    activator.is_authorized()
    assert len(server.calls) == 1

    monkeypatch.setattr(activation, "RECHECK_AFTER", timedelta(0))
    activator.is_authorized()
    assert len(server.calls) == 2


def test_the_machine_id_is_a_hash() -> None:
    machine = activation.machine_id()
    assert len(machine) == 64
    assert int(machine, 16) >= 0
    assert activation.raw_machine_id() not in machine


def test_a_search_needs_an_activated_computer(
    client: TestClient,
    profile: dict,
    make_activator: Callable[..., Activator],
) -> None:
    app.dependency_overrides[get_activator] = make_activator
    response = client.post(
        "/api/jobs/current",
        json={"user_id": profile["id"], "journey_type": "ida", "date": FUTURE_DATE},
    )
    assert response.status_code == 403
    assert client.get("/api/activation").json()["authorized"] is False


def signed_update(key: Ed25519PrivateKey = SERVER_KEY, **changes: Any) -> dict[str, str]:
    claims = {
        "kind": "update",
        "version": "2.1.0",
        "url": "https://github.com/o/r/releases/download/v2.1.0/RenfeEnjoyer.exe",
        "sha256": "AB" * 32,
        "size": 123,
        "min_version": None,
        **changes,
    }
    payload = json.dumps(claims).encode()
    return {"payload": b64(payload), "signature": b64(key.sign(payload))}


def test_a_signed_update_offer_is_handed_on(
    make_activator: Callable[..., Activator], server: FakeServer
) -> None:
    server.approves()
    server.answer["update"] = signed_update(min_version="2.0.0")
    activator = make_activator()
    offers: list = []
    activator.on_update = offers.append
    activator.refresh(force=True)
    assert offers == [
        activation.UpdateOffer(
            version="2.1.0",
            url="https://github.com/o/r/releases/download/v2.1.0/RenfeEnjoyer.exe",
            sha256="ab" * 32,
            size=123,
            min_version="2.0.0",
        )
    ]


@pytest.mark.parametrize(
    "offer",
    [
        signed_update(key=Ed25519PrivateKey.generate()),
        signed_update(kind="license"),
        # A license is signed by the same key but must never pass for an update.
        signed_license(),
    ],
)
def test_an_offer_the_server_did_not_sign_as_an_update_is_ignored(
    make_activator: Callable[..., Activator], server: FakeServer, offer: dict
) -> None:
    server.approves()
    server.answer["update"] = offer
    activator = make_activator()
    offers: list = []
    activator.on_update = offers.append
    activator.refresh(force=True)
    assert offers == [None]
    # The license that came with it still counts.
    assert activator.is_authorized()


def test_checks_and_requests_describe_the_computer(
    make_activator: Callable[..., Activator], server: FakeServer
) -> None:
    activator = make_activator()
    activator.refresh(force=True)
    activator.request_access("Ana", "ana@example.com")
    for _, body in server.calls:
        device = body["device"]
        assert device["hostname"] and device["os"] and device["timezone"]
        assert all(len(value) <= activation.DETAIL_LIMIT for value in device.values())
