"""The app's own window as its on/off switch: closing it quits the app.

The real window is a browser in app mode (or, on Windows, a native one, which only the
executable's --check-window covers). Here Playwright's page plays the window: the app
runs as it does once it has opened one, quitting when no page is connected.
"""

import os
import subprocess
import time
from collections.abc import Callable

from conftest import (
    BACKEND_PYTHON,
    HARNESS,
    Activation,
    App,
    FakeGitHub,
    Pages,
    badge,
    expect,
    wait_until,
)
from test_search import start_search, to_state
from test_updates import release

QUIT_WHEN_CLOSED = {"RENFE_QUIT_WHEN_CLOSED": "1", "E2E_WINDOW_GRACE": "3"}


def stopped(app: App) -> bool:
    return app.version() is None


def still_up_after_the_grace(app: App, page) -> None:
    page.wait_for_timeout(4_000)
    assert app.version() is not None


def close_without_being_asked(page) -> None:
    asked: list[str] = []
    page.on("dialog", lambda dialog: (asked.append(dialog.type), dialog.dismiss()))
    # With run_before_unload Playwright does not wait for the page to close.
    with page.expect_event("close"):
        page.close(run_before_unload=True)
    assert asked == []


def test_closing_the_window_quits_the_app_but_a_reload_does_not(
    make_app: Callable[..., App], pages: Pages
) -> None:
    app = make_app(extra_env=QUIT_WHEN_CLOSED)
    page = pages.new(app.url)
    expect(badge(page)).to_contain_text("En reposo")
    # Nothing is open yet when the app starts: it waits for the window, not a timer.
    assert app.version() is not None

    page.reload()
    expect(badge(page)).to_contain_text("En reposo")
    page.wait_for_timeout(4_000)
    assert app.version() is not None

    page.close()
    wait_until(lambda: stopped(app), timeout=15)
    assert app.service.process.wait(timeout=10) == 0
    assert "Ventana cerrada" in app.service.log_text()


def test_a_browser_tab_does_not_quit_the_app_unless_it_opened_the_window(
    app: App, pages: Pages
) -> None:
    page = pages.new(app.url)
    expect(badge(page)).to_contain_text("En reposo")
    page.close()
    time.sleep(1)
    assert app.version() is not None


def test_closing_during_a_search_asks_first_then_stops_it(
    make_app: Callable[..., App], pages: Pages
) -> None:
    app = make_app(extra_env=QUIT_WHEN_CLOSED)
    app.profile("Ana", "SLOW")
    page = pages.new(app.url)
    start_search(page, "Ana · SLOW")
    expect(badge(page)).to_contain_text("Captcha pendiente")

    with page.expect_event("dialog") as asked:
        page.close(run_before_unload=True)
    assert asked.value.type == "beforeunload"
    asked.value.dismiss()
    page.wait_for_timeout(4_000)
    assert not page.is_closed()
    assert app.version() is not None

    with page.expect_event("dialog") as asked:
        page.close(run_before_unload=True)
    asked.value.accept()
    wait_until(lambda: stopped(app), timeout=40)
    assert app.service.process.wait(timeout=10) == 0
    # The search was stopped, so the bot closed its browser, before the app quit.
    assert "Deteniendo la búsqueda" in app.service.log_text()


def test_after_an_update_the_window_reconnects_and_still_closes_the_app(
    make_app: Callable[..., App], activation: Activation, github: FakeGitHub, pages: Pages
) -> None:
    release(github, "2.1.0")
    activation.publish("2.1.0")
    app = make_app(activation, extra_env=QUIT_WHEN_CLOSED)
    page = pages.new(app.url)
    app.api("POST", "/api/update/check")
    wait_until(lambda: app.api("GET", "/api/update")[1]["state"] == "ready", timeout=60)
    page.reload()
    page.get_by_role("button", name="Actualizar").click()
    wait_until(lambda: app.version() == "2.1.0", timeout=90)
    # The new version opened no window of its own: this one came back to it.
    expect(page.locator(".update")).to_have_count(0, timeout=30_000)
    expect(page.get_by_role("heading", name="Activación")).to_be_visible()
    page.wait_for_timeout(4_000)
    assert app.version() == "2.1.0"

    page.close()
    wait_until(lambda: stopped(app), timeout=15)


def test_with_two_windows_open_closing_one_keeps_the_app(
    make_app: Callable[..., App], pages: Pages
) -> None:
    app = make_app(extra_env=QUIT_WHEN_CLOSED)
    first, second = pages.new(app.url), pages.new(app.url)
    for page in (first, second):
        expect(badge(page)).to_contain_text("En reposo")
    first.close()
    still_up_after_the_grace(app, second)
    second.close()
    wait_until(lambda: stopped(app), timeout=15)


def test_a_window_reopened_in_time_keeps_the_app(
    make_app: Callable[..., App], pages: Pages
) -> None:
    app = make_app(extra_env=QUIT_WHEN_CLOSED)
    page = pages.new(app.url)
    expect(badge(page)).to_contain_text("En reposo")
    page.close()
    again = pages.new(app.url)
    expect(badge(again)).to_contain_text("En reposo")
    still_up_after_the_grace(app, again)


def test_a_finished_search_does_not_ask_before_closing(
    make_app: Callable[..., App], pages: Pages
) -> None:
    app = make_app(extra_env=QUIT_WHEN_CLOSED)
    app.profile("Ana", "FAIL")
    page = pages.new(app.url)
    start_search(page, "Ana · FAIL")
    to_state(page, "Error")
    close_without_being_asked(page)
    wait_until(lambda: stopped(app), timeout=15)
    assert "Deteniendo la búsqueda" not in app.service.log_text()


def test_closing_with_a_seat_reserved_asks_then_stops_the_search(
    make_app: Callable[..., App], pages: Pages
) -> None:
    """The costliest case: the bot's browser holds the purchase the user is completing."""
    app = make_app(extra_env=QUIT_WHEN_CLOSED)
    app.profile("Ana", "SESSION")
    page = pages.new(app.url)
    start_search(page, "Ana · SESSION")
    to_state(page, "Elige tren")
    page.locator(".picker__table tbody tr").filter(has_text="07:18").click()
    page.locator(".picker__confirm").click()
    to_state(page, "Plaza reservada")

    with page.expect_event("dialog") as asked:
        page.close(run_before_unload=True)
    asked.value.dismiss()
    still_up_after_the_grace(app, page)
    to_state(page, "Plaza reservada")

    with page.expect_event("dialog") as asked:
        page.close(run_before_unload=True)
    asked.value.accept()
    wait_until(lambda: stopped(app), timeout=40)
    assert app.service.process.wait(timeout=10) == 0
    assert "Deteniendo la búsqueda" in app.service.log_text()


def test_a_rolled_back_update_keeps_the_window_and_still_closes_with_it(
    make_app: Callable[..., App], activation: Activation, github: FakeGitHub, pages: Pages
) -> None:
    release(github, "2.1.0", broken=True)
    activation.publish("2.1.0")
    app = make_app(activation, extra_env=QUIT_WHEN_CLOSED)
    page = pages.new(app.url)
    expect(page.locator(".update")).to_contain_text("2.1.0 lista.", timeout=60_000)
    page.get_by_role("button", name="Actualizar").click()
    expect(page.locator(".update")).to_contain_text("no arrancó", timeout=90_000)
    assert app.version() == "2.0.0"
    still_up_after_the_grace(app, page)
    page.close()
    wait_until(lambda: stopped(app), timeout=15)


def test_a_window_closed_during_an_update_lets_the_new_version_quit(
    make_app: Callable[..., App], activation: Activation, github: FakeGitHub, pages: Pages
) -> None:
    release(github, "2.1.0")
    activation.publish("2.1.0")
    app = make_app(
        activation, extra_env={**QUIT_WHEN_CLOSED, "E2E_RECONNECT_ALLOWANCE": "4"}
    )
    page = pages.new(app.url)
    expect(page.locator(".update")).to_contain_text("2.1.0 lista.", timeout=60_000)
    page.get_by_role("button", name="Actualizar").click()
    page.close()
    wait_until(lambda: app.version() == "2.1.0", timeout=90)
    # Nobody came back to it: after the allowance and the grace, it goes too.
    wait_until(lambda: stopped(app), timeout=30)


def test_a_second_launch_leaves_the_running_app_alone(
    make_app: Callable[..., App], pages: Pages
) -> None:
    app = make_app(extra_env=QUIT_WHEN_CLOSED)
    page = pages.new(app.url)
    expect(badge(page)).to_contain_text("En reposo")
    second = subprocess.run(
        [BACKEND_PYTHON, str(HARNESS)],
        env={
            **os.environ,
            "E2E_DATA": str(app.data),
            "RENFE_PORT": str(app.port),
            "E2E_EXE": "",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert second.returncode == 0, second.stderr
    assert "ya estaba en marcha" in second.stdout
    still_up_after_the_grace(app, page)
