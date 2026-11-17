import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import releases
from app.signing import b64decode
from tests.conftest import KEY
from tests.test_activation import MACHINE, ask, check, decide

EXE = "RenfeEnjoyer.exe"


def gh_release(
    tag: str, digest: str | None = "sha256:" + "ab" * 32, **extra: Any
) -> dict:
    asset = {
        "name": EXE,
        "size": 31_000_000,
        "browser_download_url": f"https://github.com/o/r/releases/download/{tag}/{EXE}",
    }
    if digest is not None:
        asset["digest"] = digest
    return {"tag_name": tag, "assets": [asset], "body": "notas", **extra}


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """GitHub's release list, editable by each test."""
    listed = [gh_release("v2.0.2"), gh_release("v2.0.3")]
    monkeypatch.setattr(releases, "_github", lambda path: listed)
    return listed


def update_claims(answer: dict) -> dict | None:
    update = answer["update"]
    if update is None:
        return None
    payload = b64decode(update["payload"])
    KEY.public_key().verify(b64decode(update["signature"]), payload)
    return json.loads(payload)


def publish(admin: TestClient, version: str) -> None:
    response = admin.post("/api/v1/admin/releases", json={"version": version})
    assert response.status_code == 201, response.text


def test_nothing_is_offered_until_the_admin_publishes(
    admin: TestClient, github: list
) -> None:
    ask(admin)
    assert check(admin)["update"] is None
    listed = admin.get("/api/v1/admin/releases").json()
    assert [r["version"] for r in listed["github"]] == ["2.0.3", "2.0.2"]
    assert listed["published"] == []


def test_the_newest_published_version_is_offered_signed(
    admin: TestClient, github: list
) -> None:
    ask(admin)
    publish(admin, "2.0.2")
    publish(admin, "2.0.3")
    claims = update_claims(check(admin))
    assert claims == {
        "kind": "update",
        "version": "2.0.3",
        "url": f"https://github.com/o/r/releases/download/v2.0.3/{EXE}",
        "sha256": "ab" * 32,
        "size": 31_000_000,
        "min_version": None,
    }


def test_publishing_pins_the_digest_github_reports_then(
    admin: TestClient, github: list
) -> None:
    publish(admin, "2.0.3")
    github[1] = gh_release("v2.0.3", digest="sha256:" + "cd" * 32)
    assert update_claims(check(admin))["sha256"] == "ab" * 32


def test_versions_order_by_number_not_text(admin: TestClient, github: list) -> None:
    github.append(gh_release("v2.0.10"))
    publish(admin, "2.0.3")
    publish(admin, "2.0.10")
    assert update_claims(check(admin))["version"] == "2.0.10"


@pytest.mark.parametrize(
    "release",
    [
        gh_release("v2.1.0", draft=True),
        gh_release("v2.1.0", prerelease=True),
        gh_release("v2.1.0", digest=None),
        gh_release("nightly"),
    ],
)
def test_releases_that_cannot_be_offered_are_not_listed(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch, release: dict
) -> None:
    monkeypatch.setattr(releases, "_github", lambda path: [release])
    assert admin.get("/api/v1/admin/releases").json()["github"] == []
    response = admin.post("/api/v1/admin/releases", json={"version": "2.1.0"})
    assert response.status_code == 404


def test_publishing_twice_conflicts(admin: TestClient, github: list) -> None:
    publish(admin, "2.0.3")
    response = admin.post("/api/v1/admin/releases", json={"version": "2.0.3"})
    assert response.status_code == 409


def test_withdrawing_offers_the_previous_one(admin: TestClient, github: list) -> None:
    publish(admin, "2.0.2")
    publish(admin, "2.0.3")
    assert admin.delete("/api/v1/admin/releases/2.0.3").status_code == 204
    assert update_claims(check(admin))["version"] == "2.0.2"


def test_min_version_must_be_published_and_travels_signed(
    admin: TestClient, github: list
) -> None:
    put = lambda v: admin.put("/api/v1/admin/min-version", json={"min_version": v})  # noqa: E731
    assert put("2.0.3").status_code == 422
    publish(admin, "2.0.3")
    assert put("2.0.3").status_code == 200
    assert update_claims(check(admin))["min_version"] == "2.0.3"
    # The minimum cannot be withdrawn from under the apps.
    assert admin.delete("/api/v1/admin/releases/2.0.3").status_code == 409
    assert put(None).status_code == 200


def test_revoked_computers_get_no_update(admin: TestClient, github: list) -> None:
    ask(admin)
    publish(admin, "2.0.3")
    decide(admin, "revoke", MACHINE)
    assert check(admin)["update"] is None


def test_github_down_does_not_break_the_admin(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(path: str) -> list:
        raise releases.GitHubError("No se pudo consultar GitHub")

    monkeypatch.setattr(releases, "_github", down)
    listed = admin.get("/api/v1/admin/releases").json()
    assert listed["github_error"] == "No se pudo consultar GitHub"
    response = admin.post("/api/v1/admin/releases", json={"version": "2.0.3"})
    assert response.status_code == 502


def test_release_admin_needs_a_session(client: TestClient) -> None:
    assert client.get("/api/v1/admin/releases").status_code == 401
    assert (
        client.post("/api/v1/admin/releases", json={"version": "2.0.3"}).status_code
        == 401
    )
    put = client.put("/api/v1/admin/min-version", json={"min_version": None})
    assert put.status_code == 401
