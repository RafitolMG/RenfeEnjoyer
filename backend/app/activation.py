"""Activation of this computer against the activation server (`activation/` in this repo).

Only the Windows executable requires it: the source is public, so a checkout could drop
the check anyway, and the executable is what gets passed around. Anyone can ask for access
from the app; the admin approves or revokes on the server.

An approved computer holds a license the server signed with its private key, bound to this
computer's id and valid for a few days. Each check renews it, so the app keeps working
through a server outage while a revocation still takes effect within that window. The app
only has the public key, so a fake server cannot hand out approvals.
"""

import base64
import contextlib
import getpass
import hashlib
import json
import locale
import logging
import platform
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.config import (
    ACTIVATION_REQUIRED,
    ACTIVATION_URL,
    APP_VERSION,
    DATA_DIR,
)

logger = logging.getLogger(__name__)

# The server's public key. Its private half lives only in the server's secrets; the
# pair comes from activation/scripts/generate_keys.py.
PUBLIC_KEY = "f1xuH2WVSCh7aIvvCkgkj7nB1jqvCOxwHmjIffIxzIU"

# How stale the server's last answer may get before the next check asks it again. This,
# not the license length, is how fast a revocation reaches a computer that is online.
RECHECK_AFTER = timedelta(hours=1)
REQUEST_TIMEOUT = 10


class ActivationError(Exception):
    """Raised when the activation server cannot be reached or gives a bad answer."""


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def raw_machine_id() -> str:
    """The operating system's own id for this installation."""
    if sys.platform == "win32":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0])
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            text = Path(candidate).read_text().strip()
        except OSError:
            continue
        if text:
            return text
    return f"{uuid.getnode():012x}"


def machine_id() -> str:
    """This computer's id as the server sees it: a hash, never the raw Windows id."""
    return hashlib.sha256(f"renfe-enjoyer:{raw_machine_id()}".encode()).hexdigest()


DETAIL_LIMIT = 120

# Which build the server should offer as an update; older apps send none: Windows.
PLATFORM = "windows" if sys.platform == "win32" else "linux"


def device_info() -> dict[str, str]:
    """What the admin sees about this computer, besides its id: who is asking.

    The activation form says these are sent. Anything that cannot be read is left out.
    """
    details: dict[str, str] = {
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "locale": locale.getlocale()[0] or "",
    }
    # getuser() raises when no user name is set anywhere it looks.
    with contextlib.suppress(OSError, KeyError, ImportError):
        details["user"] = getpass.getuser()
    now = datetime.now().astimezone()
    details["timezone"] = f"{now.tzname()} (UTC{now.strftime('%z')})"
    return {key: value[:DETAIL_LIMIT] for key, value in details.items() if value}


@dataclass
class License:
    machine_id: str
    expires_at: datetime


@dataclass(frozen=True)
class UpdateOffer:
    """The newest version the admin has published, as the server signed it."""

    version: str
    url: str
    sha256: str
    size: int
    # Below it the app must update before it searches again.
    min_version: str | None


class Activator:
    def __init__(
        self,
        *,
        required: bool,
        server_url: str,
        public_key: str,
        license_path: Path,
        machine: str,
    ) -> None:
        self.required = required
        self.server_url = server_url.rstrip("/")
        self.public_key = Ed25519PublicKey.from_public_bytes(_b64decode(public_key))
        self.license_path = license_path
        self.machine = machine
        self._lock = threading.Lock()
        self._status: str | None = None
        # Monotonic, so changing the computer's clock cannot postpone the next check.
        self._checked_at: float | None = None
        self._error: str | None = None
        self.update: UpdateOffer | None = None
        # Told about every verified answer's offer (app/updater.py).
        self.on_update: Callable[[UpdateOffer | None], None] | None = None

    # --- license on disk --------------------------------------------------------------

    def verify(self, license_: dict[str, Any]) -> License:
        """Check a license is the server's and is for this computer; raise otherwise."""
        try:
            payload = _b64decode(license_["payload"])
            self.public_key.verify(_b64decode(license_["signature"]), payload)
            claims = json.loads(payload)
            parsed = License(
                machine_id=claims["machine_id"],
                expires_at=datetime.fromisoformat(claims["expires_at"]),
            )
        except (InvalidSignature, KeyError, TypeError, ValueError) as exc:
            raise ActivationError("La licencia no está firmada por el servidor") from exc
        if parsed.machine_id != self.machine:
            raise ActivationError("La licencia es de otro equipo")
        return parsed

    def verify_update(self, signed: dict[str, Any]) -> UpdateOffer:
        """Check an update offer is the server's; raise otherwise."""
        try:
            payload = _b64decode(signed["payload"])
            self.public_key.verify(_b64decode(signed["signature"]), payload)
            claims = json.loads(payload)
            # The same key signs licenses; only an update says it is one.
            if claims.get("kind") != "update":
                raise ValueError("not an update")
            min_version = claims.get("min_version")
            return UpdateOffer(
                version=str(claims["version"]),
                url=str(claims["url"]),
                sha256=str(claims["sha256"]).lower(),
                size=int(claims["size"]),
                min_version=None if min_version is None else str(min_version),
            )
        except (InvalidSignature, KeyError, TypeError, ValueError) as exc:
            raise ActivationError(
                "La actualización no está firmada por el servidor"
            ) from exc

    def stored_license(self) -> License | None:
        """The saved license, if it is genuine, for this computer and still current."""
        try:
            parsed = self.verify(json.loads(self.license_path.read_text()))
        except (OSError, ValueError, ActivationError):
            return None
        return parsed if parsed.expires_at > datetime.now(UTC) else None

    def _store(self, license_: dict[str, Any] | None) -> None:
        if license_ is None:
            self.license_path.unlink(missing_ok=True)
            return
        self.verify(license_)
        self.license_path.parent.mkdir(parents=True, exist_ok=True)
        self.license_path.write_text(json.dumps(license_))

    # --- server -----------------------------------------------------------------------

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.server_url}{path}",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"RenfeEnjoyer/{APP_VERSION}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return dict(json.load(response))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc).get("detail")
            except ValueError:
                detail = None
            message = detail if isinstance(detail, str) else f"error {exc.code}"
            raise ActivationError(
                f"El servidor de activación respondió: {message}"
            ) from exc
        except (OSError, ValueError) as exc:
            raise ActivationError(
                "No se pudo contactar con el servidor de activación"
            ) from exc

    def _apply(self, answer: dict[str, Any]) -> None:
        status = answer.get("status")
        if status not in {"unknown", "pending", "approved", "revoked"}:
            raise ActivationError("Respuesta no válida del servidor de activación")
        # A revocation must not be outlived by a license saved earlier.
        self._store(answer.get("license") if status == "approved" else None)
        self._status = status
        self._checked_at = time.monotonic()
        self._error = None
        self._apply_update(answer.get("update"))

    def _apply_update(self, signed: Any) -> None:
        # A bad offer must not cost the license that came with it.
        try:
            self.update = self.verify_update(signed) if signed else None
        except ActivationError as exc:
            logger.warning("Actualización: %s", exc)
            self.update = None
        if self.on_update is not None:
            self.on_update(self.update)

    def _exchange(self, path: str, body: dict[str, Any]) -> None:
        try:
            self._apply(self._post(path, {"machine_id": self.machine, **body}))
        except ActivationError as exc:
            logger.warning("Activación: %s", exc)
            self._error = str(exc)
            self._checked_at = time.monotonic()

    def refresh(self, *, force: bool = False) -> None:
        with self._lock:
            # >= rather than >: Windows' clock ticks every ~15 ms, so two readings in a
            # row can be equal and a zero RECHECK_AFTER would otherwise never recheck.
            stale = self._checked_at is None or (
                time.monotonic() - self._checked_at >= RECHECK_AFTER.total_seconds()
            )
            if force or stale:
                self._exchange(
                    "/api/v1/check",
                    {
                        "app_version": APP_VERSION,
                        "platform": PLATFORM,
                        "device": device_info(),
                    },
                )

    def request_access(self, name: str, contact: str) -> None:
        with self._lock:
            self._exchange(
                "/api/v1/requests",
                {
                    "name": name,
                    "contact": contact,
                    "app_version": APP_VERSION,
                    "platform": PLATFORM,
                    "device": device_info(),
                },
            )

    # --- what the app asks ------------------------------------------------------------

    def is_authorized(self) -> bool:
        if not self.required:
            return True
        self.refresh()
        return self.stored_license() is not None

    def state(self) -> dict[str, Any]:
        if self.required:
            self.refresh()
        license_ = self.stored_license() if self.required else None
        return {
            "required": self.required,
            "authorized": not self.required or license_ is not None,
            # The server's last answer; None if it has never been reached.
            "status": self._status,
            "error": self._error,
            "expires_at": license_.expires_at.isoformat() if license_ else None,
            # Short enough to read out over the phone, long enough to tell apart.
            "machine_code": self.machine[:12],
        }


activator = Activator(
    required=ACTIVATION_REQUIRED,
    server_url=ACTIVATION_URL,
    public_key=PUBLIC_KEY,
    license_path=DATA_DIR / "license.json",
    machine=machine_id(),
)


def get_activator() -> Activator:
    return activator
