"""Desktop launcher: serve the app on this computer only and show it in its own window.

This is what the executables run (`packaging/renfe-enjoyer.spec`); from a checkout it
works the same with `python -m app.desktop`. Unlike `scripts/serve-tailscale.sh` it
binds to loopback, so the app is reachable from this computer and nowhere else.
Starting it while it is already up just brings its window back, so double-clicking the
executable twice is harmless.

The window (`app/window.py`) is the app's on/off switch: closing it quits. On Windows
it is a native window this process owns, and the executable has no console; elsewhere
it is a browser in app mode, and the server quits once no page has been connected for
a while.

It also drives self-updates (`app/updater.py`): when the user installs one, the server
stops, the new executable takes over the port, and this process exits, or, if the new
one never answers, serves again as before.
"""

import contextlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import TextIO

import uvicorn

from app import window
from app.bot.runner import JobConflict, job_manager
from app.config import APP_VERSION, DATA_DIR, DATABASE_PATH, FROZEN
from app.main import app
from app.updater import updater

HOST = "127.0.0.1"
PORT = int(os.getenv("RENFE_PORT", "8000"))
URL = f"http://{HOST}:{PORT}"
# Lets CI and the end-to-end suite start the app where there is no window to open.
OPEN_WINDOW = os.getenv("RENFE_NO_BROWSER", "").lower() not in {"1", "true", "yes"}
# Started by the previous version after an update: its window reconnects by itself.
AFTER_UPDATE = "--after-update" in sys.argv
# Set once the app shows in a window of another process, and inherited by the version an
# update starts, so that one also quits when the window is closed.
QUIT_WHEN_CLOSED = "RENFE_QUIT_WHEN_CLOSED"
# How long the version an update started waits for the window to reconnect before it
# takes it for closed.
RECONNECT_ALLOWANCE = 60.0
WATCH_EVERY = 0.5
# A windowed executable has no console; what it would print goes here.
LOG_PATH = DATA_DIR / "renfe-enjoyer.log"
LOG_LIMIT = 1_000_000

_server: uvicorn.Server | None = None
_quitting = threading.Event()
# The last thing that went wrong, for the message a windowed executable shows on exit.
_last_error: str | None = None


def _error(message: str) -> None:
    global _last_error
    _last_error = message
    print(message, file=sys.stderr, flush=True)


def is_running() -> bool:
    """Whether this app, not just anything, already answers on the port."""
    try:
        with urllib.request.urlopen(f"{URL}/api/health", timeout=1) as response:
            return bool(json.load(response).get("status") == "ok")
    except (OSError, ValueError):
        return False


def focus_running() -> bool:
    """Ask the instance already running to bring its native window to the front."""
    request = urllib.request.Request(f"{URL}/api/window/focus", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=2):
            return True
    except (OSError, ValueError):
        return False


def quit_when_closed() -> bool:
    return os.environ.get(QUIT_WHEN_CLOSED) == "1"


def open_window() -> None:
    """Show the app in a window of another process, and quit when it is closed."""
    os.environ[QUIT_WHEN_CLOSED] = "1"
    window.state.closes_app = True
    window.open_window(URL)


def quit_app() -> None:
    """Stop for good: the search first, so the bot closes its browser, then the server."""
    _quitting.set()
    if job_manager.is_active():
        print("Deteniendo la búsqueda en curso", flush=True)
        with contextlib.suppress(JobConflict):
            job_manager.cancel()
        deadline = time.monotonic() + 20
        while job_manager.is_active() and time.monotonic() < deadline:
            time.sleep(0.2)
    job_manager.close_streams()
    server = _server
    if server is not None:
        server.should_exit = True


def _watch_window(server: uvicorn.Server) -> None:
    """Quit once no page has followed the job stream for `window.CLOSED_GRACE` seconds.

    Only after one has: the window takes a while to open. The version an update started
    did not open one; it gives the old window `RECONNECT_ALLOWANCE` to come back.
    """
    armed_at = time.monotonic() + RECONNECT_ALLOWANCE if AFTER_UPDATE else None
    empty_since: float | None = None
    while not server.should_exit:
        time.sleep(WATCH_EVERY)
        if not quit_when_closed():
            continue
        now = time.monotonic()
        if job_manager.stream_count():
            armed_at, empty_since = now, None
            continue
        if armed_at is None or now < armed_at:
            continue
        empty_since = empty_since or now
        if now - empty_since >= window.CLOSED_GRACE:
            print("Ventana cerrada: se cierra Renfe Enjoyer", flush=True)
            quit_app()
            return


def _announce_when_ready(
    server: uvicorn.Server, on_started: Callable[[], None] | None
) -> None:
    while not server.started:
        if server.should_exit:
            return
        time.sleep(0.1)
    print(f"Renfe Enjoyer {APP_VERSION} en marcha: {URL}", flush=True)
    print(f"Perfiles guardados en {DATABASE_PATH}", flush=True)
    if sys.stdout is not None and sys.stdout.isatty():
        print("Cierra su ventana o pulsa Ctrl+C para pararlo.", flush=True)
    if on_started is not None:
        on_started()


class _Server(uvicorn.Server):
    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        # Open job streams would otherwise hold up the graceful shutdown (Ctrl+C).
        job_manager.close_streams()
        super().handle_exit(sig, frame)


def serve(on_started: Callable[[], None] | None = None) -> int | None:
    """Run the server until it stops. None when it stopped to hand over to an update."""
    global _server
    # Job streams are closed first; the timeout only bounds anything else left open.
    config = uvicorn.Config(
        app, host=HOST, port=PORT, log_level="warning", timeout_graceful_shutdown=5
    )
    server = _Server(config)

    def stop() -> None:
        job_manager.close_streams()
        server.should_exit = True

    updater.stop_server = stop
    _server = server
    threading.Thread(
        target=_announce_when_ready, args=(server, on_started), daemon=True
    ).start()
    threading.Thread(target=_watch_window, args=(server,), daemon=True).start()
    try:
        server.run()
    except SystemExit:
        # uvicorn exits this way when it cannot bind, after logging the OS error.
        if server.started:
            raise
        _error(
            f"No se pudo abrir el puerto {PORT}: lo está usando otro programa. "
            "Ciérralo o elige otro con la variable de entorno RENFE_PORT."
        )
        return 1
    finally:
        # Also ends the watcher, which a server that never bound would leave running.
        server.should_exit = True
        updater.stop_server = None
        _server = None
    return None if updater.handover_requested else 0


def serve_until_closed(on_started: Callable[[], None] | None) -> int:
    """Serve, handing over to each update the user installs, until the app stops."""
    while (code := serve(on_started)) is None:
        print("Instalando la actualización…", flush=True)
        if updater.handover(PORT):
            return 0
        _error(str(updater.state()["error"]))
        if _quitting.is_set():
            return 0
        # The window is still open and reconnects by itself.
        on_started = None
    return code


def run_native() -> int:
    """Serve on a background thread while the native window holds the main thread."""
    result: list[int] = []
    ready = threading.Event()
    native = window.NativeWindow(URL, confirm_close=job_manager.is_active)

    def serve_in_background() -> None:
        try:
            result.append(serve_until_closed(ready.set))
        except Exception:
            traceback.print_exc()
            _error("Error inesperado del servidor")
            result.append(1)
        finally:
            ready.set()
            # After a handover the new version shows its own window.
            native.close()

    server_thread = threading.Thread(target=serve_in_background, daemon=True)
    server_thread.start()
    ready.wait()
    if result:
        return result[0]

    window.state.focus = native.focus
    try:
        shown = native.run()
    finally:
        window.state.focus = None
    if not shown:
        # No WebView2: a browser in app mode, as on Linux.
        open_window()
    elif server_thread.is_alive():
        # The user closed the window.
        quit_app()
    server_thread.join()
    return result[0] if result else 0


def main() -> int:
    if is_running():
        print(f"Renfe Enjoyer ya estaba en marcha: {URL}")
        if OPEN_WINDOW and not focus_running():
            window.open_window(URL)
        return 0

    window.state.closes_app = quit_when_closed()
    if OPEN_WINDOW and window.native_available():
        return run_native()
    show = open_window if OPEN_WINDOW and not AFTER_UPDATE else None
    return serve_until_closed(show)


def check_browser() -> int:
    """Start the bot's browser out of sight and load a page: `--check-browser`.

    Goes through the same driver the searches use, so a bundle that cannot start the
    browser (a missing hidden import, libraries leaking into it) fails here, in CI or
    on a user's computer, instead of at the first search.
    """
    from app.bot.driver import build_chrome_driver

    with tempfile.TemporaryDirectory(prefix="renfe-check-") as profile:
        try:
            driver = build_chrome_driver(Path(profile), headless=True)
            try:
                driver.get("data:text/html,<title>renfe-ok</title>")
                if driver.title != "renfe-ok":
                    raise RuntimeError(f"la página de prueba no cargó ({driver.title!r})")
                version = driver.capabilities.get("browserVersion", "?")
            finally:
                driver.quit()
        except Exception as exc:
            print(f"El navegador no funciona: {exc}", file=sys.stderr, flush=True)
            return 1
    print(f"Navegador OK: {version}", flush=True)
    return 0


def check_window() -> int:
    """Open the native window on a test page and read it back: `--check-window`."""
    if not window.native_available():
        print("Esta plataforma no tiene ventana nativa", file=sys.stderr, flush=True)
        return 1
    try:
        detail = window.check_native()
    except Exception as exc:
        print(f"La ventana no funciona: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"Ventana OK: {detail}", flush=True)
    return 0


def _attach_log() -> TextIO | None:
    """Give a windowed executable, which has no console, a file to write to instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return None
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        if LOG_PATH.stat().st_size > LOG_LIMIT:
            LOG_PATH.replace(LOG_PATH.with_name(LOG_PATH.name + ".1"))
    stream = LOG_PATH.open("a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream
    print(f"\n== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(sys.argv)}", flush=True)
    return stream


def _show_error(message: str) -> None:
    """A message box: nobody reads the log of an app that just did not open."""
    import ctypes

    MB_ICONERROR = 0x10
    ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
        None, f"{message}\n\nDetalles en {LOG_PATH}", window.TITLE, MB_ICONERROR
    )


def run() -> None:
    windowed = _attach_log() is not None
    # The console is what the user reads: time and message, not "WARNING:app.updater:".
    logging.basicConfig(
        format="%(asctime)s  %(message)s", datefmt="%H:%M", level=logging.INFO, force=True
    )
    if "--check-browser" in sys.argv:
        sys.exit(check_browser())
    if "--check-window" in sys.argv:
        sys.exit(check_window())
    try:
        code = main()
    except Exception as exc:
        traceback.print_exc()
        _error(f"Error inesperado: {exc}")
        code = 1
    if code and FROZEN:
        if windowed and sys.platform == "win32":
            _show_error(_last_error or "Renfe Enjoyer no pudo arrancar")
        elif sys.stdin is not None and sys.stdin.isatty():
            # A console opened just for this closes the moment the process ends,
            # taking the error message with it.
            input("Pulsa Enter para cerrar...")
    sys.exit(code)


if __name__ == "__main__":
    run()
