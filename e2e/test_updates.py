"""Updates end to end: GitHub release, admin publishing, download, handover, rollback.

The app runs app.desktop's real handover; its "executable" is a file whose first line
names the version the next process starts as (see harness/app_server.py).
"""

from collections.abc import Callable

from conftest import (
    PLATFORM,
    Activation,
    App,
    FakeGitHub,
    Pages,
    admin_page,
    expect,
    wait_until,
)

FUTURE = "01/01/2099"
PADDING = b"\n" + b"\0" * (4 * 1024 * 1024)  # 4 MiB: big enough to watch it download


def build(version: str, broken: bool = False) -> bytes:
    return (b"BROKEN" if broken else f"E2E:{version}".encode()) + PADDING


def release(github: FakeGitHub, version: str, broken: bool = False) -> None:
    """A release whose build for this platform is `version`; the other is a decoy."""
    other = "windows" if PLATFORM == "linux" else "linux"
    github.release(version, {PLATFORM: build(version, broken), other: build("9.9.9")})


def banner(page):
    return page.locator(".update")


def test_an_update_is_downloaded_installed_and_reloaded(
    make_app: Callable[..., App], activation: Activation, github: FakeGitHub, pages: Pages
) -> None:
    release(github, "2.1.0")
    github.throttle = 0.05
    app = make_app(activation)
    page = pages.new(app.url)
    expect(banner(page)).to_have_count(0)

    admin = admin_page(pages, activation)
    admin.get_by_role("tab", name="Versiones").click()
    card = admin.locator("#versions article.card", has_text="v2.1.0")
    expect(card).to_contain_text("Sin publicar")
    card.get_by_role("button", name="Publicar").click()
    admin.locator("#confirm-dialog").get_by_role("button", name="Publicar").click()
    expect(card).to_contain_text("Publicada")

    seen: list[str] = []
    # The hourly check, brought forward; the page notices on its next look.
    app.api("POST", "/api/update/check")
    page.reload()
    while "lista" not in (seen[-1] if seen else ""):
        text = banner(page).inner_text() if banner(page).count() else ""
        if text and (not seen or seen[-1] != text):
            seen.append(text)
        page.wait_for_timeout(200)
        assert len(seen) < 200
    percents = [int(t.split("·")[1].strip(" %")) for t in seen if "Descargando" in t]
    assert len(percents) >= 2 and percents == sorted(percents), seen
    expect(banner(page)).to_contain_text("Versión 2.1.0 lista.")

    page.get_by_role("button", name="Actualizar").click()
    expect(banner(page)).to_contain_text("Actualizando a la 2.1.0")
    page.wait_for_function("() => !document.querySelector('.update')", timeout=60_000)
    assert app.version() == "2.1.0"
    # This platform's build, not the other one's.
    assert app.exe.read_bytes().startswith(b"E2E:2.1.0")
    wait_until(lambda: not app.exe.with_name(app.exe.name + ".old").exists())
    assert sorted(
        p.name for p in app.data.iterdir() if p.name.startswith(app.exe.name)
    ) == [app.exe.name]


def test_no_update_while_a_search_runs(
    make_app: Callable[..., App], activation: Activation, github: FakeGitHub, pages: Pages
) -> None:
    release(github, "2.1.0")
    activation.publish("2.1.0")
    app = make_app(activation)
    activation_approves(activation, app, pages)
    profile = app.profile("Ana", "SLOW")
    page = pages.new(app.url)
    expect(banner(page)).to_contain_text("Versión 2.1.0 lista.")
    app.api(
        "POST",
        "/api/jobs/current",
        {"user_id": profile["id"], "journey_type": "ida", "date": FUTURE},
    )
    expect(page.get_by_role("button", name="Actualizar")).to_be_disabled()
    expect(page.get_by_role("button", name="Actualizar")).to_have_attribute(
        "title", "Termina la búsqueda primero"
    )
    assert app.api("POST", "/api/update")[0] == 409
    page.get_by_role("button", name="Detener búsqueda").click()
    expect(page.get_by_role("button", name="Actualizar")).to_be_enabled()


def test_a_build_that_does_not_start_is_rolled_back(
    make_app: Callable[..., App], activation: Activation, github: FakeGitHub, pages: Pages
) -> None:
    release(github, "2.1.0", broken=True)
    activation.publish("2.1.0")
    app = make_app(activation)
    original = app.exe.read_bytes()
    page = pages.new(app.url)
    expect(banner(page)).to_contain_text("Versión 2.1.0 lista.")
    page.get_by_role("button", name="Actualizar").click()
    expect(banner(page)).to_contain_text(
        "La versión 2.1.0 no arrancó; sigues con la 2.0.0", timeout=60_000
    )
    assert app.version() == "2.0.0"
    assert app.exe.read_bytes() == original
    downloads = len(github.downloads)
    app.api("POST", "/api/update/check")
    expect(banner(page)).to_contain_text("no arrancó")
    assert len(github.downloads) == downloads  # not offered again until a restart


def test_a_rebuilt_release_is_flagged_and_re_pinned(
    make_app: Callable[..., App], activation: Activation, github: FakeGitHub, pages: Pages
) -> None:
    release(github, "2.1.0")
    activation.publish("2.1.0")
    # Rebuilt under the same number after publishing: the pin no longer matches.
    github.release(
        "2.1.0",
        {
            PLATFORM: build("2.1.0") + b"rebuilt",
            "windows" if PLATFORM == "linux" else "linux": build("9.9.9"),
        },
    )
    app = make_app(activation)
    page = pages.new(app.url)
    expect(banner(page)).to_contain_text(
        "La descarga de la versión 2.1.0 no coincide con la publicada"
    )
    downloads = len(github.downloads)
    for _ in range(3):  # the hourly checks
        app.api("POST", "/api/update/check")
    assert len(github.downloads) == downloads, "it must not download the same file again"

    admin = admin_page(pages, activation)
    admin.get_by_role("tab", name="Versiones").click()
    card = admin.locator("#versions article.card", has_text="v2.1.0")
    expect(card).to_contain_text("Los archivos de GitHub han cambiado")
    card.get_by_role("button", name="Volver a fijar").click()
    admin.locator("#confirm-dialog").get_by_role("button", name="Volver a fijar").click()
    expect(card.locator(".stale")).to_have_count(0)

    page.get_by_role("button", name="Comprobar de nuevo").click()
    expect(banner(page)).to_contain_text("Versión 2.1.0 lista.")


def test_a_minimum_version_stops_older_ones_from_searching(
    make_app: Callable[..., App], activation: Activation, github: FakeGitHub, pages: Pages
) -> None:
    release(github, "2.1.0")
    activation.publish("2.1.0")
    status, _ = activation.admin(
        "PUT", "/api/v1/admin/min-version", {"min_version": "2.1.0"}
    )
    assert status == 200
    app = make_app(activation)
    activation_approves(activation, app, pages)
    profile = app.profile("Ana", "SESSION")
    page = pages.new(app.url)
    expect(banner(page)).to_contain_text("Esta versión ya no funciona con Renfe.")
    status, body = app.api(
        "POST",
        "/api/jobs/current",
        {"user_id": profile["id"], "journey_type": "ida", "date": FUTURE},
    )
    assert status == 403 and "actualiza" in body["detail"]

    admin = admin_page(pages, activation)
    admin.get_by_role("tab", name="Versiones").click()
    card = admin.locator("#versions article.card", has_text="v2.1.0")
    card.get_by_role("button", name="Retirar").click()
    admin.locator("#confirm-dialog").get_by_role("button", name="Retirar").click()
    expect(admin.locator("#alert")).to_have_text(
        "Es la versión mínima: cambia antes la versión mínima"
    )
    admin.select_option("#min-version", "")
    admin.locator("#confirm-dialog").get_by_role("button", name="Aceptar").click()
    expect(admin.locator("#min-version")).to_have_value("")
    app.api("POST", "/api/update/check")
    assert (
        app.api(
            "POST",
            "/api/jobs/current",
            {"user_id": profile["id"], "journey_type": "ida", "date": FUTURE},
        )[0]
        == 202
    )


def test_a_withdrawn_version_is_not_installed(
    make_app: Callable[..., App], activation: Activation, github: FakeGitHub, pages: Pages
) -> None:
    release(github, "2.1.0")
    activation.publish("2.1.0")
    app = make_app(activation)
    page = pages.new(app.url)
    expect(banner(page)).to_contain_text("Versión 2.1.0 lista.")
    assert activation.admin("DELETE", "/api/v1/admin/releases/2.1.0")[0] == 204
    app.api("POST", "/api/update/check")
    page.reload()
    expect(banner(page)).to_have_count(0)
    assert not app.exe.with_name(app.exe.name + ".new").exists()


def test_a_finished_download_survives_a_restart(
    make_app: Callable[..., App], activation: Activation, github: FakeGitHub, pages: Pages
) -> None:
    release(github, "2.1.0")
    activation.publish("2.1.0")
    app = make_app(activation)
    page = pages.new(app.url)
    expect(banner(page)).to_contain_text("Versión 2.1.0 lista.")
    downloads = len(github.downloads)
    app.stop()
    app = make_app(activation, data=app.data, machine_id=app.machine_id)
    page = pages.new(app.url)
    expect(banner(page)).to_contain_text("Versión 2.1.0 lista.")
    assert len(github.downloads) == downloads


def test_a_checkout_never_updates(make_app: Callable[..., App], pages: Pages) -> None:
    app = make_app(executable=False)
    page = pages.new(app.url)
    expect(page.locator(".layout")).to_be_visible()
    assert app.api("GET", "/api/update")[1]["supported"] is False
    expect(banner(page)).to_have_count(0)


def activation_approves(activation: Activation, app: App, pages: Pages) -> None:
    page = pages.new(app.url)
    page.fill("#activation-name", "Ana")
    page.fill("#activation-contact", "ana@example.com")
    page.get_by_role("button", name="Solicitar acceso").click()
    expect(page.get_by_text("Solicitud enviada")).to_be_visible()
    activation.decide(app.machine_id, "approve")
    page.get_by_role("button", name="Comprobar ahora").click()
    expect(page.locator(".layout")).to_be_visible()
