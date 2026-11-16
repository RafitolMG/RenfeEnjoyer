from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.activation import Activator, get_activator

router = APIRouter(prefix="/api/activation", tags=["activation"])

ActivatorDep = Annotated[Activator, Depends(get_activator)]


class AccessRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    contact: str = Field(min_length=1, max_length=120)


@router.get("")
def read_activation(activator: ActivatorDep) -> dict[str, Any]:
    return activator.state()


@router.post("/request")
def request_access(payload: AccessRequest, activator: ActivatorDep) -> dict[str, Any]:
    """Ask the activation server to let this computer in."""
    activator.request_access(payload.name.strip(), payload.contact.strip())
    return activator.state()


@router.post("/refresh")
def refresh_activation(activator: ActivatorDep) -> dict[str, Any]:
    """Ask the server again now, e.g. right after the admin has approved this computer."""
    activator.refresh(force=True)
    return activator.state()
