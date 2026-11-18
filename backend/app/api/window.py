from fastapi import APIRouter, HTTPException, status

from app import window

router = APIRouter(prefix="/api/window", tags=["window"])


@router.get("")
def read_window() -> dict[str, bool]:
    """Whether closing the page quits the app, so the SPA asks first during a search."""
    return {"closes_app": window.state.closes_app}


@router.post("/focus", status_code=status.HTTP_204_NO_CONTENT)
def focus_window() -> None:
    """Bring the native window to the front: a second launch of the executable asks."""
    focus = window.state.focus
    if focus is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esta instancia no tiene ventana")
    focus()
