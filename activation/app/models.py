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
