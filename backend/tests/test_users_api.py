import pytest
from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"


def test_create_and_list_profile(client: TestClient, profile: dict) -> None:
    listed = client.get("/api/users").json()
    assert [item["username"] for item in listed] == ["demo"]
    assert listed[0]["email"] == "demo@example.com"


def test_password_is_never_returned(client: TestClient, profile: dict) -> None:
    assert "password" not in profile
    assert "password" not in client.get("/api/users").json()[0]


def test_duplicate_username_is_rejected(client: TestClient, profile: dict) -> None:
    duplicate = client.post(
        "/api/users",
        json={
            "username": "demo",
            "email": "otro@example.com",
            "password": "x",
            "abono": "ABC123",
        },
    )
    assert duplicate.status_code == 409


def test_profile_can_be_renamed(client: TestClient, profile: dict) -> None:
    """Legacy edit_user keyed its UPDATE on the new name, so renaming never applied."""
    response = client.patch(
        f"/api/users/{profile['id']}", json={"username": "renombrado"}
    )
    assert response.status_code == 200
    assert response.json()["username"] == "renombrado"
    assert client.get("/api/users").json()[0]["username"] == "renombrado"


def test_blank_password_keeps_the_stored_one(
    client: TestClient, session, profile: dict
) -> None:
    from app.db.models import User

    client.patch(f"/api/users/{profile['id']}", json={"password": "", "abono": "NUEVO99"})

    stored = session.get(User, profile["id"])
    assert stored.password == "secreto"
    assert stored.abono == "NUEVO99"


def test_delete_profile(client: TestClient, profile: dict) -> None:
    assert client.delete(f"/api/users/{profile['id']}").status_code == 204
    assert client.get("/api/users").json() == []


def test_unknown_profile_returns_404(client: TestClient) -> None:
    assert client.patch("/api/users/999", json={"abono": "X"}).status_code == 404
    assert client.delete("/api/users/999").status_code == 404


def test_renaming_to_an_existing_name_conflicts(
    client: TestClient, profile: dict
) -> None:
    other = client.post(
        "/api/users",
        json={
            "username": "otro",
            "email": "o@example.com",
            "password": "x",
            "abono": "A",
        },
    ).json()
    response = client.patch(f"/api/users/{other['id']}", json={"username": "demo"})
    assert response.status_code == 409
    assert response.json()["detail"] == "Ya existe un perfil con ese nombre"


def test_keeping_the_same_name_is_not_a_conflict(
    client: TestClient, profile: dict
) -> None:
    response = client.patch(
        f"/api/users/{profile['id']}", json={"username": "demo", "abono": "NUEVO"}
    )
    assert response.status_code == 200


@pytest.mark.parametrize("field", ["username", "email", "password", "abono"])
def test_blank_fields_are_rejected(client: TestClient, field: str) -> None:
    payload = {"username": "n", "email": "e@example.com", "password": "p", "abono": "A"}
    payload[field] = "   "
    assert client.post("/api/users", json=payload).status_code == 422


def test_text_fields_are_trimmed(client: TestClient) -> None:
    created = client.post(
        "/api/users",
        json={
            "username": " n ",
            "email": " e@example.com ",
            "password": "p",
            "abono": " A ",
        },
    ).json()
    assert (created["username"], created["email"], created["abono"]) == (
        "n",
        "e@example.com",
        "A",
    )


def test_blank_rename_is_rejected(client: TestClient, profile: dict) -> None:
    response = client.patch(f"/api/users/{profile['id']}", json={"username": "  "})
    assert response.status_code == 422
