from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.activation import ActivatorDep
from app.bot.runner import job_manager
from app.updater import UpdateNotReady, Updater, get_updater

router = APIRouter(prefix="/api/update", tags=["update"])

UpdaterDep = Annotated[Updater, Depends(get_updater)]


@router.get("")
def read_update(updater: UpdaterDep, activator: ActivatorDep) -> dict[str, Any]:
    # Offers arrive with the activation check, which this keeps from going stale.
    if activator.required:
        activator.refresh()
    return updater.state()


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def apply_update(updater: UpdaterDep) -> dict[str, Any]:
    """Install the downloaded version: this server stops and the new one takes over."""
    if job_manager.is_active():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Termina la búsqueda en curso antes de actualizar",
        )
    try:
        updater.apply()
    except UpdateNotReady as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return updater.state()
