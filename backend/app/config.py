import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Inside the PyInstaller executable the code and the built SPA are unpacked to a temporary
# directory that is deleted on exit, so the user's data has to live somewhere else.
FROZEN = getattr(sys, "frozen", False)

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

APP_VERSION = "2.0.4"

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
