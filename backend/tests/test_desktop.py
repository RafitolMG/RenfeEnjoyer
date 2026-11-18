import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from app import desktop, window
from app.main import app


@pytest.fixture(autouse=True)
def fresh_window_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(desktop.QUIT_WHEN_CLOSED, raising=False)
    monkeypatch.setattr(window, "state", window.WindowState())
    monkeypatch.setattr(desktop, "_quitting", threading.Event())


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Windows the launcher opens; never a real one, and never the native one."""
    urls: list[str] = []
    monkeypatch.setattr(desktop, "OPEN_WINDOW", True)
    monkeypatch.setattr(window, "native_available", lambda: False)
    monkeypatch.setattr(window, "open_window", lambda url: urls.append(url) or "app")
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


def test_a_second_launch_only_opens_a_window(
    monkeypatch: pytest.MonkeyPatch, opened: list[str]
) -> None:
    monkeypatch.setattr(desktop, "is_running", lambda: True)
    monkeypatch.setattr(desktop, "focus_running", lambda: False)
    monkeypatch.setattr(
        desktop.uvicorn, "Server", lambda *_: pytest.fail("started a second server")
    )
    assert desktop.main() == 0
    assert opened == [desktop.URL]


def test_a_second_launch_brings_the_native_window_back(
    monkeypatch: pytest.MonkeyPatch, opened: list[str]
) -> None:
    monkeypatch.setattr(desktop, "is_running", lambda: True)
    monkeypatch.setattr(desktop, "focus_running", lambda: True)
    assert desktop.main() == 0
    assert opened == []


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


def test_https_trusts_the_systems_certificates() -> None:
    from app.config import ca_bundle

    fedora = "/etc/pki/tls/certs/ca-bundle.crt"
    assert ca_bundle({}, exists=lambda path: path == fedora) == fedora
    # A minimal system without any falls back to certifi's.
    assert ca_bundle({}, exists=lambda path: False).endswith("cacert.pem")
    # What the user chose wins.
    assert ca_bundle({"SSL_CERT_FILE": "/mine.pem"}, exists=lambda path: True) is None


def test_a_window_of_another_process_makes_the_app_quit_when_it_closes(
    opened: list[str],
) -> None:
    desktop.open_window()
    assert opened == [desktop.URL]
    assert desktop.quit_when_closed()
    # The SPA asks before closing during a search, and the next version inherits it.
    assert window.state.closes_app


class _FakeServer:
    should_exit = False


def _watch(
    monkeypatch: pytest.MonkeyPatch, streams: list[int], after_update: bool = False
) -> tuple[list[int], float]:
    """Run the watcher over a sequence of connected-page counts; when it quit, if."""
    monkeypatch.setenv(desktop.QUIT_WHEN_CLOSED, "1")
    monkeypatch.setattr(desktop, "WATCH_EVERY", 0.01)
    monkeypatch.setattr(desktop, "AFTER_UPDATE", after_update)
    monkeypatch.setattr(desktop, "RECONNECT_ALLOWANCE", 0.2)
    monkeypatch.setattr(window, "CLOSED_GRACE", 0.1)
    server = _FakeServer()
    counts = iter(streams)
    monkeypatch.setattr(
        desktop.job_manager, "stream_count", lambda: next(counts, streams[-1])
    )
    quits: list[int] = []

    def fake_quit() -> None:
        quits.append(1)
        server.should_exit = True

    monkeypatch.setattr(desktop, "quit_app", fake_quit)
    started = time.monotonic()
    thread = threading.Thread(target=desktop._watch_window, args=(server,))
    thread.start()
    thread.join(timeout=5)
    server.should_exit = True
    return quits, time.monotonic() - started


def test_the_app_quits_once_its_window_has_been_closed_for_a_while(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No page yet (the window is opening), one, a reload, then none for good.
    quits, _ = _watch(monkeypatch, [0] * 30 + [1] * 5 + [0] * 3 + [1] * 5 + [0])
    assert quits == [1]


def test_a_reload_does_not_quit_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    quits, _ = _watch(monkeypatch, [1, 0, 0, 1, 0, 1] + [1] * 200 + [1])
    assert quits == []


def test_without_any_window_the_app_waits_for_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quits, _ = _watch(monkeypatch, [0] * 100 + [1])
    assert quits == []


def test_after_an_update_a_window_that_never_reconnects_counts_as_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quits, took = _watch(monkeypatch, [0], after_update=True)
    assert quits == [1]
    assert took >= 0.3  # the allowance, then the grace


def test_quitting_stops_the_search_first(monkeypatch: pytest.MonkeyPatch) -> None:
    active = [True]
    calls: list[str] = []

    def cancel() -> None:
        calls.append("cancel")
        active[0] = False

    monkeypatch.setattr(desktop.job_manager, "is_active", lambda: active[0])
    monkeypatch.setattr(desktop.job_manager, "cancel", cancel)
    monkeypatch.setattr(
        desktop.job_manager, "close_streams", lambda: calls.append("close")
    )
    server = _FakeServer()
    monkeypatch.setattr(desktop, "_server", server)
    desktop.quit_app()
    assert calls == ["cancel", "close"]
    assert server.should_exit


def test_the_spa_learns_whether_closing_quits_the_app() -> None:
    with TestClient(app) as client:
        assert client.get("/api/window").json() == {"closes_app": False}
        window.state.closes_app = True
        assert client.get("/api/window").json() == {"closes_app": True}


def test_a_second_launch_can_bring_the_native_window_back() -> None:
    focused: list[int] = []
    with TestClient(app) as client:
        assert client.post("/api/window/focus").status_code == 404
        window.state.focus = lambda: focused.append(1)
        assert client.post("/api/window/focus").status_code == 204
    assert focused == [1]


def test_the_app_window_uses_a_chromium_browser_in_app_mode() -> None:
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name == "chromium" else None

    assert window.find_app_browser("linux", {}, which) == "/usr/bin/chromium"
    assert window.find_app_browser("linux", {}, lambda name: None) is None
    chosen = {"RENFE_APP_BROWSER": "/opt/brave"}
    assert window.find_app_browser("linux", chosen, which) == "/opt/brave"
    command = window.app_window_command("/usr/bin/chromium", "http://x", Path("/p"))
    assert "--app=http://x" in command
    assert f"--user-data-dir={Path('/p')}" in command


def test_on_windows_edge_opens_the_app_window_when_chrome_is_missing() -> None:
    environ = {"PROGRAMFILES(X86)": r"C:\Program Files (x86)", "PROGRAMFILES": r"C:\PF"}
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    found = window.find_app_browser(
        "win32", environ, lambda name: None, lambda path: path == edge
    )
    assert found == edge


class _FakeWebview:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def create_confirmation_dialog(self, title: str, message: str) -> bool:
        self.asked.append(message)
        return self.answer


@pytest.mark.parametrize("answer", [True, False])
def test_closing_the_native_window_during_a_search_asks_first(answer: bool) -> None:
    native = window.NativeWindow("http://x", confirm_close=lambda: True)
    fake = _FakeWebview(answer)
    assert native._on_closing(fake) is answer
    assert "búsqueda en curso" in fake.asked[0]


def test_closing_the_native_window_while_idle_just_closes() -> None:
    native = window.NativeWindow("http://x", confirm_close=lambda: False)
    fake = _FakeWebview(False)
    assert native._on_closing(fake) is True
    assert fake.asked == []


def test_without_webview2_the_native_window_steps_aside() -> None:
    native = window.NativeWindow("http://x", confirm_close=lambda: False)
    assert native._check_renderer("mshtml") is False
    assert native._check_renderer("edgechromium") is True


def test_a_windowed_executable_writes_to_a_log_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "renfe-enjoyer.log"
    monkeypatch.setattr(desktop, "LOG_PATH", log)
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    stream: Any = desktop._attach_log()
    try:
        desktop._error("algo falló")
    finally:
        stream.close()
    assert "algo falló" in log.read_text(encoding="utf-8")


# --- the native window (Windows), with a stand-in for pywebview -----------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _FakeNative:
    """Stands in for window.NativeWindow; `outcome` is what the user does with it."""

    instances: ClassVar[list["_FakeNative"]] = []

    def __init__(self, url: str, confirm_close: Any, outcome: str) -> None:
        self.url = url
        self.confirm_close = confirm_close
        self.outcome = outcome
        self.closed = threading.Event()
        self.focus_during_run: Any = None
        self.ran = False
        _FakeNative.instances.append(self)

    def run(self) -> bool:
        self.ran = True
        self.focus_during_run = window.state.focus
        if self.outcome == "no-webview2":
            return False
        if self.outcome == "user-closes":
            # The page is up: the server answers by the time the window shows.
            assert desktop.is_running()
            return True
        # "server-closes": the window stays until the server side closes it.
        assert self.closed.wait(10)
        return True

    def close(self) -> None:
        self.closed.set()

    def focus(self) -> None: ...


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch, opened: list[str]) -> Callable[[str], None]:
    """After `opened`, which turns the native window off."""
    port = _free_port()
    monkeypatch.setattr(desktop, "PORT", port)
    monkeypatch.setattr(desktop, "URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(desktop, "OPEN_WINDOW", True)
    monkeypatch.setattr(desktop, "AFTER_UPDATE", False)
    monkeypatch.setattr(window, "native_available", lambda: True)
    _FakeNative.instances = []

    def use(outcome: str) -> None:
        monkeypatch.setattr(
            window,
            "NativeWindow",
            lambda url, confirm_close: _FakeNative(url, confirm_close, outcome),
        )

    return use


def test_closing_the_native_window_stops_the_server(
    native: Callable[[str], None], opened: list[str]
) -> None:
    native("user-closes")
    assert desktop.main() == 0
    fake = _FakeNative.instances[0]
    assert fake.url == desktop.URL
    assert not desktop.is_running()
    # A second launch could bring it to the front while it was up, not after.
    assert fake.focus_during_run == fake.focus
    assert window.state.focus is None
    # It asks before closing during a search: the job manager is what it asks.
    assert fake.confirm_close() is False
    assert opened == []
    assert not desktop.quit_when_closed()


def test_without_webview2_the_app_opens_in_app_mode_and_quits_with_it(
    native: Callable[[str], None], opened: list[str]
) -> None:
    native("no-webview2")
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(desktop.main()))
    thread.start()
    deadline = time.monotonic() + 10
    while not opened and time.monotonic() < deadline:
        time.sleep(0.05)
    assert opened == [desktop.URL]
    assert desktop.quit_when_closed()
    assert window.state.closes_app
    assert desktop.is_running()
    desktop.quit_app()  # what the watcher does once that window closes
    thread.join(timeout=15)
    assert result == [0]


def test_a_server_that_cannot_start_shows_no_window(
    native: Callable[[str], None], busy_port: int, opened: list[str]
) -> None:
    native("user-closes")
    assert desktop.main() == 1
    assert _FakeNative.instances[0].ran is False
    assert "RENFE_PORT" in (desktop._last_error or "")


def test_when_the_server_ends_by_itself_it_closes_the_native_window(
    native: Callable[[str], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a handover: the new version shows its own window, this one goes."""
    native("server-closes")

    def handed_over(on_started: Any) -> int:
        on_started()
        return 0

    monkeypatch.setattr(desktop, "serve_until_closed", handed_over)
    assert desktop.run_native() == 0
    assert _FakeNative.instances[0].closed.is_set()


def test_a_server_crash_in_native_mode_is_reported(
    native: Callable[[str], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    native("user-closes")

    def crash(on_started: Any) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(desktop, "serve_until_closed", crash)
    assert desktop.run_native() == 1
    assert _FakeNative.instances[0].ran is False
    assert desktop._last_error == "Error inesperado del servidor"


# --- main(): which window, if any -----------------------------------------------------


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """What main() hands serve_until_closed, instead of serving."""
    calls: list[Any] = []
    monkeypatch.setattr(desktop, "is_running", lambda: False)
    monkeypatch.setattr(window, "native_available", lambda: False)
    monkeypatch.setattr(
        desktop, "serve_until_closed", lambda show: calls.append(show) or 0
    )
    monkeypatch.setattr(desktop, "AFTER_UPDATE", False)
    return calls


def test_the_app_opens_its_window_once_the_server_is_up(
    monkeypatch: pytest.MonkeyPatch, served: list[Any]
) -> None:
    monkeypatch.setattr(desktop, "OPEN_WINDOW", True)
    assert desktop.main() == 0
    assert served == [desktop.open_window]


def test_without_windows_the_app_opens_none_and_never_quits_by_itself(
    monkeypatch: pytest.MonkeyPatch, served: list[Any]
) -> None:
    monkeypatch.setattr(desktop, "OPEN_WINDOW", False)
    monkeypatch.setattr(window, "native_available", lambda: True)
    assert desktop.main() == 0
    assert served == [None]
    assert not window.state.closes_app


def test_the_version_an_update_started_opens_no_second_window(
    monkeypatch: pytest.MonkeyPatch, served: list[Any]
) -> None:
    monkeypatch.setattr(desktop, "OPEN_WINDOW", True)
    monkeypatch.setattr(desktop, "AFTER_UPDATE", True)
    monkeypatch.setenv(desktop.QUIT_WHEN_CLOSED, "1")
    assert desktop.main() == 0
    assert served == [None]
    # It inherited the rule, so the SPA keeps asking before closing.
    assert window.state.closes_app


def test_the_native_window_is_used_wherever_it_is_available(
    monkeypatch: pytest.MonkeyPatch, served: list[Any]
) -> None:
    monkeypatch.setattr(desktop, "OPEN_WINDOW", True)
    monkeypatch.setattr(window, "native_available", lambda: True)
    monkeypatch.setattr(desktop, "run_native", lambda: 7)
    assert desktop.main() == 7
    assert served == []


def test_a_second_launch_without_windows_opens_nothing(
    monkeypatch: pytest.MonkeyPatch, opened: list[str]
) -> None:
    monkeypatch.setattr(desktop, "OPEN_WINDOW", False)
    monkeypatch.setattr(desktop, "is_running", lambda: True)
    monkeypatch.setattr(desktop, "focus_running", lambda: pytest.fail("asked to focus"))
    assert desktop.main() == 0
    assert opened == []


# --- serving through updates ----------------------------------------------------------


@pytest.fixture
def handovers(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[Any]]:
    def setup(results: list[int | None], succeeds: bool) -> list[Any]:
        shown: list[Any] = []
        sequence = iter(results)

        def fake_serve(on_started: Any = None) -> int | None:
            shown.append(on_started)
            return next(sequence)

        monkeypatch.setattr(desktop, "serve", fake_serve)
        monkeypatch.setattr(desktop.updater, "handover", lambda port: succeeds)
        monkeypatch.setattr(
            desktop.updater, "state", lambda: {"error": "La versión 9 no arrancó"}
        )
        return shown

    return setup


def test_a_successful_handover_ends_this_process(
    handovers: Callable[..., list[Any]],
) -> None:
    shown = handovers([None], succeeds=True)
    assert desktop.serve_until_closed(desktop.open_window) == 0
    assert shown == [desktop.open_window]


def test_a_rolled_back_update_serves_again_without_another_window(
    handovers: Callable[..., list[Any]],
) -> None:
    shown = handovers([None, 0], succeeds=False)
    assert desktop.serve_until_closed(desktop.open_window) == 0
    assert shown == [desktop.open_window, None]
    assert desktop._last_error == "La versión 9 no arrancó"


def test_a_window_closed_during_a_failed_handover_does_not_serve_again(
    handovers: Callable[..., list[Any]],
) -> None:
    shown = handovers([None, 0], succeeds=False)
    desktop._quitting.set()
    assert desktop.serve_until_closed(None) == 0
    assert shown == [None]


# --- what a windowed executable does when it fails ------------------------------------


@pytest.mark.parametrize(
    ("windowed", "platform", "tty", "expected"),
    [
        (True, "win32", False, "box"),
        (False, "linux", True, "enter"),
        (False, "linux", False, "nothing"),
    ],
)
def test_a_failed_start_is_shown_where_the_user_looks(
    monkeypatch: pytest.MonkeyPatch,
    windowed: bool,
    platform: str,
    tty: bool,
    expected: str,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(desktop, "FROZEN", True)
    monkeypatch.setattr(desktop, "_attach_log", lambda: object() if windowed else None)
    monkeypatch.setattr(desktop.sys, "platform", platform)
    monkeypatch.setattr(desktop.sys, "argv", ["RenfeEnjoyer"])

    def fail() -> int:
        desktop._error("puerto ocupado")
        return 1

    monkeypatch.setattr(desktop, "main", fail)
    monkeypatch.setattr(
        desktop, "_show_error", lambda message: seen.append(f"box:{message}")
    )
    monkeypatch.setattr("builtins.input", lambda prompt: seen.append("enter") or "")

    class _Stdin:
        def isatty(self) -> bool:
            return tty

    monkeypatch.setattr(desktop.sys, "stdin", _Stdin())
    with pytest.raises(SystemExit) as exit_info:
        desktop.run()
    assert exit_info.value.code == 1
    assert (
        seen
        == {
            "box": ["box:puerto ocupado"],
            "enter": ["enter"],
            "nothing": [],
        }[expected]
    )


def test_the_window_check_needs_a_native_window(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(window, "native_available", lambda: False)
    assert desktop.check_window() == 1
    monkeypatch.setattr(window, "native_available", lambda: True)

    def broken() -> str:
        raise RuntimeError("sin WebView2")

    monkeypatch.setattr(window, "check_native", broken)
    assert desktop.check_window() == 1
    assert "sin WebView2" in capsys.readouterr().err
    monkeypatch.setattr(window, "check_native", lambda: "edgechromium")
    assert desktop.check_window() == 0


def test_the_log_is_rotated_before_it_grows_too_big(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "renfe-enjoyer.log"
    log.write_text("x" * 100)
    monkeypatch.setattr(desktop, "LOG_PATH", log)
    monkeypatch.setattr(desktop, "LOG_LIMIT", 10)
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    stream: Any = desktop._attach_log()
    stream.close()
    assert (tmp_path / "renfe-enjoyer.log.1").read_text() == "x" * 100
    # The new log starts afresh (its header names the command, which may hold an x).
    assert "x" * 100 not in log.read_text()


def test_a_console_needs_no_log_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(desktop, "LOG_PATH", tmp_path / "renfe-enjoyer.log")
    assert desktop._attach_log() is None
    assert not (tmp_path / "renfe-enjoyer.log").exists()


# --- opening a window of another process ----------------------------------------------


def test_the_app_window_gets_its_own_profile_and_the_desktop_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[dict[str, Any]] = []

    def popen(command: list[str], **kwargs: Any) -> None:
        started.append({"command": command, **kwargs})

    monkeypatch.setattr(window.subprocess, "Popen", popen)
    monkeypatch.setattr(
        "app.bot.driver.browser_environment", lambda: {"WAYLAND_DISPLAY": "wayland-1"}
    )
    assert window.open_app_window("http://x", browser="/usr/bin/chromium") is True
    assert started[0]["command"][:2] == ["/usr/bin/chromium", "--app=http://x"]
    assert f"--user-data-dir={window.WINDOW_PROFILE}" in started[0]["command"]
    assert started[0]["env"] == {"WAYLAND_DISPLAY": "wayland-1"}


def test_a_browser_that_will_not_start_falls_back_to_a_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tabs: list[str] = []

    def popen(command: list[str], **kwargs: Any) -> None:
        raise OSError("not executable")

    monkeypatch.setattr(window.subprocess, "Popen", popen)
    monkeypatch.setattr(window, "find_app_browser", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(window.webbrowser, "open", tabs.append)
    assert window.open_window("http://x") == "browser"
    assert tabs == ["http://x"]


def test_with_no_chromium_browser_the_app_opens_in_a_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tabs: list[str] = []
    monkeypatch.setattr(window, "find_app_browser", lambda: None)
    monkeypatch.setattr(window.webbrowser, "open", tabs.append)
    assert window.open_window("http://x") == "browser"
    assert tabs == ["http://x"]


def test_on_windows_with_no_browser_installed_none_is_found() -> None:
    assert (
        window.find_app_browser("win32", {}, lambda name: None, lambda p: False) is None
    )
    found = window.find_app_browser(
        "win32", {}, {"msedge": "C:/e.exe"}.get, lambda p: False
    )
    assert found == "C:/e.exe"
    assert (
        window.find_app_browser("darwin", {}, lambda name: "/x", lambda p: True) is None
    )


def test_the_native_window_is_only_for_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window.sys, "platform", "linux")
    assert window.native_available() is False


# --- a second launch, against a real server -------------------------------------------


def test_a_second_launch_finds_out_whether_there_is_a_window_to_bring_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uvicorn

    port = _free_port()
    monkeypatch.setattr(desktop, "URL", f"http://127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run)
    thread.start()
    try:
        while not server.started:
            time.sleep(0.05)
        assert desktop.is_running()
        assert desktop.focus_running() is False
        focused: list[int] = []
        window.state.focus = lambda: focused.append(1)
        assert desktop.focus_running() is True
        assert focused == [1]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
