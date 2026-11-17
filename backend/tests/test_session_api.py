import shutil
import threading

import pytest
from fastapi.testclient import TestClient

from app.bot.driver import mark_session_verified, profile_dir_for
from app.bot.events import JobState
from tests.conftest import FUTURE_DATE

ACCOUNT = "demo@example.com"


@pytest.fixture
def browsed_profile() -> None:
    """A profile Chrome has opened and filled with cookies, without ever logging in."""
    directory = profile_dir_for(ACCOUNT)
    (directory / "Default").mkdir(parents=True, exist_ok=True)
    (directory / "Default" / "Cookies").write_text("stub")


@pytest.fixture
def stored_profile(browsed_profile: None) -> None:
    mark_session_verified(profile_dir_for(ACCOUNT))


def test_profiles_never_share_a_browser_session() -> None:
    """A shared profile would let the bot search on whichever account logged in last."""
    assert profile_dir_for("uno@example.com") != profile_dir_for("dos@example.com")
    assert profile_dir_for("Uno@Example.com ") == profile_dir_for("uno@example.com")


def test_reports_when_nothing_is_stored(client: TestClient, profile: dict) -> None:
    shutil.rmtree(profile_dir_for(ACCOUNT), ignore_errors=True)
    response = client.get(f"/api/users/{profile['id']}/session")
    assert response.json() == {"stored": False, "verified_at": None}


def test_cookies_from_an_anonymous_visit_are_not_a_session(
    client: TestClient, profile: dict, browsed_profile: None
) -> None:
    """A search that stalled on the captcha leaves cookies behind but no login."""
    response = client.get(f"/api/users/{profile['id']}/session")
    assert response.json() == {"stored": False, "verified_at": None}


def test_reports_and_clears_a_stored_session(
    client: TestClient, profile: dict, stored_profile: None
) -> None:
    reported = client.get(f"/api/users/{profile['id']}/session").json()
    assert reported["stored"] is True
    assert reported["verified_at"] is not None
    assert client.delete(f"/api/users/{profile['id']}/session").status_code == 204
    assert not profile_dir_for(ACCOUNT).exists()


def test_unknown_profile_returns_404(client: TestClient) -> None:
    assert client.get("/api/users/999/session").status_code == 404
    assert client.delete("/api/users/999/session").status_code == 404


def test_session_is_not_cleared_while_a_search_runs(
    client: TestClient,
    profile: dict,
    stored_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The running browser holds the profile open, so clearing it would break the job."""
    started = threading.Event()

    def blocking_run_search(request, reporter, interaction) -> None:
        reporter.state(JobState.POLLING, "Buscando plazas")
        started.set()
        interaction.release.wait(timeout=5)

    monkeypatch.setattr("app.bot.runner.run_search", blocking_run_search)
    client.post(
        "/api/jobs/current",
        json={
            "user_id": profile["id"],
            "departure_time": "07:30",
            "journey_type": "ida",
            "date": FUTURE_DATE,
        },
    )
    assert started.wait(timeout=5)

    assert client.delete(f"/api/users/{profile['id']}/session").status_code == 409
    assert profile_dir_for(ACCOUNT).exists()

    client.post("/api/jobs/current/stop")


def _create(client: TestClient, username: str, email: str) -> dict:
    response = client.post(
        "/api/users",
        json={"username": username, "email": email, "password": "x", "abono": "A"},
    )
    assert response.status_code == 201
    return response.json()


def test_deleting_a_profile_drops_its_browser_session(
    client: TestClient, profile: dict, stored_profile: None
) -> None:
    assert client.delete(f"/api/users/{profile['id']}").status_code == 204
    assert not profile_dir_for(ACCOUNT).exists()


def test_a_session_shared_with_another_profile_is_kept(
    client: TestClient, profile: dict, stored_profile: None
) -> None:
    """One Renfe account can hold several passes, each saved as its own profile."""
    _create(client, "otro abono", " Demo@Example.com")
    assert client.delete(f"/api/users/{profile['id']}").status_code == 204
    assert profile_dir_for(ACCOUNT).exists()


def test_changing_the_email_drops_the_old_session(
    client: TestClient, profile: dict, stored_profile: None
) -> None:
    response = client.patch(
        f"/api/users/{profile['id']}", json={"email": "nuevo@example.com"}
    )
    assert response.status_code == 200
    assert not profile_dir_for(ACCOUNT).exists()


def test_profiles_are_not_deleted_while_a_search_runs(
    client: TestClient,
    profile: dict,
    stored_profile: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()

    def blocking_run_search(request, reporter, interaction) -> None:
        reporter.state(JobState.POLLING, "Buscando plazas")
        started.set()
        interaction.release.wait(timeout=5)

    monkeypatch.setattr("app.bot.runner.run_search", blocking_run_search)
    client.post(
        "/api/jobs/current",
        json={"user_id": profile["id"], "journey_type": "ida", "date": FUTURE_DATE},
    )
    assert started.wait(timeout=5)

    assert client.delete(f"/api/users/{profile['id']}").status_code == 409
    moved = client.patch(f"/api/users/{profile['id']}", json={"email": "n@example.com"})
    assert moved.status_code == 409
    renamed = client.patch(f"/api/users/{profile['id']}", json={"username": "otro"})
    assert renamed.status_code == 200
    assert profile_dir_for(ACCOUNT).exists()

    client.post("/api/jobs/current/stop")
