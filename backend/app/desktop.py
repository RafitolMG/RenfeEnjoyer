"""Desktop launcher: serve the app on this computer only and open it in the browser.

This is what the Windows executable runs (`packaging/renfe-enjoyer.spec`); from a
checkout it works the same with `python -m app.desktop`. Unlike
`scripts/serve-tailscale.sh` it binds to loopback, so the app is reachable from this
computer and nowhere else. Starting it while it is already up just opens the browser
again, so double-clicking the executable twice is harmless.

It also drives self-updates (`app/updater.py`): when the user installs one, the server
stops, the new executable takes over the port, and this process exits, or, if the new
one never answers, serves again as before.
"""

import json
import os
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from types import FrameType

import uvicorn

from app.bot.runner import job_manager
from app.config import APP_VERSION, DATABASE_PATH, FROZEN
from app.main import app
from app.updater import updater

HOST = "127.0.0.1"
PORT = int(os.getenv("RENFE_PORT", "8000"))
URL = f"http://{HOST}:{PORT}"
# Lets CI start the executable where there is no browser to open.
OPEN_BROWSER = os.getenv("RENFE_NO_BROWSER", "").lower() not in {"1", "true", "yes"}
# Started by the previous version after an update: its page reloads itself instead.
AFTER_UPDATE = "--after-update" in sys.argv


def is_running() -> bool:
    """Whether this app, not just anything, already answers on the port."""
    try:
        with urllib.request.urlopen(f"{URL}/api/health", timeout=1) as response:
            return bool(json.load(response).get("status") == "ok")
    except (OSError, ValueError):
        return False


def open_browser() -> None:
    if OPEN_BROWSER:
        webbrowser.open(URL)


def _announce_when_ready(server: uvicorn.Server, browse: bool) -> None:
    while not server.started:
        if server.should_exit:
            return
        time.sleep(0.1)
    print(f"Renfe Enjoyer {APP_VERSION} en marcha: {URL}", flush=True)
    print(f"Perfiles guardados en {DATABASE_PATH}", flush=True)
    print(
        "Solo se puede abrir desde este equipo. Cierra esta ventana para pararlo.",
        flush=True,
    )
    if browse:
        open_browser()


class _Server(uvicorn.Server):
    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        # Open job streams would otherwise hold up the graceful shutdown (Ctrl+C).
        job_manager.close_streams()
        super().handle_exit(sig, frame)


def serve(browse: bool) -> int | None:
    """Run the server until it stops. None when it stopped to hand over to an update."""
    # Job streams are closed first; the timeout only bounds anything else left open.
    config = uvicorn.Config(
        app, host=HOST, port=PORT, log_level="warning", timeout_graceful_shutdown=5
    )
    server = _Server(config)

    def stop() -> None:
        job_manager.close_streams()
        server.should_exit = True

    updater.stop_server = stop
    threading.Thread(
        target=_announce_when_ready, args=(server, browse), daemon=True
    ).start()
    try:
        server.run()
    except SystemExit:
        # uvicorn exits this way when it cannot bind, after logging the OS error.
        if server.started:
            raise
        print(
            f"No se pudo abrir el puerto {PORT}: lo está usando otro programa. "
            "Ciérralo o elige otro con la variable de entorno RENFE_PORT.",
            file=sys.stderr,
        )
        return 1
    finally:
        updater.stop_server = None
    return None if updater.handover_requested else 0


def main() -> int:
    if is_running():
        print(f"Renfe Enjoyer ya estaba en marcha: {URL}")
        open_browser()
        return 0

    browse = not AFTER_UPDATE
    while (code := serve(browse)) is None:
        print("Instalando la actualización…", flush=True)
        if updater.handover(PORT):
            return 0
        print(updater.state()["error"], file=sys.stderr, flush=True)
        # The page is still open and reconnects by itself.
        browse = False
    return code


def run() -> None:
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 1
    if code and FROZEN:
        # A double-clicked console window closes the moment the process ends, taking
        # the error message with it.
        input("Pulsa Enter para cerrar...")
    sys.exit(code)


if __name__ == "__main__":
    run()
