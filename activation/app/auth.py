"""Admin login: a password form, a server-side session cookie and password changes.

ADMIN_PASSWORD is only the initial password. Once the admin changes it, its scrypt hash in
the database wins and the variable is ignored. `python -m app.reset_admin` drops that hash
to fall back to the variable when the password is forgotten.
"""

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, delete

from app import config
from app.db import SessionDep
from app.models import AdminCredential, AdminSession, as_utc, utcnow

SESSION_COOKIE = "renfe_admin"
SESSION_LIFETIME = timedelta(hours=12)
MIN_PASSWORD_LENGTH = 12

PAGES = Path(__file__).parent
LOGIN_PAGE = (PAGES / "login.html").read_text(encoding="utf-8")
ADMIN_PAGE = (PAGES / "admin.html").read_text(encoding="utf-8")

router = APIRouter()


# --- passwords -------------------------------------------------------------------------

# scrypt from the standard library: memory-hard, so a stolen hash is slow to crack.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return hmac.compare_digest(candidate, bytes.fromhex(digest))


def using_initial_password(session: Session) -> bool:
    return session.get(AdminCredential, 1) is None


def check_password(session: Session, password: str) -> bool:
    credential = session.get(AdminCredential, 1)
    if credential is not None:
        return verify_password(password, credential.password_hash)
    return hmac.compare_digest(password.encode(), config.ADMIN_PASSWORD.encode())


# --- throttling ------------------------------------------------------------------------

# Failed attempts per client address, so guessing the password is impractical.
_FAILURES: defaultdict[str, deque[float]] = defaultdict(deque)
_FAILURES_LOCK = threading.Lock()
MAX_FAILURES = 10
FAILURE_WINDOW = 600.0


def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


def _ensure_not_locked_out(request: Request) -> None:
    now = time.monotonic()
    with _FAILURES_LOCK:
        failures = _FAILURES[_client(request)]
        while failures and now - failures[0] > FAILURE_WINDOW:
            failures.popleft()
        if len(failures) >= MAX_FAILURES:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Demasiados intentos fallidos; espera unos minutos.",
            )


def _record_failure(request: Request) -> None:
    with _FAILURES_LOCK:
        _FAILURES[_client(request)].append(time.monotonic())


# --- sessions --------------------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _active_session(request: Request, session: Session) -> AdminSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    admin = session.get(AdminSession, _token_hash(token))
    if admin is None or as_utc(admin.expires_at) <= utcnow():
        return None
    return admin


def require_admin(request: Request, session: SessionDep) -> AdminSession:
    admin = _active_session(request, session)
    if admin is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Inicia sesión para continuar")
    return admin


AdminDep = Annotated[AdminSession, Depends(require_admin)]


class Login(BaseModel):
    password: str = Field(max_length=200)


@router.post("/api/v1/admin/login", status_code=status.HTTP_204_NO_CONTENT)
def login(
    payload: Login, request: Request, response: Response, session: SessionDep
) -> None:
    _ensure_not_locked_out(request)
    if not check_password(session, payload.password):
        _record_failure(request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Contraseña incorrecta")

    session.exec(delete(AdminSession).where(col(AdminSession.expires_at) <= utcnow()))
    token = secrets.token_urlsafe(32)
    session.add(
        AdminSession(
            token_hash=_token_hash(token), expires_at=utcnow() + SESSION_LIFETIME
        )
    )
    session.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        # Behind Traefik the scheme comes from X-Forwarded-Proto, so this is https in
        # production and plain http only in tests and local runs.
        secure=request.url.scheme == "https",
        samesite="strict",
    )


@router.post("/api/v1/admin/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, session: SessionDep) -> None:
    admin = _active_session(request, session)
    if admin is not None:
        session.delete(admin)
        session.commit()
    response.delete_cookie(SESSION_COOKIE)


@router.get("/api/v1/admin/me")
def me(admin: AdminDep, session: SessionDep) -> dict[str, Any]:
    return {
        "using_initial_password": using_initial_password(session),
        "session_expires_at": as_utc(admin.expires_at).isoformat(),
    }


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


@router.post("/api/v1/admin/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: PasswordChange, request: Request, admin: AdminDep, session: SessionDep
) -> None:
    """Replace the admin password and sign out every other session."""
    # A stolen session cookie must not be enough to lock the admin out, so the current
    # password is asked for again, and guesses count towards the same lockout.
    _ensure_not_locked_out(request)
    if not check_password(session, payload.current_password):
        _record_failure(request)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "La contraseña actual no es correcta"
        )
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "La nueva contraseña debe ser distinta"
        )

    credential = session.get(AdminCredential, 1) or AdminCredential(password_hash="")
    credential.password_hash = hash_password(payload.new_password)
    credential.updated_at = utcnow()
    session.add(credential)
    session.exec(
        delete(AdminSession).where(col(AdminSession.token_hash) != admin.token_hash)
    )
    session.commit()


# --- pages -----------------------------------------------------------------------------


@router.get("/admin/login", response_class=HTMLResponse, response_model=None)
def login_page(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    if _active_session(request, session) is not None:
        return RedirectResponse("/admin", status.HTTP_303_SEE_OTHER)
    return HTMLResponse(LOGIN_PAGE)


@router.get("/admin", response_class=HTMLResponse, response_model=None)
def admin_page(request: Request, session: SessionDep) -> HTMLResponse | RedirectResponse:
    if _active_session(request, session) is None:
        return RedirectResponse("/admin/login", status.HTTP_303_SEE_OTHER)
    return HTMLResponse(ADMIN_PAGE)
