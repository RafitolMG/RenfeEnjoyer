"""Profiles: what a new user does first, and every way it can go wrong."""

import sqlite3
from datetime import date, timedelta

from conftest import App, Pages, badge, expect

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


def open_dialog(page):
    page.get_by_role("button", name="Gestionar").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    return dialog


def fill_profile(page, name: str, email: str, password: str, abono: str) -> None:
    page.fill("#username", name)
    page.fill("#email", email)
    page.fill("#password", password)
    page.fill("#abono", abono)
    page.get_by_role("dialog").get_by_role("button", name="Guardar").click()


def test_a_new_user_starts_empty_and_creates_a_profile(app: App, pages: Pages) -> None:
    page = pages.new(app.url)
    expect(page.locator("#profile")).to_be_disabled()
    expect(page.get_by_text("Crea un perfil con tus credenciales")).to_be_visible()
    page.fill("#date", TOMORROW)
    expect(page.get_by_role("button", name="Ver trenes del día")).to_be_disabled()

    dialog = open_dialog(page)
    # With no profiles the form opens straight away and has nothing to go back to.
    expect(dialog.get_by_role("heading", name="Nuevo perfil")).to_be_visible()
    expect(dialog.get_by_role("button", name="Volver")).to_have_count(0)
    fill_profile(page, "Ana", "ana@example.com", "secreto", "ABONO1")
    expect(dialog).to_contain_text("ana@example.com · abono ABONO1")
    page.keyboard.press("Escape")

    expect(page.locator("#profile option:checked")).to_have_text("Ana · ABONO1")
    expect(page.get_by_role("button", name="Ver trenes del día")).to_be_enabled()
    _, profiles = app.api("GET", "/api/users")
    assert "password" not in profiles[0]


def test_the_dialog_closes_with_escape_the_cross_and_outside(
    app: App, pages: Pages
) -> None:
    page = pages.new(app.url)
    open_dialog(page)
    page.keyboard.press("Escape")
    open_dialog(page)
    page.get_by_role("button", name="Cerrar").click()
    open_dialog(page)
    page.mouse.click(5, 5)
    expect(page.get_by_role("dialog")).to_have_count(0)


def test_blank_or_invalid_details_are_refused(app: App, pages: Pages) -> None:
    page = pages.new(app.url)
    open_dialog(page)
    fill_profile(page, "   ", "a@b.es", "   ", "   ")
    expect(page.get_by_role("dialog").locator(".error-text")).to_have_text(
        "Rellena todos los campos."
    )
    fill_profile(page, "Ana", "no-es-un-correo", "x", "A")
    assert not page.eval_on_selector("#email", "e => e.validity.valid")
    assert app.api("GET", "/api/users")[1] == []
    # The API refuses the same, for any other client.
    status, _ = app.api(
        "POST",
        "/api/users",
        {"username": " ", "email": "a@b.es", "password": "x", "abono": "A"},
    )
    assert status == 422


def test_names_must_be_unique_also_when_renaming(app: App, pages: Pages) -> None:
    app.profile("Ana", "A1")
    app.profile("Luis", "L1")
    page = pages.new(app.url)
    dialog = open_dialog(page)
    dialog.get_by_role("button", name="Nuevo perfil").click()
    fill_profile(page, "Ana", "otra@example.com", "x", "Z")
    expect(dialog.locator(".error-text")).to_have_text(
        "Ya existe un perfil con ese nombre"
    )
    dialog.get_by_role("button", name="Volver").click()
    dialog.locator(".profiles__item", has_text="Luis").get_by_role(
        "button", name="Editar"
    ).click()
    page.fill("#username", "Ana")
    dialog.get_by_role("button", name="Guardar").click()
    expect(dialog.locator(".error-text")).to_have_text(
        "Ya existe un perfil con ese nombre"
    )


def test_passwords_are_stored_encrypted_and_kept_when_left_blank(
    app: App, pages: Pages
) -> None:
    profile = app.profile("Ana", "SESSION", password="contraseña-larga")
    with sqlite3.connect(app.data / "app.db") as db:
        stored = db.execute("select password from users").fetchone()[0]
    assert stored.startswith("enc:v1:") and "contraseña" not in stored

    page = pages.new(app.url)
    dialog = open_dialog(page)
    dialog.get_by_role("button", name="Editar").click()
    expect(page.locator("#password")).to_have_value("")
    page.fill("#abono", "SESSION")
    dialog.get_by_role("button", name="Guardar").click()
    page.keyboard.press("Escape")

    # The scripted bot logs the length of the password it would type into Renfe.
    app.api(
        "POST",
        "/api/jobs/current",
        {"user_id": profile["id"], "journey_type": "ida", "date": "01/01/2099"},
    )
    expect(page.locator(".log")).to_contain_text("Contraseña de 16 caracteres")


def test_deleting_asks_first_and_reselects(app: App, pages: Pages) -> None:
    app.profile("Ana", "A1")
    app.profile("Luis", "L1")
    page = pages.new(app.url)
    page.select_option("#profile", label="Luis · L1")
    dialog = open_dialog(page)
    item = dialog.locator(".profiles__item", has_text="Luis")
    page.once("dialog", lambda d: d.dismiss())
    item.get_by_role("button", name="Eliminar").click()
    expect(item).to_have_count(1)
    page.once("dialog", lambda d: d.accept())
    item.get_by_role("button", name="Eliminar").click()
    expect(item).to_have_count(0)
    page.keyboard.press("Escape")
    expect(page.locator("#profile option:checked")).to_have_text("Ana · A1")


def test_deleting_a_profile_drops_its_renfe_session(app: App, pages: Pages) -> None:
    ana = app.profile("Ana", "SESSION", email="cuenta@example.com")
    app.profile("Ana bis", "OTRO", email="cuenta@example.com")
    page = pages.new(app.url)
    app.api(
        "POST",
        "/api/jobs/current",
        {"user_id": ana["id"], "journey_type": "ida", "date": "01/01/2099"},
    )
    expect(badge(page)).to_contain_text("Elige tren", ignore_case=True)
    # While the browser holds the profile open, it cannot be deleted under it.
    assert app.api("DELETE", f"/api/users/{ana['id']}")[0] == 409
    app.api("POST", "/api/jobs/current/stop")
    expect(badge(page)).to_contain_text("Detenido", ignore_case=True)

    sessions = list((app.data / "browser-profile").iterdir())
    assert len(sessions) == 1
    # Another profile still uses the account: the session stays.
    assert app.api("DELETE", f"/api/users/{ana['id']}")[0] == 204
    assert sessions[0].exists()
    other = app.api("GET", "/api/users")[1][0]
    assert app.api("DELETE", f"/api/users/{other['id']}")[0] == 204
    assert not sessions[0].exists()


def test_the_selected_profile_is_remembered_and_follows_a_running_search(
    app: App, pages: Pages
) -> None:
    app.profile("Alfa", "SESSION")
    zeta = app.profile("Zeta", "SESSION")
    page = pages.new(app.url)
    expect(page.locator("#profile option:checked")).to_have_text("Alfa · SESSION")
    page.select_option("#profile", label="Zeta · SESSION")
    page.reload()
    expect(page.locator("#profile option:checked")).to_have_text("Zeta · SESSION")

    page.select_option("#profile", label="Alfa · SESSION")
    app.api(
        "POST",
        "/api/jobs/current",
        {"user_id": zeta["id"], "journey_type": "ida", "date": "01/01/2099"},
    )
    expect(page.locator("#profile option:checked")).to_have_text("Zeta · SESSION")
    other = pages.new(app.url)
    expect(other.locator("#profile option:checked")).to_have_text("Zeta · SESSION")
