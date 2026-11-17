import os
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

# Inside the PyInstaller executable the code and the built SPA are unpacked to a temporary
# directory that is deleted on exit, so the user's data has to live somewhere else.
FROZEN = getattr(sys, "frozen", False)


def system_environment(environ: dict[str, str]) -> dict[str, str]:
    """The environment without the library path PyInstaller sets for its bundle.

    On Linux the bundle points LD_LIBRARY_PATH at its own libraries, built on an older
    distribution, and every program the app starts inherits it: the browser, Chromium,
    even /bin/sh, which on Arch then loads the bundle's old libreadline and dies. The
    path the user had, if any, is kept by PyInstaller in LD_LIBRARY_PATH_ORIG.
    """
    env = dict(environ)
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original:
        env["LD_LIBRARY_PATH"] = original
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


# Where each family of distributions keeps its CA bundle.
SYSTEM_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Arch, Gentoo
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/ssl/cert.pem",  # Alpine, and Arch again
)


def ca_bundle(environ: dict[str, str], exists: Any = os.path.exists) -> str | None:
    """The certificates HTTPS should trust, if the bundled OpenSSL cannot find them.

    Its compiled-in location is the build machine's (/usr/lib/ssl on Ubuntu), which Arch
    and Fedora do not have, so every HTTPS request, to the activation server or to
    GitHub for updates, failed as "no connection". The system's own bundle comes first,
    so certificates the user added are trusted; certifi's, packaged with Selenium, is
    the fallback for minimal systems with none.
    """
    if environ.get("SSL_CERT_FILE") or environ.get("SSL_CERT_DIR"):
        return None
    for path in SYSTEM_CA_BUNDLES:
        if exists(path):
            return path
    try:
        import certifi
    except ImportError:
        return None
    return str(certifi.where())


if FROZEN and sys.platform.startswith("linux"):
    # The loader reads LD_LIBRARY_PATH only at startup, so this process keeps finding its
    # bundled libraries; only the programs it starts from now on see the system's.
    _cleaned = system_environment(dict(os.environ))
    os.environ.clear()
    os.environ.update(_cleaned)
    # OpenSSL reads SSL_CERT_FILE whenever a context loads the default certificates.
    _bundle = ca_bundle(dict(os.environ))
    if _bundle:
        os.environ["SSL_CERT_FILE"] = _bundle

if FROZEN:
    _BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
    if sys.platform == "win32":
        _DATA_HOME = Path(os.getenv("LOCALAPPDATA") or Path.home())
    else:
        # The XDG location every Linux desktop expects: ~/.local/share by default.
        _DATA_HOME = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    DATA_DIR = _DATA_HOME / "RenfeEnjoyer"
    FRONTEND_DIST = _BUNDLE_DIR / "frontend" / "dist"
else:
    DATA_DIR = BASE_DIR
    # Built SPA. When present it is served from this app, so there is a single origin
    # and no CORS involved; in development Vite serves it instead.
    FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

DATABASE_PATH = Path(os.getenv("RENFE_DB_PATH", DATA_DIR / "renfe_enjoyer.db"))
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

# Encrypts the Renfe passwords in the database (app/credentials.py). Kept out of the
# database file so a copy of it alone reveals nothing.
KEY_PATH = Path(os.getenv("RENFE_KEY_PATH", DATA_DIR / "credentials.key"))

# Vite dev server; in production the SPA is served from the same origin.
CORS_ORIGINS = os.getenv("RENFE_CORS_ORIGINS", "http://localhost:5173").split(",")

HEADLESS = os.getenv("RENFE_HEADLESS", "").lower() in {"1", "true", "yes"}

# Renfe's pages are slow and the results table is polled, so waits are generous.
SELENIUM_TIMEOUT = int(os.getenv("RENFE_SELENIUM_TIMEOUT", "100"))

APP_VERSION = "2.0.6"

# The Windows executable only runs on computers the activation server has approved. The
# variable can switch the check on for a checkout (to try it), never off for the bundle.
ACTIVATION_REQUIRED = FROZEN or os.getenv("RENFE_ACTIVATION", "").lower() in {
    "1",
    "true",
    "yes",
}
# Overridable to test against a local server. Pointing it elsewhere gains nothing: the
# app only accepts licenses signed with the real server's key.
ACTIVATION_URL = os.getenv("RENFE_ACTIVATION_URL", "https://renfe.rafitolmg.dev")

# Persistent Chrome profile. Keeping the session between runs means the bot normally
# skips the login altogether, and with it the captcha and the verification code.
PROFILE_DIR = Path(os.getenv("RENFE_PROFILE_DIR", DATA_DIR / "browser-profile"))
