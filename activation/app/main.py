"""Activation server: decides which computers may run the Windows build of Renfe Enjoyer.

Anyone can ask for access from the app; only the admin can approve or revoke a request.
An approved computer gets a license signed with this server's private key that lasts
`LICENSE_DAYS`, and every check renews it, so the app keeps working through short outages
while a revocation still takes effect within that window.
"""

import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, SQLModel, col, select

from app import auth, config
from app.auth import AdminDep
from app.db import SessionDep, engine
from app.models import Machine, MachineStatus, utcnow
from app.signing import issue_license, load_private_key, public_key_of


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not config.PRIVATE_KEY or not config.ADMIN_PASSWORD:
        raise RuntimeError("Faltan ACTIVATION_PRIVATE_KEY o ADMIN_PASSWORD")
    app.state.signing_key = load_private_key(config.PRIVATE_KEY)
    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    yield


app = FastAPI(
    title="Renfe Enjoyer · Activación",
    lifespan=lifespan,
    # The API is for the app and the admin page, not for browsing.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.include_router(auth.router)

PAGES = Path(__file__).parent
HOME_PAGE = (PAGES / "index.html").read_text(encoding="utf-8")
# python:3.12-slim has no /etc/mime.types and Python 3.12 does not know .webp on its own,
# so the logo went out as application/octet-stream.
mimetypes.add_type("image/webp", ".webp")
app.mount("/static", StaticFiles(directory=PAGES / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    """Public landing page: what the app does and where to download it."""
    return HOME_PAGE


# --- Desktop app -----------------------------------------------------------------------

# The app hashes the computer's id into 64 hex characters.
MachineId = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CheckIn(BaseModel):
    machine_id: MachineId
    app_version: str | None = Field(default=None, max_length=40)


class AccessRequest(CheckIn):
    name: str = Field(min_length=1, max_length=80)
    contact: str = Field(min_length=1, max_length=120)


def _answer(request: Request, machine: Machine | None) -> dict[str, Any]:
    if machine is None:
        return {"status": "unknown", "license": None}
    license_ = None
    if machine.status == MachineStatus.APPROVED:
        license_ = issue_license(
            request.app.state.signing_key, machine.machine_id, config.LICENSE_DAYS
        )
    return {"status": machine.status.value, "license": license_}


def _find(session: Session, machine_id: str) -> Machine | None:
    return session.exec(select(Machine).where(Machine.machine_id == machine_id)).first()


def _touch(session: Session, machine: Machine, app_version: str | None) -> None:
    machine.last_seen_at = utcnow()
    if app_version:
        machine.app_version = app_version
    session.add(machine)
    session.commit()
    session.refresh(machine)


@app.post("/api/v1/check")
def check(payload: CheckIn, request: Request, session: SessionDep) -> dict[str, Any]:
    """Where this computer stands; approved ones get a fresh license."""
    machine = _find(session, payload.machine_id)
    if machine is not None:
        _touch(session, machine, payload.app_version)
    return _answer(request, machine)


@app.post("/api/v1/requests")
def request_access(
    payload: AccessRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    """Ask for access. Asking again while pending updates the name and contact."""
    machine = _find(session, payload.machine_id)
    if machine is None:
        pending = session.exec(
            select(func.count())
            .select_from(Machine)
            .where(Machine.status == MachineStatus.PENDING)
        ).one()
        if pending >= config.MAX_PENDING:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Hay demasiadas solicitudes pendientes; inténtalo más tarde.",
            )
        machine = Machine(
            machine_id=payload.machine_id, name=payload.name, contact=payload.contact
        )
    elif machine.status == MachineStatus.PENDING:
        machine.name, machine.contact = payload.name, payload.contact
    # An approved or revoked computer keeps its decision: asking again changes nothing.
    _touch(session, machine, payload.app_version)
    return _answer(request, machine)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/public-key")
def public_key(request: Request) -> dict[str, str]:
    """Lets anyone check which key the app should be built with."""
    return {"public_key": public_key_of(request.app.state.signing_key)}


# --- Admin (login, sessions and password live in app/auth.py) -----------------------


@app.get("/api/v1/admin/machines")
def list_machines(_: AdminDep, session: SessionDep) -> list[Machine]:
    return list(session.exec(select(Machine).order_by(col(Machine.requested_at).desc())))


def _decide(session: Session, machine_id: int, decision: MachineStatus) -> Machine:
    machine = session.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Equipo no encontrado")
    machine.status = decision
    machine.decided_at = utcnow()
    session.add(machine)
    session.commit()
    session.refresh(machine)
    return machine


@app.post("/api/v1/admin/machines/{machine_id}/approve")
def approve(machine_id: int, _: AdminDep, session: SessionDep) -> Machine:
    return _decide(session, machine_id, MachineStatus.APPROVED)


@app.post("/api/v1/admin/machines/{machine_id}/revoke")
def revoke(machine_id: int, _: AdminDep, session: SessionDep) -> Machine:
    return _decide(session, machine_id, MachineStatus.REVOKED)


@app.delete("/api/v1/admin/machines/{machine_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(machine_id: int, _: AdminDep, session: SessionDep) -> None:
    """Forget a computer. It can then ask again, unlike a revoked one."""
    machine = session.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Equipo no encontrado")
    session.delete(machine)
    session.commit()
