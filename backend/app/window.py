"""The app's own window, which the desktop launcher shows instead of a browser tab.

Three ways to show the SPA, tried in this order by `app.desktop`:

1. Native (Windows): a pywebview window over WebView2, which Windows 10 and 11 ship. It
   belongs to this process, so closing it quits the app, and the executable has no
   console at all.
2. App mode (Linux, or Windows without WebView2): Chrome, Chromium or Edge started with
   `--app`, a window of its own with no tabs nor address bar, in a profile of its own.
   It is a separate process, so the server quits once no page has been connected for
   `CLOSED_GRACE` seconds instead; that also lets the window outlive an update handover
   and reconnect to the new version, as a browser tab did.
3. The default browser, as before, with the same rule for quitting.

Bundling a webview on Linux was ruled out: WebKitGTK packed into the bundle starts its
helper processes from the build machine's paths, and Qt WebEngine adds 150-200 MB.
"""

import logging
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import DATA_DIR, FRONTEND_DIST

logger = logging.getLogger(__name__)

TITLE = "Renfe Enjoyer"
WIDTH, HEIGHT = 1180, 860
MIN_SIZE = (420, 560)
# The dark theme's background, shown before the page has painted.
BACKGROUND = "#111318"
# A reload, or a reconnect to the version an update just started, comes back well
# within this; a closed window does not.
CLOSED_GRACE = 10.0

# Separate from the bot's per-account profiles: this one only keeps the window's size
# and the SPA's local storage.
WINDOW_PROFILE = DATA_DIR / "window-profile"
# WebView2's equivalent, for the native window.
WEBVIEW_STORAGE = DATA_DIR / "webview"

# Chromium-based browsers that understand --app, by platform. Edge comes first on
# Windows because every Windows 10 and 11 has it.
LINUX_BROWSERS = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "brave-browser",
)
WINDOWS_BROWSERS = (
    ("PROGRAMFILES(X86)", r"Microsoft\Edge\Application\msedge.exe"),
    ("PROGRAMFILES", r"Microsoft\Edge\Application\msedge.exe"),
    ("PROGRAMFILES", r"Google\Chrome\Application\chrome.exe"),
    ("PROGRAMFILES(X86)", r"Google\Chrome\Application\chrome.exe"),
    ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
)


class WindowState:
    """What the SPA and a second launch need to know about the window."""

    def __init__(self) -> None:
        # Closing the page quits the app: the SPA then asks first while a search runs.
        # The native window asks by itself, so it leaves this off.
        self.closes_app = False
        # Brings the native window to the front, for a second launch of the executable.
        self.focus: Callable[[], None] | None = None


state = WindowState()


def find_app_browser(
    platform: str = sys.platform,
    environ: dict[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    exists: Callable[[str], bool] = os.path.isfile,
) -> str | None:
    """A Chromium-based browser that can open the app as a window of its own."""
    env = os.environ if environ is None else environ
    # Any browser that understands --app; the binary tests point it at a script.
    if override := env.get("RENFE_APP_BROWSER"):
        return override
    if platform == "win32":
        for variable, relative in WINDOWS_BROWSERS:
            base = env.get(variable)
            if base and exists(candidate := f"{base}\\{relative}"):
                return candidate
        return which("msedge") or which("chrome")
    if platform.startswith("linux"):
        for name in LINUX_BROWSERS:
            if path := which(name):
                return path
    return None


def app_window_command(browser: str, url: str, profile: Path) -> list[str]:
    return [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile}",
        f"--window-size={WIDTH},{HEIGHT}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def open_app_window(url: str, browser: str | None = None) -> bool:
    """Open the app as a browser window of its own. False if no browser can."""
    browser = browser or find_app_browser()
    if browser is None:
        return False
    # The display lookup the bot's browser needs when the server runs outside the
    # desktop session applies to this window just the same.
    from app.bot.driver import browser_environment

    try:
        subprocess.Popen(
            app_window_command(browser, url, WINDOW_PROFILE),
            env=browser_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.warning("Could not open the app window with %s: %s", browser, exc)
        return False
    return True


def open_window(url: str) -> str:
    """Open the app in a window of another process: app mode, or else a browser tab."""
    if open_app_window(url):
        return "app"
    webbrowser.open(url)
    return "browser"


def native_available() -> bool:
    """Whether this process can show the native window (it may still find no WebView2)."""
    if sys.platform != "win32":
        return False
    try:
        import webview  # noqa: F401
    except ImportError:
        return False
    return True


def check_native(timeout: float = 60) -> str:
    """Show the native window on a test page and read its title back from the page.

    What `--check-window` runs in CI: a bundle missing pywebview's or pythonnet's
    files, or a machine without WebView2, fails here instead of at the user's launch.
    """
    import webview

    found: dict[str, Any] = {}
    test = webview.create_window(TITLE, html="<title>renfe-ok</title>")

    def keep_renderer(renderer: str) -> bool:
        found["renderer"] = renderer
        return renderer == "edgechromium"

    def probe() -> None:
        try:
            if test.events.loaded.wait(timeout):
                found["title"] = test.evaluate_js("document.title")
        finally:
            test.destroy()

    test.events.initialized += keep_renderer
    webview.start(probe)
    if found.get("renderer") != "edgechromium":
        raise RuntimeError(f"sin WebView2 (motor {found.get('renderer')!r})")
    if found.get("title") != "renfe-ok":
        raise RuntimeError(f"la página de prueba no cargó ({found.get('title')!r})")
    return str(found["renderer"])


class NativeWindow:
    """The pywebview window. `run()` blocks the main thread, as pywebview requires."""

    def __init__(self, url: str, confirm_close: Callable[[], bool]) -> None:
        self.url = url
        # Asked when the user closes the window: True when closing needs confirming.
        self._confirm_close = confirm_close
        self._window: Any = None
        self._renderer: str | None = None
        self._closing_by_us = False
        self._lock = threading.Lock()

    def run(self) -> bool:
        """Show the window until it is closed. False if WebView2 is not available."""
        import webview

        window = webview.create_window(
            TITLE,
            self.url,
            width=WIDTH,
            height=HEIGHT,
            min_size=MIN_SIZE,
            background_color=BACKGROUND,
            # A browser lets you select and zoom; the log is worth copying from.
            text_select=True,
            zoomable=True,
        )
        window.events.initialized += self._check_renderer
        window.events.closing += self._on_closing
        with self._lock:
            self._window = window
        WEBVIEW_STORAGE.mkdir(parents=True, exist_ok=True)
        icon = FRONTEND_DIST / "favicon.ico"
        try:
            webview.start(
                # The SPA remembers the selected profile in local storage.
                private_mode=False,
                storage_path=str(WEBVIEW_STORAGE),
                icon=str(icon) if icon.is_file() else None,
            )
        finally:
            with self._lock:
                self._window = None
        return self._renderer == "edgechromium"

    def _check_renderer(self, renderer: str) -> bool:
        self._renderer = renderer
        # Without WebView2 pywebview falls back to Internet Explorer's engine, which
        # cannot run the SPA; the launcher opens an app-mode window instead.
        if renderer != "edgechromium":
            logger.warning("No WebView2 (pywebview chose %s)", renderer)
            return False
        return True

    def _on_closing(self, window: Any) -> bool:
        if self._closing_by_us or not self._confirm_close():
            return True
        # Runs on the GUI thread, which is where a message box has to be shown.
        return bool(
            window.create_confirmation_dialog(
                TITLE,
                "Hay una búsqueda en curso. Si cierras la aplicación se detendrá "
                "y se cerrará su navegador.",
            )
        )

    def close(self) -> None:
        """Close the window from another thread; no-op once it is gone."""
        with self._lock:
            window = self._window
            self._closing_by_us = True
        if window is not None:
            window.destroy()

    def focus(self) -> None:
        with self._lock:
            window = self._window
        if window is not None:
            window.restore()
            window.show()
            # Windows only lets a window it did not activate itself flash in the
            # taskbar; staying on top for a moment brings it in front.
            window.on_top = True
            window.on_top = False
