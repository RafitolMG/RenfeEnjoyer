from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import auth
from app.db import engine
from app.models import AdminSession
from app.reset_admin import main as reset_admin
from tests.conftest import INITIAL_PASSWORD, log_in

NEW_PASSWORD = "otra contraseña larga"


def change(client: TestClient, current: str, new: str) -> int:
    return client.post(
        "/api/v1/admin/password",
        json={"current_password": current, "new_password": new},
    ).status_code


def login_status(client: TestClient, password: str) -> int:
    return client.post("/api/v1/admin/login", json={"password": password}).status_code


# --- login ----------------------------------------------------------------------------


def test_the_admin_page_sends_strangers_to_the_login(client: TestClient) -> None:
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    assert "Contraseña" in client.get("/admin/login").text


def test_the_admin_api_needs_a_session(client: TestClient) -> None:
    assert client.get("/api/v1/admin/machines").status_code == 401
    assert client.post("/api/v1/admin/machines/1/approve").status_code == 401


def test_a_wrong_password_is_refused(client: TestClient) -> None:
    assert login_status(client, "wrong") == 401
    assert client.get("/api/v1/admin/machines").status_code == 401


def test_logging_in_opens_the_admin_side(client: TestClient) -> None:
    log_in(client)
    assert client.get("/api/v1/admin/machines").status_code == 200
    assert client.get("/admin", follow_redirects=False).status_code == 200
    # Already signed in: the login page goes straight to the admin page.
    assert client.get("/admin/login", follow_redirects=False).status_code == 303


def test_the_session_cookie_is_locked_down(client: TestClient) -> None:
    response = client.post("/api/v1/admin/login", json={"password": INITIAL_PASSWORD})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie


def test_only_a_hash_of_the_session_token_is_stored(admin: TestClient) -> None:
    token = admin.cookies[auth.SESSION_COOKIE]
    with Session(engine) as session:
        stored = session.exec(select(AdminSession)).one()
    assert stored.token_hash != token


def test_an_expired_session_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "SESSION_LIFETIME", timedelta(seconds=-1))
    log_in(client)
    assert client.get("/api/v1/admin/machines").status_code == 401


def test_logging_out_ends_the_session(admin: TestClient) -> None:
    token = admin.cookies[auth.SESSION_COOKIE]
    assert admin.post("/api/v1/admin/logout").status_code == 204
    # Even a copy of the old cookie no longer works.
    admin.cookies.set(auth.SESSION_COOKIE, token)
    assert admin.get("/api/v1/admin/machines").status_code == 401


def test_guessing_the_password_is_throttled(client: TestClient) -> None:
    for _ in range(auth.MAX_FAILURES):
        assert login_status(client, "guess") == 401
    # Locked out even with the right password until the window passes.
    assert login_status(client, INITIAL_PASSWORD) == 429


# --- password change ------------------------------------------------------------------


def test_a_fresh_server_reports_the_initial_password(admin: TestClient) -> None:
    assert admin.get("/api/v1/admin/me").json()["using_initial_password"] is True


def test_changing_the_password_replaces_the_initial_one(admin: TestClient) -> None:
    assert change(admin, INITIAL_PASSWORD, NEW_PASSWORD) == 204
    assert admin.get("/api/v1/admin/me").json()["using_initial_password"] is False

    admin.post("/api/v1/admin/logout")
    assert login_status(admin, INITIAL_PASSWORD) == 401
    assert login_status(admin, NEW_PASSWORD) == 204


def test_a_changed_password_can_be_changed_again(admin: TestClient) -> None:
    change(admin, INITIAL_PASSWORD, NEW_PASSWORD)
    assert change(admin, NEW_PASSWORD, "una tercera contraseña") == 204
    admin.post("/api/v1/admin/logout")
    assert login_status(admin, "una tercera contraseña") == 204


def test_the_current_password_is_required(admin: TestClient) -> None:
    assert change(admin, "not it", NEW_PASSWORD) == 400
    admin.post("/api/v1/admin/logout")
    assert login_status(admin, INITIAL_PASSWORD) == 204


@pytest.mark.parametrize("new", ["corta", INITIAL_PASSWORD])
def test_the_new_password_must_be_long_and_different(admin: TestClient, new: str) -> None:
    assert change(admin, INITIAL_PASSWORD, new) in {400, 422}


def test_changing_the_password_signs_out_the_other_sessions(client: TestClient) -> None:
    log_in(client)
    other = client.cookies[auth.SESSION_COOKIE]
    client.cookies.clear()
    log_in(client)

    assert change(client, INITIAL_PASSWORD, NEW_PASSWORD) == 204
    assert client.get("/api/v1/admin/machines").status_code == 200
    client.cookies.set(auth.SESSION_COOKIE, other)
    assert client.get("/api/v1/admin/machines").status_code == 401


def test_the_stored_password_is_a_salted_hash() -> None:
    first, second = auth.hash_password(NEW_PASSWORD), auth.hash_password(NEW_PASSWORD)
    assert NEW_PASSWORD not in first
    assert first != second
    assert auth.verify_password(NEW_PASSWORD, first)
    assert not auth.verify_password("otra", first)


def test_a_forgotten_password_can_be_reset_to_the_initial_one(admin: TestClient) -> None:
    change(admin, INITIAL_PASSWORD, NEW_PASSWORD)
    reset_admin()
    # The reset also signed out the session that was open.
    assert admin.get("/api/v1/admin/machines").status_code == 401
    assert login_status(admin, NEW_PASSWORD) == 401
    assert login_status(admin, INITIAL_PASSWORD) == 204
