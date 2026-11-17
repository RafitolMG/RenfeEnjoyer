import hashlib
import http.server
import os
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import updater as updater_module
from app.activation import UpdateOffer
from app.api.update import get_updater
from app.bot.events import JobState
from app.main import app
from app.updater import UpdateNotReady, Updater, is_newer
from tests.conftest import FUTURE_DATE

OLD_BUILD = b"old build" * 1000
NEW_BUILD = b"new build" * 5000


@pytest.fixture
def download_server() -> Iterator[str]:
    """Serves NEW_BUILD at any path, like GitHub's release download."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(NEW_BUILD)))
            self.end_headers()
            self.wfile.write(NEW_BUILD)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    yield f"http://127.0.0.1:{server.server_port}/RenfeEnjoyer.exe"
    server.shutdown()


@pytest.fixture
def exe(tmp_path: Path) -> Path:
    path = tmp_path / "RenfeEnjoyer.exe"
    path.write_bytes(OLD_BUILD)
    return path


def offer(url: str, version: str = "2.1.0", **changes: object) -> UpdateOffer:
    fields: dict = {
        "version": version,
        "url": url,
        "sha256": hashlib.sha256(NEW_BUILD).hexdigest(),
        "size": len(NEW_BUILD),
        "min_version": None,
    }
    fields.update(changes)
    return UpdateOffer(**fields)


def settle(updater: Updater, timeout: float = 10) -> str:
    deadline = time.monotonic() + timeout
    while updater.state()["state"] == "downloading" and time.monotonic() < deadline:
        time.sleep(0.02)
    return str(updater.state()["state"])


def ready_updater(exe: Path, url: str) -> Updater:
    updater = Updater(exe, current="2.0.0")
    updater.offer(offer(url))
    assert settle(updater) == "ready"
    return updater


def test_versions_compare_as_numbers() -> None:
    assert is_newer("2.0.10", "2.0.9")
    assert not is_newer("2.0.2", "2.0.2")
    assert not is_newer("1.9.9", "2.0.0")
    assert not is_newer(None, "2.0.0")
    assert not is_newer("garbage", "2.0.0")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_the_download_can_be_executed_like_the_running_build(
    exe: Path, download_server: str
) -> None:
    exe.chmod(0o755)
    ready_updater(exe, download_server)
    assert exe.with_name("RenfeEnjoyer.exe.new").stat().st_mode & 0o777 == 0o755


def test_a_newer_version_is_downloaded_and_checked(
    exe: Path, download_server: str
) -> None:
    updater = ready_updater(exe, download_server)
    assert exe.with_name("RenfeEnjoyer.exe.new").read_bytes() == NEW_BUILD
    assert exe.read_bytes() == OLD_BUILD
    state = updater.state()
    assert (state["version"], state["progress"], state["error"]) == ("2.1.0", 1.0, None)


@pytest.mark.parametrize("tampered", [{"sha256": "0" * 64}, {"size": len(NEW_BUILD) + 1}])
def test_a_download_that_does_not_match_is_thrown_away(
    exe: Path, download_server: str, tampered: dict
) -> None:
    updater = Updater(exe, current="2.0.0")
    updater.offer(offer(download_server, **tampered))
    assert settle(updater) == "failed"
    assert sorted(p.name for p in exe.parent.iterdir()) == ["RenfeEnjoyer.exe"]


@pytest.mark.parametrize("version", ["2.0.0", "1.9.0"])
def test_nothing_older_or_equal_is_downloaded(
    exe: Path, download_server: str, version: str
) -> None:
    updater = Updater(exe, current="2.0.0")
    updater.offer(offer(download_server, version=version))
    assert updater.state()["state"] == "none"


def test_a_withdrawn_version_is_not_installed(exe: Path, download_server: str) -> None:
    updater = ready_updater(exe, download_server)
    updater.offer(None)
    assert updater.state()["state"] == "none"
    assert not exe.with_name("RenfeEnjoyer.exe.new").exists()


def test_a_finished_download_survives_a_restart(exe: Path, download_server: str) -> None:
    ready_updater(exe, download_server)
    restarted = Updater(exe, current="2.0.0")
    assert (restarted.state()["state"], restarted.state()["version"]) == (
        "ready",
        "2.1.0",
    )


def test_a_staged_file_changed_since_is_dropped(exe: Path, download_server: str) -> None:
    ready_updater(exe, download_server)
    exe.with_name("RenfeEnjoyer.exe.new").write_bytes(b"swapped")
    restarted = Updater(exe, current="2.0.0")
    assert restarted.state()["state"] == "none"
    assert not exe.with_name("RenfeEnjoyer.exe.new").exists()


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions"
)
def test_a_read_only_folder_falls_back_to_a_manual_download(
    exe: Path, download_server: str
) -> None:
    exe.parent.chmod(0o555)
    try:
        updater = Updater(exe, current="2.0.0")
        updater.offer(offer(download_server))
        assert settle(updater) == "manual"
        assert updater.state()["download_url"] == download_server
    finally:
        exe.parent.chmod(0o755)


def test_a_minimum_above_this_version_is_required(
    exe: Path, download_server: str
) -> None:
    updater = Updater(exe, current="2.0.0")
    updater.offer(offer(download_server, min_version="2.0.0"))
    assert not updater.required()
    updater.offer(offer(download_server, min_version="2.1.0"))
    assert updater.required()


def test_applying_needs_a_checked_download(exe: Path) -> None:
    updater = Updater(exe, current="2.0.0")
    updater.stop_server = lambda: None
    with pytest.raises(UpdateNotReady):
        updater.apply()


def test_applying_stops_the_server(exe: Path, download_server: str) -> None:
    updater = ready_updater(exe, download_server)
    stopped = threading.Event()
    updater.stop_server = stopped.set
    updater.apply()
    assert updater.handover_requested
    assert updater.state()["state"] == "applying"
    assert stopped.wait(timeout=5)


class FakeProcess:
    def __init__(self) -> None:
        self.killed = False

    def poll(self) -> int | None:
        return -9 if self.killed else None

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return -9


def test_the_new_version_takes_over(exe: Path, download_server: str) -> None:
    updater = ready_updater(exe, download_server)
    launched: list[Path] = []

    def launcher(path: Path) -> FakeProcess:
        launched.append(path)
        return FakeProcess()

    updater._launch = launcher
    updater._probe = lambda port: "2.1.0"
    assert updater.handover(8000)
    assert launched == [exe]
    assert exe.read_bytes() == NEW_BUILD
    assert exe.with_name("RenfeEnjoyer.exe.old").read_bytes() == OLD_BUILD


def test_a_new_version_that_never_answers_is_rolled_back(
    exe: Path, download_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(updater_module, "HANDOVER_TIMEOUT", 1.0)
    updater = ready_updater(exe, download_server)
    process = FakeProcess()
    updater._launch = lambda path: process
    updater._probe = lambda port: "2.0.0"

    assert not updater.handover(8000)
    assert process.killed
    assert exe.read_bytes() == OLD_BUILD
    assert sorted(p.name for p in exe.parent.iterdir()) == ["RenfeEnjoyer.exe"]
    state = updater.state()
    assert state["state"] == "failed"
    assert state["error"] == "La versión 2.1.0 no arrancó; sigues con la 2.0.0"
    # Not downloaded again on the next check.
    updater.offer(offer(download_server))
    assert updater.state()["state"] == "failed"


def test_a_checkout_never_updates(download_server: str) -> None:
    updater = Updater(None, current="2.0.0")
    updater.offer(offer(download_server))
    assert updater.state()["state"] == "none"
    assert updater.state()["supported"] is False


# --- API -------------------------------------------------------------------------------


@pytest.fixture
def ready(exe: Path, download_server: str) -> Iterator[Updater]:
    updater = ready_updater(exe, download_server)
    updater.stop_server = lambda: None
    app.dependency_overrides[get_updater] = lambda: updater
    yield updater
    app.dependency_overrides.pop(get_updater, None)


def test_the_health_check_names_the_version(client: TestClient) -> None:
    assert client.get("/api/health").json()["version"] == updater_module.APP_VERSION


def test_the_spa_reads_the_update_state(client: TestClient, ready: Updater) -> None:
    body = client.get("/api/update").json()
    assert (body["state"], body["version"], body["current"]) == (
        "ready",
        "2.1.0",
        "2.0.0",
    )


def test_installing_without_a_download_conflicts(client: TestClient) -> None:
    assert client.post("/api/update").status_code == 409


def test_installing_starts_the_handover(client: TestClient, ready: Updater) -> None:
    assert client.post("/api/update").status_code == 202
    assert ready.handover_requested


def test_no_update_while_a_search_runs(
    client: TestClient, profile: dict, ready: Updater, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()

    def blocking(request, reporter, interaction) -> None:
        reporter.state(JobState.POLLING, "Buscando plazas")
        started.set()
        interaction.release.wait(timeout=5)

    monkeypatch.setattr("app.bot.runner.run_search", blocking)
    client.post(
        "/api/jobs/current",
        json={"user_id": profile["id"], "journey_type": "ida", "date": FUTURE_DATE},
    )
    assert started.wait(timeout=5)
    response = client.post("/api/update")
    assert response.status_code == 409
    assert not ready.handover_requested
    client.post("/api/jobs/current/stop")


def test_a_version_below_the_minimum_cannot_search(
    client: TestClient, profile: dict, ready: Updater, download_server: str
) -> None:
    ready.offer(offer(download_server, min_version="2.1.0"))
    response = client.post(
        "/api/jobs/current",
        json={"user_id": profile["id"], "journey_type": "ida", "date": FUTURE_DATE},
    )
    assert response.status_code == 403
    assert "actualiza" in response.json()["detail"]
