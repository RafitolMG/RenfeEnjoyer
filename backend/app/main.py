import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import activation, jobs, session, update, users
from app.bot.runner import job_manager
from app.config import APP_VERSION, CORS_ORIGINS, FRONTEND_DIST
from app.db.session import init_db

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    # The bot runs on a worker thread and needs this loop to publish progress.
    job_manager.bind_loop(asyncio.get_running_loop())
    yield


app = FastAPI(title="Renfe Enjoyer", version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router)
app.include_router(jobs.router)
app.include_router(session.router)
app.include_router(activation.router)
app.include_router(update.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    # The version tells the SPA, and an update handover, which build answers.
    return {"status": "ok", "version": APP_VERSION}


# Mounted last so the API routes above take precedence over the catch-all.
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="spa")
