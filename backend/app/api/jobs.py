import asyncio
import contextlib
from datetime import date
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from app.api.activation import ActivatorDep
from app.api.deps import SessionDep
from app.api.update import UpdaterDep
from app.bot.renfe import PromptNotOpen, SearchRequest
from app.bot.runner import JobConflict, TrainNotListed, job_manager
from app.db.models import User
from app.schemas import (
    JobStartRequest,
    TrainChoice,
    VerificationCode,
    parse_renfe_date,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/current")
def get_current_job() -> dict[str, Any]:
    return job_manager.status()


@router.post("/current", status_code=status.HTTP_202_ACCEPTED)
def start_job(
    payload: JobStartRequest,
    session: SessionDep,
    activator: ActivatorDep,
    updater: UpdaterDep,
) -> dict[str, Any]:
    if not activator.is_authorized():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Este equipo no está activado: solicita acceso en la pantalla de activación.",
        )
    if updater.required():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Esta versión ya no funciona con Renfe: actualiza para seguir buscando.",
        )
    user = session.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Perfil no encontrado")
    if not user.password:
        # Saved under a key this computer no longer has (see app/credentials.py).
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No se puede leer la contraseña guardada de este perfil: vuelve a "
            "escribirla en Gestionar.",
        )
    if parse_renfe_date(payload.date) < date.today():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Esa fecha ya ha pasado"
        )

    request = SearchRequest(
        departure_time=payload.departure_time,
        journey_type=payload.journey_type,
        date=payload.date,
        email=user.email,
        password=user.password,
        abono=user.abono,
    )
    try:
        return job_manager.start(request, user.username)
    except JobConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/current/stop", status_code=status.HTTP_202_ACCEPTED)
def stop_job() -> dict[str, Any]:
    try:
        job_manager.cancel()
    except JobConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return job_manager.status()


@router.post("/current/release", status_code=status.HTTP_202_ACCEPTED)
def release_job() -> dict[str, Any]:
    """Close the browser after the user has completed the purchase by hand."""
    try:
        job_manager.release()
    except JobConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return job_manager.status()


@router.post("/current/code", status_code=status.HTTP_202_ACCEPTED)
def submit_verification_code(payload: VerificationCode) -> dict[str, Any]:
    """Supply the code Renfe sent by SMS or email while the bot waits on it."""
    try:
        job_manager.submit_code(payload.code)
    except PromptNotOpen as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return job_manager.status()


@router.post("/current/train", status_code=status.HTTP_202_ACCEPTED)
def choose_train(payload: TrainChoice) -> dict[str, Any]:
    """Pick which of the listed trains the bot should hunt a seat on."""
    try:
        job_manager.submit_train(payload.departure_time)
    except PromptNotOpen as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except TrainNotListed as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return job_manager.status()


@router.websocket("/stream")
async def stream_job(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = job_manager.subscribe()
    try:
        # Replay so a client joining mid-run still renders the full progress log.
        await websocket.send_json(
            {
                "type": "snapshot",
                "status": job_manager.status(),
                "events": job_manager.history(),
            }
        )
        # A client that leaves is otherwise only noticed at the next event, which may
        # never come; the desktop app counts the clients to know its window is closed.
        gone = asyncio.create_task(_until_disconnected(websocket))
        try:
            while True:
                next_message = asyncio.ensure_future(queue.get())
                await asyncio.wait(
                    {next_message, gone}, return_when=asyncio.FIRST_COMPLETED
                )
                if gone.done():
                    next_message.cancel()
                    return
                if (message := next_message.result()) is None:
                    break
                await websocket.send_json(message)
        finally:
            gone.cancel()
        # The server is stopping for an update: the client reconnects to the new one.
        await websocket.close(code=status.WS_1012_SERVICE_RESTART)
    except WebSocketDisconnect:
        pass
    finally:
        job_manager.unsubscribe(queue)


async def _until_disconnected(websocket: WebSocket) -> None:
    """Return once the client has gone; the stream sends, it never expects messages."""
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass
