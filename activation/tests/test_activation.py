import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.signing import b64decode
from tests.conftest import KEY

MACHINE = "a" * 64
OTHER = "b" * 64


def ask(client: TestClient, machine: str = MACHINE, **extra: str) -> dict:
    body = {"machine_id": machine, "name": "Ana", "contact": "ana@example.com", **extra}
    response = client.post("/api/v1/requests", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def check(client: TestClient, machine: str = MACHINE) -> dict:
    response = client.post("/api/v1/check", json={"machine_id": machine})
    assert response.status_code == 200, response.text
    return response.json()


def machine_db_id(client: TestClient, machine: str = MACHINE) -> int:
    rows = client.get("/api/v1/admin/machines").json()
    return next(row["id"] for row in rows if row["machine_id"] == machine)


def decide(client: TestClient, action: str, machine: str = MACHINE) -> None:
    db_id = machine_db_id(client, machine)
    response = client.post(f"/api/v1/admin/machines/{db_id}/{action}")
    assert response.status_code == 200, response.text


def test_an_unknown_computer_gets_no_license(client: TestClient) -> None:
    assert check(client) == {"status": "unknown", "license": None, "update": None}


def test_a_request_waits_for_the_admin(client: TestClient) -> None:
    assert ask(client) == {"status": "pending", "license": None, "update": None}
    assert check(client)["status"] == "pending"


def test_an_approved_computer_gets_a_license_signed_for_it(admin: TestClient) -> None:
    client = admin
    ask(client)
    decide(client, "approve")

    answer = check(client)
    assert answer["status"] == "approved"
    payload = b64decode(answer["license"]["payload"])
    KEY.public_key().verify(b64decode(answer["license"]["signature"]), payload)
    claims = json.loads(payload)
    assert claims["machine_id"] == MACHINE
    expires = datetime.fromisoformat(claims["expires_at"])
    assert timedelta(days=6) < expires - datetime.now(UTC) <= timedelta(days=7)


def test_approving_one_computer_does_not_approve_another(admin: TestClient) -> None:
    client = admin
    ask(client)
    ask(client, OTHER)
    decide(client, "approve")
    assert check(client, OTHER) == {"status": "pending", "license": None, "update": None}


def test_a_revoked_computer_loses_its_license(admin: TestClient) -> None:
    client = admin
    ask(client)
    decide(client, "approve")
    decide(client, "revoke")
    assert check(client) == {"status": "revoked", "license": None, "update": None}


def test_asking_again_cannot_undo_a_revocation(admin: TestClient) -> None:
    client = admin
    ask(client)
    decide(client, "revoke")
    assert ask(client, name="Otra")["status"] == "revoked"


def test_asking_again_while_pending_updates_the_details(admin: TestClient) -> None:
    client = admin
    ask(client)
    ask(client, name="Ana García", contact="+34 600 000 000")
    row = client.get("/api/v1/admin/machines").json()[0]
    assert (row["name"], row["contact"]) == ("Ana García", "+34 600 000 000")


def test_a_deleted_computer_can_ask_again(admin: TestClient) -> None:
    client = admin
    ask(client)
    decide(client, "revoke")
    db_id = machine_db_id(client)
    assert client.delete(f"/api/v1/admin/machines/{db_id}").status_code == 204
    assert ask(client)["status"] == "pending"


@pytest.mark.parametrize("machine", ["short", "A" * 64, "g" * 64])
def test_machine_ids_must_be_the_apps_hash(client: TestClient, machine: str) -> None:
    response = client.post("/api/v1/check", json={"machine_id": machine})
    assert response.status_code == 422


def test_pending_requests_are_capped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.config.MAX_PENDING", 1)
    ask(client)
    response = client.post(
        "/api/v1/requests", json={"machine_id": OTHER, "name": "B", "contact": "b"}
    )
    assert response.status_code == 503


def test_the_public_key_is_published(client: TestClient) -> None:
    from app.signing import public_key_of

    assert client.get("/api/v1/public-key").json() == {"public_key": public_key_of(KEY)}


def test_the_home_page_offers_the_latest_release(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "releases/latest/download/RenfeEnjoyer.exe" in page.text
    assert 'href="/admin"' in page.text
    logo = client.get("/static/logo.webp")
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/webp"
