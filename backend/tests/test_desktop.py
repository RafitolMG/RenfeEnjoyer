import socket
from collections.abc import Iterator

import pytest

from app import desktop


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(desktop, "OPEN_BROWSER", True)
    monkeypatch.setattr(desktop.webbrowser, "open", urls.append)
    return urls


@pytest.fixture
def busy_port(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """A port some other program holds: it accepts connections but is not the app."""
    with socket.socket() as listener:
        # Without it Windows lets uvicorn's SO_REUSEADDR socket bind the same port, and
        # the server under test would start and never return.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(desktop, "PORT", port)
        monkeypatch.setattr(desktop, "URL", f"http://127.0.0.1:{port}")
        yield port


def test_a_second_launch_only_opens_the_browser(
    monkeypatch: pytest.MonkeyPatch, opened: list[str]
) -> None:
    monkeypatch.setattr(desktop, "is_running", lambda: True)
    monkeypatch.setattr(
        desktop.uvicorn, "Server", lambda *_: pytest.fail("started a second server")
    )
    assert desktop.main() == 0
    assert opened == [desktop.URL]


def test_another_program_on_the_port_is_not_taken_for_the_app(busy_port: int) -> None:
    assert not desktop.is_running()


def test_a_busy_port_is_reported_instead_of_crashing(
    busy_port: int, opened: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert desktop.main() == 1
    assert "RENFE_PORT" in capsys.readouterr().err
    assert opened == []


def test_programs_started_by_the_bundle_get_the_system_libraries() -> None:
    from app.config import system_environment

    bundled = {"LD_LIBRARY_PATH": "/tmp/_MEI123", "PATH": "/usr/bin"}
    assert system_environment(bundled) == {"PATH": "/usr/bin"}
    kept = {"LD_LIBRARY_PATH": "/tmp/_MEI123", "LD_LIBRARY_PATH_ORIG": "/opt/lib"}
    assert system_environment(kept) == {"LD_LIBRARY_PATH": "/opt/lib"}
    assert system_environment({"PATH": "/usr/bin"}) == {"PATH": "/usr/bin"}


def test_the_browser_check_reports_a_browser_that_will_not_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(profile_dir: object, headless: bool | None = None) -> None:
        raise RuntimeError("chrome exploded")

    monkeypatch.setattr("app.bot.driver.build_chrome_driver", broken)
    assert desktop.check_browser() == 1
    assert "chrome exploded" in capsys.readouterr().err
