"""The real app, from source, for the end-to-end suite; started by e2e/conftest.py.

Everything is the app's own code except three stand-ins, set from the environment:

- the Renfe bot, replaced by a scripted one whose scenario is the profile's pass
  (`abono`), so the suite never touches Renfe;
- the activation server's public key and URL (E2E_PUBLIC_KEY, E2E_ACTIVATION_URL), so a
  local server with a test key can approve and offer updates; without a URL,
  activation is not required, as in a checkout;
- no window is opened (RENFE_NO_BROWSER): Playwright's page is the window, and
  RENFE_QUIT_WHEN_CLOSED makes the app quit when it closes, as after opening one;
- the executable (E2E_EXE): the updater replaces this file, and "starting the new
  build" runs this same harness as the version written inside the file, or a process
  that dies at once when the file says BROKEN.

It runs app.desktop's own serve-and-hand-over loop, so updates go through the real code.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

DATA = Path(os.environ["E2E_DATA"])
DATA.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("RENFE_DB_PATH", str(DATA / "app.db"))
os.environ.setdefault("RENFE_PROFILE_DIR", str(DATA / "browser-profile"))
os.environ.setdefault("RENFE_KEY_PATH", str(DATA / "credentials.key"))
os.environ["RENFE_NO_BROWSER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import app.config as config  # noqa: E402

# Before anything else imports it: the health check and the updater read it.
config.APP_VERSION = os.environ.get("E2E_APP_VERSION", config.APP_VERSION)

import app.activation as activation  # noqa: E402
import app.bot.runner as runner  # noqa: E402
import app.updater as updater_module  # noqa: E402
from app.bot.driver import mark_session_verified, profile_dir_for  # noqa: E402
from app.bot.errors import BotError  # noqa: E402
from app.bot.events import JobState as S  # noqa: E402
from app.bot.renfe import JobCancelled, NoTrainsListed  # noqa: E402

TRAINS = [
    {
        "departure": "06:45",
        "cells": {
            "Salida": "06:45",
            "Llegada": "07:30",
            "Duración": "45 min",
            "Tren": "13001",
            "Plazas": "Completo",
        },
    },
    {
        "departure": "07:18",
        "cells": {
            "Salida": "07:18",
            "Llegada": "08:02",
            "Duración": "44 min",
            "Tren": "13003",
            "Clase": "Estándar",
            "Plazas": "Disponible",
        },
    },
    {
        "departure": "08:10",
        "cells": {
            "Salida": "08:10",
            "Llegada": "08:55",
            "Duración": "45 min",
            "Tren": "13005",
            "Plazas": "Completo",
        },
    },
]


def _sleep(seconds: float, cancel: threading.Event) -> None:
    if cancel.wait(seconds):
        raise JobCancelled


def scripted_search(request, reporter, interaction) -> None:
    """The bot's states in the order Renfe's site drives them, per scenario.

    HAPPY: captcha, code (000000 is rejected), train choice, a few reloads, reserved.
    SESSION: a stored session skips the login. FAIL/CRASH/NOTRAINS: the failures.
    SLOW: keeps reloading until stopped.
    """
    scenario = request.abono.upper()
    cancel = interaction.cancel
    reporter.state(S.STARTING, "Abriendo el navegador")
    # The password the bot would type: proves it was decrypted, and which one is kept.
    reporter.log(f"Contraseña de {len(request.password)} caracteres")
    _sleep(0.3, cancel)
    if scenario == "CRASH":
        raise RuntimeError("chrome exploded")
    if scenario == "FAIL":
        raise BotError("Renfe ha rechazado el login: usuario o contraseña incorrectos")
    if scenario not in {"SESSION", "NOTRAINS"}:
        reporter.state(S.LOGGING_IN, "Iniciando sesión en Renfe")
        _sleep(0.3, cancel)
        reporter.state(S.AWAITING_HUMAN, "Resuelve el captcha en el navegador")
        _sleep(1.5, cancel)
        while True:
            interaction.code.request()
            reporter.state(S.AWAITING_CODE, "Introduce el código de verificación")
            code = interaction.code.wait(cancel)
            _sleep(0.2, cancel)
            if code == "000000":
                reporter.log("Renfe ha rechazado el código")
                continue
            break
    profile = profile_dir_for(request.email)
    profile.mkdir(parents=True, exist_ok=True)
    mark_session_verified(profile)
    reporter.state(S.OPENING_PASS, f"Abriendo el abono {request.abono}")
    _sleep(0.3, cancel)
    reporter.state(S.SEARCHING, "Configurando la búsqueda")
    _sleep(0.3, cancel)
    if scenario == "NOTRAINS":
        raise NoTrainsListed("Renfe no lista ningún tren para ese día")
    departure = request.departure_time
    if departure is None:
        reporter.trains(TRAINS)
        interaction.train.request()
        reporter.state(S.AWAITING_TRAIN, "Elige el tren")
        departure = interaction.train.wait(cancel)
    reporter.state(S.POLLING, f"Buscando plaza en el de las {departure}")
    reloads = 10_000 if scenario == "SLOW" else 3
    for attempt in range(1, reloads + 1):
        _sleep(0.3, cancel)
        reporter.attempt(attempt)
    reporter.state(S.RESERVED, "Plaza reservada: completa la compra")
    while not interaction.release.wait(0.2):
        if cancel.is_set():
            raise JobCancelled


runner.run_search = scripted_search


def launch_next(executable: Path) -> subprocess.Popen[bytes]:
    """What starting the downloaded build means here: the version written in it."""
    content = executable.read_bytes().decode(errors="replace")
    if content.startswith("BROKEN"):
        return subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
    version = content.splitlines()[0].split(":", 1)[1].strip()
    env = {**os.environ, "E2E_APP_VERSION": version}
    return subprocess.Popen([sys.executable, __file__, "--after-update"], env=env)


activation_url = os.environ.get("E2E_ACTIVATION_URL")
activator = activation.Activator(
    required=activation_url is not None,
    server_url=activation_url or "http://127.0.0.1:9",
    public_key=os.environ.get("E2E_PUBLIC_KEY", activation.PUBLIC_KEY),
    license_path=DATA / "license.json",
    machine=os.environ.get("E2E_MACHINE_ID", activation.machine_id()),
)
activation.activator = activator

exe = os.environ.get("E2E_EXE")
updater = updater_module.Updater(
    Path(exe) if exe else None, current=config.APP_VERSION, launcher=launch_next
)
updater_module.updater = updater
activator.on_update = updater.offer

from app import desktop, window  # noqa: E402  (binds the updater above)

# How long the app waits for a closed window to come back before it quits.
window.CLOSED_GRACE = float(os.environ.get("E2E_WINDOW_GRACE", window.CLOSED_GRACE))
# And how long the version an update started waits for that window to reconnect.
desktop.RECONNECT_ALLOWANCE = float(
    os.environ.get("E2E_RECONNECT_ALLOWANCE", desktop.RECONNECT_ALLOWANCE)
)

desktop.updater = updater
desktop.run()
