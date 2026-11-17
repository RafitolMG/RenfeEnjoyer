"""The activation server's admin: login, password, computers and their history."""

import json
import urllib.request

from conftest import ADMIN_PASSWORD, Activation, Pages, admin_page, expect, http_json


def ask(
    activation: Activation,
    machine: str,
    name: str,
    contact: str,
    device: dict | None = None,
) -> None:
    body = {
        "machine_id": machine * 64,
        "name": name,
        "contact": contact,
        "app_version": "2.0.5",
    }
    if device:
        body["device"] = device
    status, _ = http_json("POST", activation.url + "/api/v1/requests", body)
    assert status == 200


def refuse_native_dialogs(page) -> list[str]:
    """A browser that blocks a page's dialogs answers "no" to all of them."""
    seen: list[str] = []
    page.on("dialog", lambda dialog: (seen.append(dialog.message), dialog.dismiss()))
    return seen


def test_login_and_logout(activation: Activation, pages: Pages) -> None:
    page = pages.new(activation.url + "/admin")
    expect(page).to_have_url(activation.url + "/admin/login")
    page.click("#submit")
    expect(page.locator("#error")).to_have_text("Escribe la contraseña.")
    page.fill("#password", "mala")
    page.click("#submit")
    expect(page.locator("#error")).to_have_text("Contraseña incorrecta")
    page.fill("#password", ADMIN_PASSWORD)
    page.click("#submit")
    expect(page).to_have_url(activation.url + "/admin")
    expect(page.locator("#initial-notice")).to_be_visible()
    page.goto(activation.url + "/admin/login")
    expect(page).to_have_url(activation.url + "/admin")
    page.locator("#logout").click()
    expect(page).to_have_url(activation.url + "/admin/login")
    page.goto(activation.url + "/admin")
    expect(page).to_have_url(activation.url + "/admin/login")


def test_changing_the_password(activation: Activation, pages: Pages) -> None:
    page = admin_page(pages, activation)
    other = admin_page(pages, activation)
    page.locator("#open-password").click()
    form, message = page.locator("#password-form"), page.locator("#password-message")
    new = "una-clave-nueva-larga"
    for current, password, repeat, expected in [
        ("", "", "", "Rellena todos los campos."),
        (ADMIN_PASSWORD, "corta", "corta", "Mínimo 12 caracteres."),
        (ADMIN_PASSWORD, new, new + "x", "No coinciden."),
        ("mala", new, new, "La contraseña actual no es correcta"),
        (
            ADMIN_PASSWORD,
            ADMIN_PASSWORD,
            ADMIN_PASSWORD,
            "La nueva contraseña debe ser distinta",
        ),
    ]:
        form.locator("[name=current]").fill(current)
        form.locator("[name=new]").fill(password)
        form.locator("[name=repeat]").fill(repeat)
        page.locator("#password-save").click()
        expect(message).to_have_text(expected)
    form.locator("[name=current]").fill(ADMIN_PASSWORD)
    form.locator("[name=new]").fill(new)
    form.locator("[name=repeat]").fill(new)
    page.locator("#password-save").click()
    expect(message).to_have_text("Contraseña cambiada.")
    expect(page.locator("#initial-notice")).to_be_hidden()
    # The other session is signed out; the old password no longer opens it.
    other.reload()
    expect(other).to_have_url(activation.url + "/admin/login")
    assert (
        http_json(
            "POST", activation.url + "/api/v1/admin/login", {"password": ADMIN_PASSWORD}
        )[0]
        == 401
    )
    assert (
        http_json("POST", activation.url + "/api/v1/admin/login", {"password": new})[0]
        == 204
    )


def test_computers_can_be_found_filtered_and_decided(
    activation: Activation, pages: Pages
) -> None:
    ask(
        activation,
        "a",
        "José García",
        "jose@example.com",
        {"hostname": "PORTATIL-JOSE", "user": "jgarcia", "os": "Windows 11 (10.0.26100)"},
    )
    ask(
        activation,
        "b",
        "Ana",
        "ana@example.com",
        {"hostname": "SOBREMESA", "user": "ana"},
    )
    ask(activation, "c", "eeeee", "holaob@gmail.com")
    page = admin_page(pages, activation)
    native = refuse_native_dialogs(page)
    cards = page.locator("article.card")
    expect(cards).to_have_count(3)
    names = lambda: [c.split(" · ")[0] for c in cards.locator(".who").all_inner_texts()]  # noqa: E731

    for query, expected in [
        ("jose", ["José García"]),
        ("GARCÍA", ["José García"]),
        ("holaob", ["eeeee"]),
        ("jgarcia portatil", ["José García"]),
        ("cccccccccccc", ["eeeee"]),
        ("nadie", []),
    ]:
        page.fill("#search", query)
        expect(cards).to_have_count(len(expected))
        assert names() == expected
    expect(page.locator(".empty")).to_have_text("Ninguno.")
    page.fill("#search", "ana")
    expect(page.locator("[data-filter=pending]")).to_have_text("Pendientes (1)")
    page.fill("#search", "")

    card = cards.filter(has_text="José García")
    expect(card).to_contain_text("PORTATIL-JOSE · jgarcia")
    expect(card).to_contain_text("Windows 11 (10.0.26100)")
    expect(card).to_contain_text("Pidió desde 127.0.0.1")
    card.get_by_role("button", name="Historial").click()
    expect(card.locator(".history li").first).to_contain_text("127.0.0.1")

    card.get_by_role("button", name="Aprobar").click()
    expect(cards.filter(has_text="José García")).to_have_count(0)
    page.locator("[data-filter=approved]").click()
    expect(page.locator("[data-filter=approved]")).to_have_text("Aprobados (1)")
    cards.filter(has_text="José García").get_by_role("button", name="Revocar").click()
    page.locator("[data-filter=revoked]").click()
    expect(cards.filter(has_text="José García")).to_have_count(1)
    page.locator("[data-filter=all]").click()
    gone = cards.filter(has_text="eeeee")
    gone.get_by_role("button", name="Borrar").click()
    dialog = page.locator("#confirm-dialog")
    expect(dialog).to_contain_text("¿Borrar a eeeee?")
    dialog.get_by_role("button", name="Cancelar").click()
    expect(gone).to_have_count(1)
    gone.get_by_role("button", name="Borrar").click()
    dialog.get_by_role("button", name="Borrar").click()
    expect(gone).to_have_count(0)
    assert native == [], "the admin must not rely on the browser's own dialogs"


def test_the_list_keeps_the_search_and_open_histories_across_reloads(
    activation: Activation, pages: Pages
) -> None:
    ask(activation, "a", "José", "jose@example.com")
    ask(activation, "b", "Ana", "ana@example.com")
    page = admin_page(pages, activation)
    page.fill("#search", "ana")
    card = page.locator("article.card", has_text="Ana")
    card.get_by_role("button", name="Historial").click()
    page.evaluate("load()")  # what the 30-second refresh calls
    expect(page.locator("article.card")).to_have_count(1)
    expect(card.locator(".history")).to_be_visible()


def test_repeated_wrong_passwords_lock_the_login(
    activation: Activation, pages: Pages
) -> None:
    for _ in range(10):
        http_json("POST", activation.url + "/api/v1/admin/login", {"password": "x"})
    page = pages.new(activation.url + "/admin/login")
    page.fill("#password", ADMIN_PASSWORD)
    page.click("#submit")
    expect(page.locator("#error")).to_have_text(
        "Demasiados intentos fallidos; espera unos minutos."
    )


def test_the_landing_page_offers_both_builds(
    activation: Activation, pages: Pages
) -> None:
    page = pages.new()
    latest = {
        "tag_name": "v2.0.5",
        "assets": [
            {"name": "RenfeEnjoyer.exe", "size": 31_000_000},
            {"name": "RenfeEnjoyer-linux", "size": 54_000_000},
        ],
    }
    page.route(
        "https://api.github.com/repos/**/releases/latest",
        lambda route: route.fulfill(json=latest),
    )
    page.goto(activation.url + "/")
    windows, linux = page.locator("#download"), page.locator("#download-linux")
    expect(windows).to_have_attribute(
        "href",
        "https://github.com/RafitolMG/RenfeEnjoyer/releases/latest/download/RenfeEnjoyer.exe",
    )
    expect(linux).to_have_attribute(
        "href",
        "https://github.com/RafitolMG/RenfeEnjoyer/releases/latest/download/RenfeEnjoyer-linux",
    )
    expect(page.locator("#release-meta")).to_contain_text("v2.0.5 · 31 MB")
    expect(linux).to_be_visible()
    images_loaded = page.evaluate(
        "[...document.images].every((i) => i.complete && i.naturalWidth > 0)"
    )
    assert images_loaded

    # A latest release without a Linux build hides the Linux button.
    without_linux = {**latest, "assets": latest["assets"][:1]}
    other = pages.new()
    other.route(
        "https://api.github.com/repos/**/releases/latest",
        lambda route: route.fulfill(json=without_linux),
    )
    other.goto(activation.url + "/")
    expect(other.locator("#download-linux")).to_be_hidden()
    expect(other.locator("#linux-meta")).to_be_hidden()

    mobile = pages.new(width=390)
    mobile.route(
        "https://api.github.com/repos/**/releases/latest",
        lambda route: route.fulfill(json=latest),
    )
    mobile.goto(activation.url + "/")
    assert mobile.evaluate("document.documentElement.scrollWidth") <= 390


def test_admin_pages_and_api_need_a_session(activation: Activation) -> None:
    assert http_json("GET", activation.url + "/api/v1/admin/machines")[0] == 401
    assert http_json("GET", activation.url + "/api/v1/admin/releases")[0] == 401
    assert (
        http_json(
            "PUT", activation.url + "/api/v1/admin/min-version", {"min_version": None}
        )[0]
        == 401
    )
    assert (
        http_json("POST", activation.url + "/api/v1/check", {"machine_id": "abc"})[0]
        == 422
    )
    with urllib.request.urlopen(activation.url + "/api/v1/public-key") as response:
        assert json.load(response)["public_key"]
