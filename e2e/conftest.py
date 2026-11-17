"""Fixtures for the end-to-end suite: real servers in subprocesses, a real browser.

    .venv/bin/pip install -e './backend[e2e]'   # once, then: playwright install chromium
    .venv/bin/pytest e2e                         # from the repository root

E2E_CHROMIUM points Playwright at a system browser instead of its own download.
"""

import base64
import contextlib
import hashlib
import http.server
import json
import os
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from playwright.sync_api import Browser, Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "harness" / "app_server.py"
BACKEND_PYTHON = os.environ.get("E2E_BACKEND_PYTHON", sys.executable)
ACTIVATION_PYTHON = os.environ.get(
    "E2E_ACTIVATION_PYTHON", str(ROOT / "activation" / ".venv" / "bin" / "python")
)
ADMIN_PASSWORD = "e2e-admin-password"
GITHUB_REPO = "e2e/renfe"
ASSETS = {"windows": "RenfeEnjoyer.exe", "linux": "RenfeEnjoyer-linux"}
# The build this platform's app downloads, as the activation server names it.
PLATFORM = "windows" if sys.platform == "win32" else "linux"

expect.set_options(timeout=10_000)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


_KEY = Ed25519PrivateKey.generate()
PRIVATE_KEY = b64(_KEY.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
PUBLIC_KEY = b64(_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(
    method: str, url: str, body: Any = None, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw.decode()


def wait_until(check: Callable[[], Any], timeout: float = 30, every: float = 0.2) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
        except (OSError, ValueError):
            result = None
        if result:
            return result
        time.sleep(every)
    raise AssertionError(f"timed out after {timeout}s waiting for {check}")


@dataclass
class Service:
    """A server in its own process group, so whatever it starts dies with it."""

    url: str
    process: subprocess.Popen[bytes]
    log: Path

    def stop(self) -> None:
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
        # A handover leaves its successor in the same group after the first exits.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.process.pid, signal.SIGKILL)

    def log_text(self) -> str:
        return self.log.read_text(errors="replace")


def start(
    command: list[str],
    env: dict[str, str],
    log: Path,
    url: str,
    health: str,
    cwd: Path | None = None,
) -> Service:
    """Start a server and wait until `url + health` answers."""
    process = subprocess.Popen(
        command,
        env={**os.environ, **env},
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    service = Service(url=url, process=process, log=log)

    def up() -> bool:
        if process.poll() is not None:
            raise AssertionError(
                f"{command[-1]} exited:\n{log.read_text(errors='replace')}"
            )
        with urllib.request.urlopen(url + health, timeout=1):
            return True

    wait_until(up)
    return service


# --- a stand-in for GitHub's releases ------------------------------------------------


@dataclass
class FakeGitHub:
    """Serves /repos/<repo>/releases like GitHub's API, and the files themselves."""

    url: str = ""
    releases: list[dict[str, Any]] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    downloads: list[str] = field(default_factory=list)
    # Seconds between 64 KiB chunks: slow enough for the progress to be seen.
    throttle: float = 0.0

    def release(self, version: str, builds: dict[str, bytes]) -> None:
        """Publish (or rebuild) a release with these builds, by platform."""
        tag = f"v{version}"
        assets = []
        for platform, content in builds.items():
            path = f"/download/{tag}/{ASSETS[platform]}"
            self.files[path] = content
            assets.append(
                {
                    "name": ASSETS[platform],
                    "size": len(content),
                    "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
                    "browser_download_url": self.url + path,
                }
            )
        self.releases = [r for r in self.releases if r["tag_name"] != tag]
        self.releases.insert(
            0,
            {
                "tag_name": tag,
                "assets": assets,
                "body": "",
                "published_at": "2026-11-17T00:00:00Z",
            },
        )


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    fake = FakeGitHub()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith(f"/repos/{GITHUB_REPO}/releases"):
                body = json.dumps(fake.releases).encode()
            elif self.path in fake.files:
                fake.downloads.append(self.path)
                body = fake.files[self.path]
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for start in range(0, len(body), 65536):
                    self.wfile.write(body[start : start + 65536])
                    if fake.throttle:
                        time.sleep(fake.throttle)
            except OSError:
                pass  # the app gave up on it

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.url = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    yield fake
    server.shutdown()


# --- the activation server -----------------------------------------------------------


@dataclass
class Activation:
    service: Service
    db: Path

    @property
    def url(self) -> str:
        return self.service.url

    def admin_cookie(self, password: str = ADMIN_PASSWORD) -> str:
        request = urllib.request.Request(
            self.url + "/api/v1/admin/login",
            data=json.dumps({"password": password}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            return response.headers["set-cookie"].split(";")[0]

    def admin(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        return http_json(method, self.url + path, body, {"Cookie": self.admin_cookie()})

    def machine(self, machine_id: str) -> dict[str, Any]:
        _, machines = self.admin("GET", "/api/v1/admin/machines")
        return next(m for m in machines if m["machine_id"] == machine_id)

    def decide(self, machine_id: str, action: str) -> None:
        status, _ = self.admin(
            "POST", f"/api/v1/admin/machines/{self.machine(machine_id)['id']}/{action}"
        )
        assert status == 200

    def publish(self, version: str) -> None:
        status, body = self.admin("POST", "/api/v1/admin/releases", {"version": version})
        assert status == 201, body

    def sql(self, query: str, *args: Any) -> list[tuple[Any, ...]]:
        with sqlite3.connect(self.db) as db:
            return db.execute(query, args).fetchall()


@pytest.fixture
def activation(tmp_path: Path, github: FakeGitHub) -> Iterator[Activation]:
    port = free_port()
    db = tmp_path / "activation.db"
    service = start(
        [
            ACTIVATION_PYTHON,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        {
            "ACTIVATION_PRIVATE_KEY": PRIVATE_KEY,
            "ADMIN_PASSWORD": ADMIN_PASSWORD,
            "ACTIVATION_DB_PATH": str(db),
            "GEOIP_ENABLED": "0",
            "GITHUB_API": github.url,
            "GITHUB_REPO": GITHUB_REPO,
        },
        tmp_path / "activation.log",
        f"http://127.0.0.1:{port}",
        "/health",
        cwd=ROOT / "activation",
    )
    yield Activation(service, db)
    service.stop()


# --- the app -------------------------------------------------------------------------


@dataclass
class App:
    service: Service
    data: Path
    exe: Path
    machine_id: str
    port: int

    @property
    def url(self) -> str:
        return self.service.url

    def api(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        return http_json(method, self.url + path, body)

    def profile(
        self,
        username: str,
        abono: str,
        email: str | None = None,
        password: str = "secreto",
    ) -> dict[str, Any]:
        status, body = self.api(
            "POST",
            "/api/users",
            {
                "username": username,
                "email": email or f"{username.lower()}@example.com",
                "password": password,
                "abono": abono,
            },
        )
        assert status == 201, body
        return body

    def version(self) -> str | None:
        try:
            return http_json("GET", self.url + "/api/health")[1]["version"]
        except (OSError, TypeError, KeyError):
            return None

    def stop(self) -> None:
        self.service.stop()


@pytest.fixture
def make_app(tmp_path: Path) -> Iterator[Callable[..., App]]:
    apps: list[App] = []

    def make(
        activation: Activation | None = None,
        version: str = "2.0.0",
        data: Path | None = None,
        port: int | None = None,
        machine_id: str | None = None,
        executable: bool = True,
    ) -> App:
        port = port or free_port()
        data = data or tmp_path / f"app-{len(apps)}"
        data.mkdir(parents=True, exist_ok=True)
        exe = data / ASSETS[PLATFORM]
        if not exe.exists():
            exe.write_bytes(f"E2E:{version}".encode())
            exe.chmod(0o755)
        machine_id = machine_id or secrets.token_hex(32)
        env = {
            "E2E_DATA": str(data),
            "E2E_APP_VERSION": version,
            # Without one the app updates nothing, like a checkout.
            "E2E_EXE": str(exe) if executable else "",
            "E2E_MACHINE_ID": machine_id,
            "E2E_PUBLIC_KEY": PUBLIC_KEY,
            "RENFE_PORT": str(port),
        }
        if activation is not None:
            env["E2E_ACTIVATION_URL"] = activation.url
        service = start(
            [BACKEND_PYTHON, str(HARNESS)],
            env,
            data / "console.log",
            f"http://127.0.0.1:{port}",
            "/api/health",
        )
        app = App(service, data, exe, machine_id, port)
        apps.append(app)
        return app

    yield make
    for app in apps:
        app.stop()


@pytest.fixture
def app(make_app: Callable[..., App]) -> App:
    """The app as from a checkout: no activation required."""
    return make_app()


# --- the browser ---------------------------------------------------------------------


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        chromium = os.environ.get("E2E_CHROMIUM")
        launched = (
            playwright.chromium.launch(executable_path=chromium)
            if chromium
            else playwright.chromium.launch()
        )
        yield launched
        launched.close()


class Pages:
    """New pages in fresh contexts; native dialogs are refused and recorded."""

    def __init__(self, browser: Browser) -> None:
        self.browser = browser
        self.contexts: list[Any] = []
        self.errors: list[str] = []
        self.native_dialogs: list[str] = []

    def new(self, url: str | None = None, width: int = 1280) -> Page:
        context = self.browser.new_context(
            locale="es-ES", viewport={"width": width, "height": 900}
        )
        self.contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        if url:
            page.goto(url)
        return page

    def close(self) -> None:
        for context in self.contexts:
            context.close()


@pytest.fixture
def pages(browser: Browser) -> Iterator[Pages]:
    opened = Pages(browser)
    yield opened
    opened.close()
    assert not opened.errors, f"JavaScript errors: {opened.errors}"


def admin_page(pages: Pages, activation: Activation) -> Page:
    page = pages.new(activation.url + "/admin/login")
    page.fill("#password", ADMIN_PASSWORD)
    page.click("#submit")
    expect(page).to_have_url(activation.url + "/admin")
    return page


def badge(page: Page) -> Any:
    return page.locator(".monitor .badge")
