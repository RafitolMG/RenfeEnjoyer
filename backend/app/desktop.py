"""Desktop launcher: serve the app on this computer only and open it in the browser.

This is what the Windows executable runs (`packaging/renfe-enjoyer.spec`); from a
checkout it works the same with `python -m app.desktop`. Unlike
`scripts/serve-tailscale.sh` it binds to loopback, so the app is reachable from this
computer and nowhere else. Starting it while it is already up just opens the browser
again, so double-clicking the executable twice is harmless.
"""

import json
import os
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser

import uvicorn

from app.config import DATABASE_PATH, FROZEN
from app.main import app

HOST = "127.0.0.1"
PORT = int(os.getenv("RENFE_PORT", "8000"))
URL = f"http://{HOST}:{PORT}"
# Lets CI start the executable where there is no browser to open.
OPEN_BROWSER = os.getenv("RENFE_NO_BROWSER", "").lower() not in {"1", "true", "yes"}


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


def _announce_when_ready(server: uvicorn.Server) -> None:
    while not server.started:
        if server.should_exit:
            return
        time.sleep(0.1)
    print(f"Renfe Enjoyer en marcha: {URL}", flush=True)
    print(f"Perfiles guardados en {DATABASE_PATH}", flush=True)
    print(
        "Solo se puede abrir desde este equipo. Cierra esta ventana para pararlo.",
        flush=True,
    )
    open_browser()


def main() -> int:
    if is_running():
        print(f"Renfe Enjoyer ya estaba en marcha: {URL}")
        open_browser()
        return 0

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=_announce_when_ready, args=(server,), daemon=True).start()
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
    return 0


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
