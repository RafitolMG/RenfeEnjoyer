"""Self-update of the Windows executable.

The activation server offers the newest version the admin has published, signed with
its key (`activation/app/releases.py`); `app.activation` verifies it and hands it here.
The new executable is downloaded next to the running one as `<exe>.new`, checked against
the signed SHA-256 and size, and recorded in `<exe>.new.json`. A download finished before
a restart is picked up again from those two files.

Applying it, which the user starts from the SPA while no search runs, is a handover:

1. the server stops, so the port is free (`app.desktop` drives this);
2. Windows will not overwrite a running executable but does rename it, so the running
   one becomes `<exe>.old` and the new one takes its name;
3. the new one starts with `--after-update` and the old process waits for /api/health
   to answer with the new version;
4. if it does, the old process exits and the new one deletes `<exe>.old`; if it does
   not within `HANDOVER_TIMEOUT`, the old process stops it, puts itself back and serves
   again, and that version is not offered again until restart.

Anyone who can write to the executable's folder can replace the executable itself, so
trusting a staged file there adds nothing an attacker could not already do.
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from app.activation import UpdateOffer
from app.config import APP_VERSION

logger = logging.getLogger(__name__)

HANDOVER_TIMEOUT = 60.0
DOWNLOAD_TIMEOUT = 30
CHUNK = 256 * 1024
# How long the old executable may stay locked by the process that is about to exit.
CLEANUP_PATIENCE = 120.0

VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    match = VERSION.match(text or "")
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def is_newer(candidate: str | None, than: str) -> bool:
    new, current = parse_version(candidate), parse_version(than)
    return new is not None and current is not None and new > current


class UpdateNotReady(Exception):
    """Raised when asked to apply an update that is not downloaded and checked."""


class Process(Protocol):
    def poll(self) -> int | None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


def launch(executable: Path) -> Process:
    """Start the new executable as an independent instance with its own console."""
    # Without this a onefile build started from another one reuses its parent's unpacked
    # files, which are deleted when the parent exits.
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen(
        [str(executable), "--after-update"], env=env, creationflags=flags
    )


def health_version(port: int) -> str | None:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=1
        ) as response:
            return str(json.load(response).get("version"))
    except (OSError, ValueError):
        return None


class Updater:
    def __init__(
        self,
        executable: Path | None,
        current: str = APP_VERSION,
        launcher: Callable[[Path], Process] = launch,
        probe: Callable[[int], str | None] = health_version,
    ) -> None:
        # None from a checkout: there is no executable to replace, `git pull` updates it.
        self.executable = executable
        self.current = current
        self._launch = launcher
        self._probe = probe
        self._lock = threading.Lock()
        self._offer: UpdateOffer | None = None
        self._state = "none"
        self._target: str | None = None
        self._progress = 0.0
        self._error: str | None = None
        # The download in hand or under way, as (version, sha256): the admin can re-pin
        # a version to a rebuilt file, and that one has to be fetched again.
        self._key: tuple[str, str] | None = None
        # Versions whose handover failed: not offered again until the app restarts.
        self._failed: set[str] = set()
        # Files that did not match what was published: fetching them again would only
        # fail again, so they wait for the offer to change.
        self._rejected: set[tuple[str, str]] = set()
        self._download: threading.Thread | None = None
        # Set by app.desktop: stops the server so the port is free for the handover.
        self.stop_server: Callable[[], None] | None = None
        self.handover_requested = False
        if executable is not None:
            self._resume()

    # --- files -------------------------------------------------------------------------

    def _path(self, suffix: str) -> Path:
        assert self.executable is not None
        return self.executable.with_name(self.executable.name + suffix)

    def _resume(self) -> None:
        """Tidy up after an earlier run and keep a download it had already checked."""
        _remove(self._path(".part"))
        threading.Thread(target=self._remove_old, daemon=True).start()
        staged, marker = self._path(".new"), self._path(".new.json")
        try:
            record = json.loads(marker.read_text())
            if (
                is_newer(record["version"], self.current)
                and _sha256(staged) == record["sha256"]
            ):
                self._state, self._target, self._progress = (
                    "ready",
                    record["version"],
                    1.0,
                )
                self._key = (record["version"], record["sha256"])
                return
        except (OSError, ValueError, KeyError, TypeError):
            pass
        _remove(staged)
        _remove(marker)

    def _remove_old(self) -> None:
        # After a handover the previous process holds `.old` until it notices and exits.
        deadline = time.monotonic() + CLEANUP_PATIENCE
        old = self._path(".old")
        while old.exists() and time.monotonic() < deadline:
            try:
                old.unlink()
            except OSError:
                time.sleep(1)

    def _discard_staged(self) -> None:
        for suffix in (".new", ".new.json", ".part"):
            _remove(self._path(suffix))

    # --- offers ------------------------------------------------------------------------

    def offer(self, offer: UpdateOffer | None) -> None:
        """React to the server's latest answer: download, keep, or drop an update."""
        if self.executable is None:
            return
        with self._lock:
            self._offer = offer
            if self._state == "applying":
                return
            if offer is None or not is_newer(offer.version, self.current):
                # Withdrawn, or nothing newer: a staged file must not be installed.
                if self._state in {"ready", "failed", "manual"} or self._target:
                    self._discard_staged()
                self._state, self._target, self._error = "none", None, None
                self._key = None
                return
            if offer.version in self._failed:
                return
            key = (offer.version, offer.sha256)
            if key in self._rejected:
                self._state, self._target, self._key = "failed", offer.version, key
                self._error = _mismatch(offer.version)
                return
            if self._key == key and self._state in {"ready", "downloading"}:
                return
            self._state, self._target, self._key = "downloading", offer.version, key
            self._progress, self._error = 0.0, None
            logger.info("Descargando la versión %s…", offer.version)
            self._download = threading.Thread(
                target=self._fetch, args=(offer,), name="renfe-update", daemon=True
            )
            self._download.start()

    def _still_wanted(self, offer: UpdateOffer) -> bool:
        with self._lock:
            return (
                self._key == (offer.version, offer.sha256)
                and self._state == "downloading"
            )

    def _fetch(self, offer: UpdateOffer) -> None:
        part = self._path(".part")
        try:
            handle = part.open("wb")
        except OSError:
            # A folder the user cannot write to, e.g. Program Files.
            self._finish(
                offer, "manual", "No se puede escribir en la carpeta del programa"
            )
            return
        digest, received = hashlib.sha256(), 0
        try:
            with handle:
                request = urllib.request.Request(
                    offer.url, headers={"User-Agent": f"RenfeEnjoyer/{self.current}"}
                )
                with urllib.request.urlopen(
                    request, timeout=DOWNLOAD_TIMEOUT
                ) as response:
                    while chunk := response.read(CHUNK):
                        if not self._still_wanted(offer):
                            raise _Superseded
                        handle.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if received > offer.size:
                            # Not the published file: no point fetching the rest.
                            raise _Mismatch
                        with self._lock:
                            self._progress = min(received / max(offer.size, 1), 1.0)
            if received != offer.size or digest.hexdigest() != offer.sha256:
                raise _Mismatch
            # On Linux a fresh file is not executable: the new build would never start.
            assert self.executable is not None
            part.chmod(self.executable.stat().st_mode & 0o777)
            os.replace(part, self._path(".new"))
            self._path(".new.json").write_text(
                json.dumps({"version": offer.version, "sha256": offer.sha256})
            )
        except _Superseded:
            _remove(part)
            return
        except _Mismatch:
            _remove(part)
            logger.warning(
                "%s: no se repetirá la descarga hasta que cambie lo publicado",
                _mismatch(offer.version),
            )
            with self._lock:
                self._rejected.add((offer.version, offer.sha256))
            self._finish(offer, "failed", _mismatch(offer.version))
            return
        except (OSError, ValueError) as exc:
            _remove(part)
            logger.warning("No se pudo descargar la versión %s: %s", offer.version, exc)
            self._finish(
                offer,
                "failed",
                f"No se pudo descargar la versión {offer.version}; "
                "se reintentará en la próxima comprobación",
            )
            return
        logger.info("Versión %s descargada: instálala con Actualizar", offer.version)
        self._finish(offer, "ready", None)

    def _finish(self, offer: UpdateOffer, state: str, error: str | None) -> None:
        with self._lock:
            if self._key != (offer.version, offer.sha256):
                return
            self._state, self._error = state, error
            if state == "ready":
                self._progress = 1.0

    # --- applying ----------------------------------------------------------------------

    def required(self) -> bool:
        """Whether the server has declared this version too old to keep searching."""
        offer = self._offer
        return offer is not None and is_newer(offer.min_version, self.current)

    def apply(self) -> None:
        """Start the handover; the server stops shortly after this returns."""
        with self._lock:
            if self._state != "ready" or self.stop_server is None:
                raise UpdateNotReady("No hay ninguna actualización lista para instalar")
            self._state = "applying"
            self.handover_requested = True
            stop = self.stop_server
        # Late enough for the response to reach the browser first.
        threading.Timer(0.5, stop).start()

    def handover(self, port: int) -> bool:
        """Swap in the new executable and wait for it. False if it was rolled back."""
        exe, staged, old = self.executable, self._path(".new"), self._path(".old")
        target = self._target
        assert exe is not None and target is not None
        self.handover_requested = False
        try:
            old.unlink(missing_ok=True)
            exe.replace(old)
            try:
                staged.replace(exe)
            except OSError:
                old.replace(exe)
                raise
        except OSError as exc:
            logger.warning("Update swap failed: %s", exc)
            return self._rolled_back(target, "No se pudo reemplazar el ejecutable")
        self._path(".new.json").unlink(missing_ok=True)

        process: Process | None = None
        try:
            process = self._launch(exe)
            deadline = time.monotonic() + HANDOVER_TIMEOUT
            while time.monotonic() < deadline and process.poll() is None:
                if self._probe(port) == target:
                    return True
                time.sleep(0.5)
        except OSError as exc:
            logger.warning("New version did not start: %s", exc)

        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        self._restore(exe, old)
        return self._rolled_back(target, f"La versión {target} no arrancó")

    def _restore(self, exe: Path, old: Path) -> None:
        # The killed process may hold its executable for a moment longer.
        deadline = time.monotonic() + 15
        while True:
            try:
                exe.unlink(missing_ok=True)
                old.replace(exe)
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)

    def _rolled_back(self, target: str, reason: str) -> bool:
        with self._lock:
            self._failed.add(target)
            self._state = "failed"
            self._error = f"{reason}; sigues con la {self.current}"
        return False

    # --- for the SPA -------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        with self._lock:
            offer = self._offer
            return {
                "current": self.current,
                "supported": self.executable is not None,
                "state": self._state,
                "version": self._target,
                "progress": round(self._progress, 3),
                "error": self._error,
                "required": self.required(),
                "download_url": offer.url if offer else None,
            }


class _Superseded(Exception):
    """A newer offer, or a withdrawal, arrived while downloading."""


class _Mismatch(Exception):
    """The file downloaded is not the one the admin published."""


def _mismatch(version: str) -> str:
    return f"La descarga de la versión {version} no coincide con la publicada"


def _remove(path: Path) -> None:
    """Delete a leftover if possible; never let tidying up stop the app.

    The executable may sit in a folder the user cannot write to (/usr/local/bin,
    Program Files, a read-only mount), where even removing a file that is not there
    fails. Updates then fall back to a manual download.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.info("Could not remove %s: %s", path, exc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _executable() -> Path | None:
    from app.config import FROZEN

    return Path(sys.executable) if FROZEN else None


updater = Updater(_executable())


def _connect() -> None:
    from app.activation import activator

    activator.on_update = updater.offer


_connect()


def get_updater() -> Updater:
    return updater
