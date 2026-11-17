from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator
from sqlmodel import SQLModel

DEPARTURE_PATTERN = r"^\d{1,2}:\d{2}$"
RENFE_DATE_FORMAT = "%d/%m/%Y"

# Blank or whitespace-only values would only fail later, inside Renfe's site.
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# A password is sent as typed, so it is checked for blanks without being trimmed.
Password = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]


class UserCreate(SQLModel):
    username: Text
    email: Text
    password: Password
    abono: Text


class UserUpdate(SQLModel):
    username: Text | None = None
    email: Text | None = None
    # Blank means "keep the stored one", so it is not constrained here.
    password: str | None = None
    abono: Text | None = None


class UserRead(SQLModel):
    """Public view of a profile. The Renfe password is never sent to the client."""

    id: int
    username: str
    email: str
    abono: str


class JobStartRequest(SQLModel):
    user_id: int
    # Omitted, the bot lists the day's trains and waits for the user to pick one.
    departure_time: str | None = Field(default=None, pattern=DEPARTURE_PATTERN)
    journey_type: Literal["ida", "vuelta"]
    date: str = Field(pattern=r"^\d{2}/\d{2}/\d{4}$")

    @field_validator("date")
    @classmethod
    def _real_date(cls, value: str) -> str:
        parse_renfe_date(value)
        return value


class VerificationCode(SQLModel):
    code: str = Field(min_length=4, max_length=12, pattern=r"^[A-Za-z0-9]+$")


class TrainChoice(SQLModel):
    departure_time: str = Field(pattern=DEPARTURE_PATTERN)


def parse_renfe_date(value: str) -> date:
    try:
        return datetime.strptime(value, RENFE_DATE_FORMAT).date()
    except ValueError as exc:
        raise ValueError("Fecha no válida") from exc
