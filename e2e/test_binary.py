"""The packaged executable, as a user runs it. Skipped unless E2E_BINARY is set.

    E2E_BINARY=dist/RenfeEnjoyer-linux         the build under test
    E2E_BINARY_OLD=dist-old/RenfeEnjoyer       optional: an older build to update from
    E2E_NETWORK=1                              optional: reach the real activation server

What only the bundle can get wrong lives here: the libraries and certificates PyInstaller
packs, the data folder of a frozen app, renaming a running executable.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import ROOT, Pages, Service, expect, free_port, http_json, start, wait_until

BINARY = os.environ.get("E2E_BINARY")
OLD = os.environ.get("E2E_BINARY_OLD")
VERSION = re.search(
    r'^APP_VERSION = "(.+)"', (ROOT / "backend/app/config.py").read_text(), re.M
)[1]  # type: ignore[index]
LINUX = sys.platform.startswith("linux")

pytestmark = pytest.mark.skipif(not BINARY, reason="needs E2E_BINARY")


def run(binary: Path, home: Path, port: int, **env: str) -> Service:
    data_env = {"XDG_DATA_HOME": str(home)} if LINUX else {"LOCALAPPDATA": str(home)}
    return start(
        [str(binary)],
        {
            **data_env,
            "RENFE_PORT": str(port),
            "RENFE_ACTIVATION_URL": "http://127.0.0.1:9",
            **env,
        },
        home.parent / f"{binary.name}-{port}.log",
        f"http://127.0.0.1:{port}",
        "/api/health",
    )


@pytest.fixture
def services() -> Iterator[list[Service]]:
    started: list[Service] = []
    yield started
    for service in started:
        service.stop()


def install(tmp_path: Path, name: str = "app") -> Path:
    folder = tmp_path / name
    folder.mkdir()
    target = folder / Path(BINARY or "").name
    shutil.copy2(BINARY or "", target)
    return target


def test_it_starts_and_keeps_its_data_in_the_platform_folder(
    tmp_path: Path, services: list[Service]
) -> None:
    binary = install(tmp_path)
    port = free_port()
    services.append(run(binary, tmp_path / "home", port, RENFE_NO_BROWSER="1"))
    assert (
        http_json("GET", f"http://127.0.0.1:{port}/api/health")[1]["version"] == VERSION
    )
    assert (tmp_path / "home" / "RenfeEnjoyer" / "renfe_enjoyer.db").exists()
    assert not (tmp_path / "app" / "renfe_enjoyer.db").exists()


@pytest.mark.skipif(not LINUX, reason="BROWSER scripts are a Linux test")
def test_the_browser_opens_with_the_systems_libraries(
    tmp_path: Path, services: list[Service]
) -> None:
    """The first 2.0.5 build passed its libraries to the browser; Arch's sh died."""
    binary = install(tmp_path)
    browser = tmp_path / "browser"
    browser.write_text(
        f'#!/bin/sh\nenv > "{tmp_path}/browser-env"\necho "$1" > "{tmp_path}/opened"\n'
    )
    browser.chmod(0o755)
    port = free_port()
    service = run(binary, tmp_path / "home", port, BROWSER=str(browser))
    services.append(service)
    wait_until(lambda: (tmp_path / "opened").exists())
    assert (tmp_path / "opened").read_text().strip() == f"http://127.0.0.1:{port}"
    inherited = (tmp_path / "browser-env").read_text()
    assert "_MEI" not in "".join(
        line for line in inherited.splitlines() if line.startswith("LD_LIBRARY_PATH=")
    )
    assert "symbol lookup" not in service.log_text()


def test_the_bots_browser_starts(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(install(tmp_path)), "--check-browser"],
        env={
            **os.environ,
            "XDG_DATA_HOME": str(tmp_path / "home"),
            "LOCALAPPDATA": str(tmp_path / "home"),
        },
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Navegador OK" in result.stdout


@pytest.mark.skipif(os.environ.get("E2E_NETWORK") != "1", reason="needs E2E_NETWORK=1")
def test_https_reaches_the_real_activation_server(
    tmp_path: Path, services: list[Service]
) -> None:
    """The bundled OpenSSL must find the system's certificates (it did not on Arch)."""
    port = free_port()
    service = start(
        [str(install(tmp_path))],
        {
            "XDG_DATA_HOME": str(tmp_path / "home"),
            "LOCALAPPDATA": str(tmp_path / "home"),
            "RENFE_PORT": str(port),
            "RENFE_NO_BROWSER": "1",
        },
        tmp_path / "https.log",
        f"http://127.0.0.1:{port}",
        "/api/health",
    )
    services.append(service)
    state = wait_until(
        lambda: (
            (body := http_json("GET", f"http://127.0.0.1:{port}/api/activation")[1])[
                "status"
            ]
            and body
        )
    )
    assert state["error"] is None, state


@pytest.mark.skipif(not LINUX or os.geteuid() == 0, reason="POSIX permissions")
def test_it_starts_from_a_folder_it_cannot_write(
    tmp_path: Path, services: list[Service]
) -> None:
    binary = install(tmp_path)
    binary.with_name(binary.name + ".part").write_bytes(b"leftover")
    binary.parent.chmod(0o555)
    try:
        port = free_port()
        services.append(run(binary, tmp_path / "home", port, RENFE_NO_BROWSER="1"))
        assert http_json("GET", f"http://127.0.0.1:{port}/api/health")[0] == 200
    finally:
        binary.parent.chmod(0o755)


def staged_install(tmp_path: Path, new: Path, version: str) -> Path:
    """An older build with a newer one already downloaded and checked next to it."""
    folder = tmp_path / "install"
    folder.mkdir()
    exe = folder / Path(BINARY or "").name
    shutil.copy2(OLD or "", exe)
    shutil.copy2(new, exe.with_name(exe.name + ".new"))
    digest = hashlib.sha256(new.read_bytes()).hexdigest()
    exe.with_name(exe.name + ".new.json").write_text(
        json.dumps({"version": version, "sha256": digest})
    )
    return exe


@pytest.mark.skipif(
    not OLD or not LINUX, reason="needs E2E_BINARY_OLD (Windows: see the CI job)"
)
def test_a_real_build_hands_over_to_the_next(
    tmp_path: Path, services: list[Service], pages: Pages
) -> None:
    exe = staged_install(tmp_path, Path(BINARY or ""), VERSION)
    port = free_port()
    service = run(exe, tmp_path / "home", port, RENFE_NO_BROWSER="1")
    services.append(service)
    page = pages.new(f"http://127.0.0.1:{port}")
    expect(page.locator(".update")).to_contain_text(f"Versión {VERSION} lista.")
    page.get_by_role("button", name="Actualizar").click()
    page.wait_for_function("() => !document.querySelector('.update')", timeout=90_000)
    assert (
        http_json("GET", f"http://127.0.0.1:{port}/api/health")[1]["version"] == VERSION
    )
    assert service.process.wait(timeout=30) == 0
    assert exe.read_bytes() == Path(BINARY or "").read_bytes()
    wait_until(lambda: not exe.with_name(exe.name + ".old").exists(), timeout=60)
    assert os.access(exe, os.X_OK)


@pytest.mark.skipif(
    not OLD or not LINUX, reason="needs E2E_BINARY_OLD (Windows: see the CI job)"
)
def test_a_real_build_that_does_not_start_is_rolled_back(
    tmp_path: Path, services: list[Service], pages: Pages
) -> None:
    broken = tmp_path / "broken"
    broken.write_text("#!/bin/sh\nexit 3\n")
    broken.chmod(0o755)
    exe = staged_install(tmp_path, broken, VERSION)
    old_bytes = Path(OLD or "").read_bytes()
    port = free_port()
    services.append(run(exe, tmp_path / "home", port, RENFE_NO_BROWSER="1"))
    page = pages.new(f"http://127.0.0.1:{port}")
    expect(page.locator(".update")).to_contain_text(f"Versión {VERSION} lista.")
    page.get_by_role("button", name="Actualizar").click()
    expect(page.locator(".update")).to_contain_text(
        f"La versión {VERSION} no arrancó", timeout=60_000
    )
    assert exe.read_bytes() == old_bytes
    assert sorted(p.name for p in exe.parent.iterdir()) == [exe.name]
