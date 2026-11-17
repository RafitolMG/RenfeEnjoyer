"""A search, from the form to the reservation; a scripted bot stands in for Renfe."""

from collections.abc import Callable
from datetime import date, timedelta

import pytest
from conftest import App, Pages, badge, expect, wait_until

TOMORROW = date.today() + timedelta(days=1)
FUTURE = "01/01/2099"


def start_search(page, profile: str, day: date = TOMORROW, journey: str = "ida") -> None:
    page.select_option("#profile", label=profile)
    page.select_option("#journey", journey)
    page.fill("#date", day.isoformat())
    page.get_by_role("button", name="Ver trenes del día").click()


def to_state(page, label: str, timeout: int = 15_000) -> None:
    expect(badge(page)).to_contain_text(label, ignore_case=True, timeout=timeout)


def test_the_whole_search_from_captcha_to_reservation(app: App, pages: Pages) -> None:
    app.profile("Ana", "HAPPY")
    page = pages.new(app.url)
    start_search(page, "Ana · HAPPY")
    expect(page.get_by_role("button", name="Búsqueda en curso…")).to_be_disabled()

    to_state(page, "Captcha pendiente")
    expect(page.get_by_text("Renfe pide resolver un captcha")).to_be_visible()
    expect(page.locator(".summary")).to_contain_text("Por elegir")

    to_state(page, "Código requerido")
    code = page.get_by_label("Código de verificación")
    send = page.get_by_role("button", name="Enviar")
    expect(send).to_be_disabled()
    code.fill("12")
    send.click()
    expect(page.locator(".banner")).to_contain_text("at least 4 characters")
    code.fill("000000")
    send.click()
    expect(page.locator(".log")).to_contain_text("Renfe ha rechazado el código")
    to_state(page, "Código requerido")
    code.fill("123456")
    code.press("Enter")

    to_state(page, "Elige tren")
    rows = page.locator(".picker__table tbody tr")
    expect(rows).to_have_count(3)
    headers = page.locator(".picker__table thead th").all_inner_texts()
    # Seats right after the time: the one column that decides, visible without scrolling.
    assert [h.lower() for h in headers[1:3]] == ["salida", "plazas"]
    confirm = page.locator(".picker__confirm")
    expect(confirm).to_be_disabled()
    rows.filter(has_text="07:18").click()
    expect(confirm).to_have_text("Buscar plaza en el de las 07:18")
    confirm.click()

    to_state(page, "Buscando plazas")
    expect(page.locator(".summary")).to_contain_text("07:18")
    expect(page.locator(".attempts__value")).to_have_text("3")
    to_state(page, "Plaza reservada")
    expect(page.get_by_text("Completa la compra en la ventana")).to_be_visible()
    page.get_by_role("button", name="He terminado, cerrar navegador").click()

    to_state(page, "Finalizado")
    expect(page.get_by_text("Sesión verificada el")).to_be_visible()
    expect(page.get_by_role("button", name="Ver trenes del día")).to_be_enabled()
    lines = page.locator(".log__line").count()
    page.reload()
    to_state(page, "Finalizado")
    expect(page.locator(".log__line")).to_have_count(lines)


def test_a_stored_session_can_be_closed(app: App, pages: Pages) -> None:
    app.profile("Ana", "SESSION")
    page = pages.new(app.url)
    start_search(page, "Ana · SESSION")
    to_state(page, "Elige tren")
    page.get_by_role("button", name="Detener búsqueda").click()
    to_state(page, "Detenido")
    expect(page.get_by_text("Sesión verificada el")).to_be_visible()
    page.once("dialog", lambda d: d.dismiss())
    page.get_by_role("button", name="Cerrar sesión").click()
    expect(page.get_by_text("Sesión verificada el")).to_be_visible()
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Cerrar sesión").click()
    expect(page.get_by_text("Sin sesión iniciada")).to_be_visible()


@pytest.mark.parametrize(
    "stop_at", ["Captcha pendiente", "Código requerido", "Elige tren", "Buscando plazas"]
)
def test_a_search_stops_wherever_it_is(app: App, pages: Pages, stop_at: str) -> None:
    app.profile("Ana", "SLOW")
    page = pages.new(app.url)
    start_search(page, "Ana · SLOW")
    steps: list[Callable[[], None]] = [
        lambda: to_state(page, "Captcha pendiente"),
        lambda: (
            to_state(page, "Código requerido"),
            page.get_by_label("Código de verificación").fill("123456"),
            page.get_by_role("button", name="Enviar").click(),
        ),
        lambda: (
            to_state(page, "Elige tren"),
            page.locator(".picker__table tbody tr").first.click(),
            page.locator(".picker__confirm").click(),
        ),
        lambda: to_state(page, "Buscando plazas"),
    ]
    labels = ["Captcha pendiente", "Código requerido", "Elige tren", "Buscando plazas"]
    for label, step in zip(labels, steps, strict=True):
        if label == stop_at:
            to_state(page, label)
            break
        step()
    page.get_by_role("button", name="Detener búsqueda").click()
    to_state(page, "Detenido")
    expect(page.locator(".picker")).to_have_count(0)
    expect(page.get_by_label("Código de verificación")).to_have_count(0)


@pytest.mark.parametrize(
    ("abono", "message"),
    [
        ("FAIL", "Renfe ha rechazado el login: usuario o contraseña incorrectos"),
        ("CRASH", "RuntimeError: chrome exploded"),
        ("NOTRAINS", "Renfe no lista ningún tren para ese día"),
    ],
)
def test_failures_are_explained(app: App, pages: Pages, abono: str, message: str) -> None:
    app.profile("Ana", abono)
    page = pages.new(app.url)
    start_search(page, f"Ana · {abono}")
    to_state(page, "Error")
    expect(page.locator(".monitor__message")).to_have_text(message)
    expect(page.get_by_role("button", name="Ver trenes del día")).to_be_enabled()


def test_a_new_search_starts_with_a_clean_log(app: App, pages: Pages) -> None:
    app.profile("Ana", "NOTRAINS")
    app.profile("Luis", "FAIL")
    page = pages.new(app.url)
    start_search(page, "Ana · NOTRAINS")
    to_state(page, "Error")
    start_search(page, "Luis · FAIL")
    to_state(page, "Error")
    expect(page.locator(".log")).not_to_contain_text("ningún tren")


def test_dates_in_the_past_cannot_be_searched(app: App, pages: Pages) -> None:
    profile = app.profile("Ana", "SESSION")
    page = pages.new(app.url)
    page.fill("#date", (date.today() - timedelta(days=1)).isoformat())
    expect(page.get_by_role("button", name="Ver trenes del día")).to_be_disabled()
    page.fill("#date", date.today().isoformat())
    expect(page.get_by_role("button", name="Ver trenes del día")).to_be_enabled()
    yesterday = (date.today() - timedelta(days=1)).strftime("%d/%m/%Y")
    for day, detail in [(yesterday, "Esa fecha ya ha pasado"), ("31/02/2099", None)]:
        status, body = app.api(
            "POST",
            "/api/jobs/current",
            {"user_id": profile["id"], "journey_type": "ida", "date": day},
        )
        assert status == 422
        if detail:
            assert body["detail"] == detail


def test_two_tabs_share_one_search(app: App, pages: Pages) -> None:
    ana = app.profile("Ana", "SLOW")
    first, second = pages.new(app.url), pages.new(app.url)
    start_search(first, "Ana · SLOW")
    to_state(second, "Captcha pendiente")
    expect(second.get_by_role("button", name="Búsqueda en curso…")).to_be_disabled()
    status, _ = app.api(
        "POST",
        "/api/jobs/current",
        {"user_id": ana["id"], "journey_type": "ida", "date": FUTURE},
    )
    assert status == 409

    to_state(second, "Código requerido")
    second.get_by_label("Código de verificación").fill("654321")
    second.get_by_role("button", name="Enviar").click()
    to_state(first, "Elige tren")
    first.locator(".picker__table tbody tr", has_text="08:10").click()
    first.locator(".picker__confirm").click()
    to_state(second, "Buscando plazas")
    expect(second.locator(".summary")).to_contain_text("08:10")
    # A train not on the list, or a choice once the bot has moved on, is refused.
    assert (
        app.api("POST", "/api/jobs/current/train", {"departure_time": "06:45"})[0] == 409
    )

    expect(first.locator(".attempts__value")).not_to_have_text("0")
    first.reload()
    to_state(first, "Buscando plazas")
    expect(first.locator(".attempts")).to_be_visible()


def test_actions_out_of_turn_are_refused(app: App) -> None:
    profile = app.profile("Ana", "SESSION")
    assert app.api("POST", "/api/jobs/current/stop")[0] == 409
    assert app.api("POST", "/api/jobs/current/release")[0] == 409
    assert app.api("POST", "/api/jobs/current/code", {"code": "123456"})[0] == 409
    assert app.api("POST", "/api/jobs/current/code", {"code": "12; drop"})[0] == 422
    app.api(
        "POST",
        "/api/jobs/current",
        {"user_id": profile["id"], "journey_type": "ida", "date": FUTURE},
    )
    # Once the bot lists the trains, only those can be chosen.
    status = wait_until(
        lambda: (
            (
                code := app.api(
                    "POST", "/api/jobs/current/train", {"departure_time": "23:59"}
                )[0]
            )
            != 409
            and code
        )
    )
    assert status == 422
    assert (
        app.api("POST", "/api/jobs/current/train", {"departure_time": "07:18"})[0] == 202
    )
    assert (
        app.api("POST", "/api/jobs/current/train", {"departure_time": "06:45"})[0] == 409
    )
    app.api("POST", "/api/jobs/current/stop")


def test_losing_the_server_is_said_and_recovered(
    make_app: Callable[..., App], pages: Pages
) -> None:
    app = make_app()
    ana = app.profile("Ana", "SLOW")
    page = pages.new(app.url)
    app.api(
        "POST",
        "/api/jobs/current",
        {"user_id": ana["id"], "journey_type": "ida", "date": FUTURE},
    )
    to_state(page, "Captcha pendiente")
    app.stop()
    expect(page.locator(".monitor__message")).to_have_text(
        "Sin conexión con el servidor · reconectando…"
    )
    page.get_by_role("button", name="Detener búsqueda").click()
    expect(page.locator(".banner")).to_have_text("Sin conexión con el servidor")

    make_app(data=app.data, port=app.port)
    to_state(page, "En reposo")
    expect(page.locator(".monitor .link--down")).to_have_count(0)


@pytest.mark.parametrize("width", [1280, 390])
def test_the_train_list_shows_the_seats_without_scrolling(
    app: App, pages: Pages, width: int
) -> None:
    app.profile("Ana", "SESSION")
    page = pages.new(app.url, width=width)
    start_search(page, "Ana · SESSION")
    to_state(page, "Elige tren")
    visible = page.evaluate(
        """() => {
          const box = document.querySelector('.picker__scroll').getBoundingClientRect();
          return [...document.querySelectorAll('.picker__table th')]
            .filter((th) => th.getBoundingClientRect().right <= box.right + 1)
            .map((th) => th.textContent.trim()) }"""
    )
    assert "Plazas" in visible
    assert page.evaluate("document.documentElement.scrollWidth") <= width
