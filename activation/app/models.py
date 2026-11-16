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
