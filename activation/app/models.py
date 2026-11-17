from datetime import UTC, datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


class MachineStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REVOKED = "revoked"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Machine(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # A hash the desktop app derives from the computer's own id, never the raw id.
    machine_id: str = Field(index=True, unique=True)
    name: str
    contact: str
    status: MachineStatus = MachineStatus.PENDING
    requested_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None
    last_seen_at: datetime | None = None
    app_version: str | None = None
    # Where it asked from and last checked in from (behind Traefik: the client's address).
    request_ip: str | None = None
    last_ip: str | None = None
    # Reported by the app since 2.0.4; older versions leave them empty.
    hostname: str | None = None
    os_user: str | None = None
    os_name: str | None = None
    locale: str | None = None
    timezone: str | None = None


class CheckInRecord(SQLModel, table=True):
    """One check-in of a known computer: when, from where and on which version."""

    __tablename__ = "checkin"

    id: int | None = Field(default=None, primary_key=True)
    machine_id: int = Field(index=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    ip: str | None = None
    app_version: str | None = None


def as_utc(moment: datetime) -> datetime:
    """SQLite hands datetimes back without their zone; every one stored here is UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class AdminCredential(SQLModel, table=True):
    """The admin password once it has been changed; until then ADMIN_PASSWORD applies."""

    id: int = Field(default=1, primary_key=True)
    password_hash: str
    updated_at: datetime = Field(default_factory=utcnow)


class AdminSession(SQLModel, table=True):
    # Only the token's hash is stored, so a copy of the database logs nobody in.
    token_hash: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime


class PublishedRelease(SQLModel, table=True):
    """A version the admin has released to the apps (see app/releases.py)."""

    id: int | None = Field(default=None, primary_key=True)
    version: str = Field(index=True, unique=True)
    url: str
    # The Windows build. Pinned when publishing: a file replaced on GitHub afterwards is
    # refused by the app.
    sha256: str
    size: int
    published_at: datetime = Field(default_factory=utcnow)
    # The Linux build, from 2.0.5 on; empty for releases that had none.
    linux_url: str | None = None
    linux_sha256: str | None = None
    linux_size: int | None = None


class UpdatePolicy(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    # Below it the app refuses to search until it updates.
    min_version: str | None = None
