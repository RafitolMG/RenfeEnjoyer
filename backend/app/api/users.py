import shutil

from fastapi import APIRouter, HTTPException, status
from sqlmodel import Session, select

from app.api.deps import SessionDep
from app.bot.driver import profile_dir_for
from app.bot.runner import job_manager
from app.db.models import User
from app.schemas import UserCreate, UserRead, UserUpdate

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=list[UserRead])
def list_users(session: SessionDep) -> list[User]:
    return list(session.exec(select(User).order_by(User.username)).all())


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, session: SessionDep) -> User:
    _ensure_name_is_free(session, payload.username)
    user = User.model_validate(payload.model_dump())
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserRead)
def update_user(user_id: int, payload: UserUpdate, session: SessionDep) -> User:
    user = _get_or_404(session, user_id)

    changes = payload.model_dump(exclude_unset=True)
    # A blank password means "keep the stored one" — the client never receives it.
    if not (changes.get("password") or "").strip():
        changes.pop("password", None)

    if changes.get("username") not in (None, user.username):
        _ensure_name_is_free(session, changes["username"])

    old_email = user.email
    moves_account = changes.get("email") is not None and profile_dir_for(
        changes["email"]
    ) != profile_dir_for(old_email)
    if moves_account:
        _ensure_browser_is_free()

    for field, value in changes.items():
        if value is not None:
            setattr(user, field, value)

    session.add(user)
    session.commit()
    session.refresh(user)
    if moves_account:
        _drop_unused_browser_profile(session, old_email)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, session: SessionDep) -> None:
    user = _get_or_404(session, user_id)
    _ensure_browser_is_free()
    session.delete(user)
    session.commit()
    _drop_unused_browser_profile(session, user.email)


def _ensure_browser_is_free() -> None:
    # The running browser holds its profile open, so it cannot be removed under it.
    if job_manager.is_active():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Hay una búsqueda en curso usando el navegador",
        )


def _drop_unused_browser_profile(session: Session, email: str) -> None:
    """Remove an account's browser profile, and with it its Renfe session.

    Kept while another profile still uses the account: one Renfe account can hold
    several passes.
    """
    directory = profile_dir_for(email)
    users = session.exec(select(User)).all()
    if all(profile_dir_for(other.email) != directory for other in users):
        shutil.rmtree(directory, ignore_errors=True)


def _ensure_name_is_free(session: Session, username: str) -> None:
    # Checked up front: the unique index would otherwise surface as a bare 500.
    if session.exec(select(User).where(User.username == username)).first() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ya existe un perfil con ese nombre"
        )


def _get_or_404(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Perfil no encontrado")
    return user
