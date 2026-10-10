"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

from backend.app.config import settings as settings_conf
from backend.app.database import init_db
from backend.app.api import ai, audit, auth, batches, dashboard, demo, exceptions, reconciliation, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="AI Finance Controller",
    description="AI-assisted financial reconciliation platform (Track 04).",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings_conf.cors_origins.split(",") if settings_conf.cors_origins != "*" else ["*"],
    allow_credentials=False if settings_conf.cors_origins == "*" else True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(batches.router)
app.include_router(reconciliation.router)
app.include_router(exceptions.router)
app.include_router(dashboard.router)
app.include_router(audit.router)
app.include_router(demo.router)
app.include_router(ai.router)
app.include_router(settings.router)

@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "ai-finance-controller"}


# Single-file viewer — mounted last so /api/* and /health routes win.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")