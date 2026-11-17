"""Activation, as the executable requires it: request, approval, revocation, outages."""

from collections.abc import Callable
from pathlib import Path

from conftest import Activation, App, Pages, admin_page, badge, expect

FUTURE = "01/01/2099"


def request_access(page, name: str = "Ana", contact: str = "ana@example.com") -> None:
    page.fill("#activation-name", name)
    page.fill("#activation-contact", contact)
    page.get_by_role("button", name="Solicitar acceso").click()
    expect(
        page.get_by_text("Solicitud enviada. Pendiente de aprobación.")
    ).to_be_visible()


def test_a_new_computer_asks_and_is_let_in(
    make_app: Callable[..., App], activation: Activation, pages: Pages
) -> None:
    app = make_app(activation)
    page = pages.new(app.url)
    expect(page.get_by_text("Este equipo no tiene acceso.")).to_be_visible()
    expect(page.locator(".layout")).to_have_count(0)
    expect(page.locator(".privacy")).to_contain_text(
        "tu IP, el nombre de este equipo, tu usuario"
    )
    button = page.get_by_role("button", name="Solicitar acceso")
    expect(button).to_be_disabled()
    page.fill("#activation-name", "   ")
    page.fill("#activation-contact", "   ")
    expect(button).to_be_disabled()
    request_access(page)

    # What the admin learns about whoever asks.
    machine = activation.machine(app.machine_id)
    assert machine["status"] == "pending"
    assert machine["hostname"] and machine["os_user"] and machine["os_name"]
    assert machine["request_ip"] == "127.0.0.1"
    page.reload()
    expect(
        page.get_by_text("Solicitud enviada. Pendiente de aprobación.")
    ).to_be_visible()

    activation.decide(app.machine_id, "approve")
    page.get_by_role("button", name="Comprobar ahora").click()
    expect(page.locator(".layout")).to_be_visible()
    profile = app.profile("Ana", "SESSION")
    assert (
        app.api(
            "POST",
            "/api/jobs/current",
            {"user_id": profile["id"], "journey_type": "ida", "date": FUTURE},
        )[0]
        == 202
    )
    expect(badge(page)).to_contain_text("Elige tren", ignore_case=True)
    assert (app.data / "license.json").exists()


def test_an_approval_unlocks_the_app_by_itself(
    make_app: Callable[..., App], activation: Activation, pages: Pages
) -> None:
    app = make_app(activation)
    page = pages.new(app.url)
    request_access(page)
    activation.decide(app.machine_id, "approve")
    # The panel asks again every 30 seconds while pending.
    expect(page.locator(".layout")).to_be_visible(timeout=40_000)


def test_a_revocation_locks_it_again_and_an_approval_reopens_it(
    make_app: Callable[..., App], activation: Activation, pages: Pages
) -> None:
    app = make_app(activation)
    page = pages.new(app.url)
    request_access(page)
    activation.decide(app.machine_id, "approve")
    page.get_by_role("button", name="Comprobar ahora").click()
    app.profile("Ana", "SESSION")
    page.reload()
    page.fill("#date", "2099-01-01")

    activation.decide(app.machine_id, "revoke")
    # The app asks the server at most hourly; this is that check, brought forward.
    app.api("POST", "/api/activation/refresh")
    page.get_by_role("button", name="Ver trenes del día").click()
    expect(page.get_by_text("Acceso revocado.")).to_be_visible()
    # The panel says why; the refusal's own banner would only repeat it.
    expect(page.locator(".banner")).to_have_count(0)
    assert not (app.data / "license.json").exists()
    # Asking again changes nothing once revoked.
    assert (
        app.api("POST", "/api/activation/request", {"name": "Otra", "contact": "x"})[1][
            "status"
        ]
        == "revoked"
    )

    activation.decide(app.machine_id, "approve")
    page.get_by_role("button", name="Comprobar otra vez").click()
    expect(page.locator(".layout")).to_be_visible()
    expect(page.locator(".banner")).to_have_count(0)


def test_an_approved_computer_keeps_working_offline(
    make_app: Callable[..., App], activation: Activation, pages: Pages, tmp_path: Path
) -> None:
    app = make_app(activation)
    page = pages.new(app.url)
    request_access(page)
    activation.decide(app.machine_id, "approve")
    page.get_by_role("button", name="Comprobar ahora").click()
    expect(page.locator(".layout")).to_be_visible()

    activation.service.stop()
    app.stop()
    # Restarted while the server is down: only the saved license vouches for it.
    app = make_app(activation, data=app.data, machine_id=app.machine_id)
    page = pages.new(app.url)
    expect(page.locator(".layout")).to_be_visible()
    expect(page.locator(".session__state--warn")).to_contain_text(
        "Sin conexión con el servidor de activación · válido hasta el"
    )
    profile = app.profile("Ana", "SESSION")
    assert (
        app.api(
            "POST",
            "/api/jobs/current",
            {"user_id": profile["id"], "journey_type": "ida", "date": FUTURE},
        )[0]
        == 202
    )


def test_an_unreachable_server_is_said_and_retried(
    make_app: Callable[..., App], activation: Activation, pages: Pages
) -> None:
    activation.service.stop()
    app = make_app(activation)
    page = pages.new(app.url)
    expect(
        page.get_by_text("Sin conexión con el servidor de activación.")
    ).to_be_visible()
    expect(
        page.get_by_text("No se pudo contactar con el servidor de activación")
    ).to_be_visible()
    assert (
        app.api(
            "POST",
            "/api/jobs/current",
            {"user_id": 1, "journey_type": "ida", "date": FUTURE},
        )[0]
        == 403
    )


def test_a_deleted_computer_can_ask_again(
    make_app: Callable[..., App], activation: Activation, pages: Pages
) -> None:
    app = make_app(activation)
    page = pages.new(app.url)
    request_access(page)
    admin = admin_page(pages, activation)
    card = admin.locator("article.card", has_text="Ana")
    card.get_by_role("button", name="Borrar").click()
    admin.locator("#confirm-dialog").get_by_role("button", name="Borrar").click()
    expect(card).to_have_count(0)
    page.get_by_role("button", name="Comprobar ahora").click()
    expect(page.get_by_text("Este equipo no tiene acceso.")).to_be_visible()
    request_access(page, name="Ana otra vez")
