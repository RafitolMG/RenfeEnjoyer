from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import geo, main
from app.db import add_missing_columns, engine
from app.models import CheckInRecord
from tests.test_activation import MACHINE, ask, machine_db_id

DEVICE = {
    "hostname": "PORTATIL-ANA",
    "user": "ana",
    "os": "Windows 11 (10.0.22631)",
    "locale": "es_ES",
    "timezone": "Hora estándar romance (UTC+0100)",
}


def checkin(client: TestClient, **extra: object) -> None:
    body = {"machine_id": MACHINE, "app_version": "2.0.4", **extra}
    assert client.post("/api/v1/check", json=body).status_code == 200


def listed(admin: TestClient) -> dict:
    return next(
        m
        for m in admin.get("/api/v1/admin/machines").json()
        if m["machine_id"] == MACHINE
    )


def test_the_request_records_where_it_came_from(admin: TestClient) -> None:
    ask(admin)
    machine = listed(admin)
    assert machine["request_ip"] == machine["last_ip"] == "testclient"
    assert machine["hostname"] is None


def test_the_app_reports_the_computer(admin: TestClient) -> None:
    ask(admin, device=DEVICE)
    machine = listed(admin)
    assert (machine["hostname"], machine["os_user"], machine["os_name"]) == (
        "PORTATIL-ANA",
        "ana",
        "Windows 11 (10.0.22631)",
    )
    assert (machine["locale"], machine["timezone"]) == (
        "es_ES",
        "Hora estándar romance (UTC+0100)",
    )


def test_older_apps_without_device_details_are_still_accepted(admin: TestClient) -> None:
    ask(admin, device=DEVICE)
    checkin(admin)
    assert listed(admin)["hostname"] == "PORTATIL-ANA"


def test_oversized_details_are_refused(client: TestClient) -> None:
    body = {"machine_id": MACHINE, "device": {"hostname": "x" * 121}}
    assert client.post("/api/v1/check", json=body).status_code == 422


def test_checkins_are_recorded_but_not_every_minute(admin: TestClient) -> None:
    ask(admin)
    checkin(admin)
    checkin(admin)
    history = admin.get(f"/api/v1/admin/machines/{machine_db_id(admin)}/checkins").json()
    # The request and the first 2.0.4 check differ in version; the repeat is skipped.
    assert [entry["app_version"] for entry in history] == ["2.0.4", None]
    assert {"at", "ip", "location"} <= history[0].keys()


def test_old_checkins_are_dropped(admin: TestClient) -> None:
    ask(admin)
    db_id = machine_db_id(admin)
    with Session(engine) as session:
        session.add(
            CheckInRecord(
                machine_id=db_id,
                at=main.utcnow() - main.CHECKIN_RETENTION - timedelta(days=1),
                ip="1.2.3.4",
            )
        )
        session.commit()
    checkin(admin)
    ips = [e["ip"] for e in admin.get(f"/api/v1/admin/machines/{db_id}/checkins").json()]
    assert "1.2.3.4" not in ips


def test_deleting_a_computer_drops_its_history(admin: TestClient) -> None:
    ask(admin)
    db_id = machine_db_id(admin)
    assert admin.delete(f"/api/v1/admin/machines/{db_id}").status_code == 204
    with Session(engine) as session:
        assert session.exec(select(CheckInRecord)).all() == []


def test_history_needs_the_admin(client: TestClient) -> None:
    assert client.get("/api/v1/admin/machines/1/checkins").status_code == 401


def test_columns_added_since_deployment_are_created(client: TestClient) -> None:
    """The live database predates the requester columns; create_all would not add them."""
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE machine")
        connection.exec_driver_sql(
            "CREATE TABLE machine (id INTEGER PRIMARY KEY, machine_id VARCHAR UNIQUE, "
            "name VARCHAR, contact VARCHAR, status VARCHAR, requested_at DATETIME, "
            "decided_at DATETIME, last_seen_at DATETIME, app_version VARCHAR)"
        )
    add_missing_columns()
    with engine.begin() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(machine)")
        }
    assert {"request_ip", "last_ip", "hostname", "os_user", "timezone"} <= columns
    add_missing_columns()  # idempotent
    ask(client)


class FakeReader:
    def get(self, ip: str) -> dict | None:
        if ip != "88.26.0.1":
            return None
        return {
            "city": {"names": {"en": "Coslada"}},
            "subdivisions": [{"names": {"en": "Madrid", "es": "Comunidad de Madrid"}}],
            "country": {"names": {"en": "Spain", "es": "España"}},
        }


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geo, "_reader", FakeReader())
    geo.locate.cache_clear()
    yield
    geo.locate.cache_clear()


def test_locations_read_in_spanish_where_available(reader: None) -> None:
    assert geo.locate("88.26.0.1") == "Coslada, Comunidad de Madrid, España"
    assert geo.locate("10.0.0.1") is None
    assert geo.locate(None) is None
    assert geo.locate("not an ip") is None


def test_without_the_database_locations_are_unknown() -> None:
    geo.locate.cache_clear()
    assert geo.locate("88.26.0.1") is None
