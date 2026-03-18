"""
MEDI-COMPLY — Application entry point.

Starts a FastAPI server with:
* ``/health`` – readiness / liveness probe
* ``/status`` – system overview (agent count, bus stats)
* On startup: initializes structured logging and the async message bus.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from medi_comply.core.config import get_settings
from medi_comply.core.logger import get_logger, setup_logging
from medi_comply.core.message_bus import AsyncMessageBus

# ---------------------------------------------------------------------------
# Lifespan (startup & shutdown)
# ---------------------------------------------------------------------------

message_bus = AsyncMessageBus()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager — runs on startup and shutdown."""
    settings = get_settings()
    setup_logging(log_level=settings.log_level)
    logger = get_logger(agent_name="main")
    logger.info(
        event="startup",
        app_name=settings.app_name,
        environment=settings.environment,
    )
    yield
    await message_bus.stop()
    logger.info(event="shutdown", app_name=settings.app_name)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="MEDI-COMPLY",
    description=(
        "Multi-agent healthcare AI system for clinical coding, "
        "compliance, prior-authorization, and claims adjudication."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["ops"])
async def health() -> JSONResponse:
    """Readiness / liveness probe."""
    return JSONResponse(
        content={
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "0.1.0",
        }
    )


@app.get("/status", tags=["ops"])
async def status() -> JSONResponse:
    """System overview — message bus statistics."""
    return JSONResponse(
        content={
            "app": get_settings().app_name,
            "environment": get_settings().environment,
            "message_bus": {
                "total_messages": len(message_bus.message_history),
                "total_responses": len(message_bus.response_history),
                "dead_letters": len(message_bus.dead_letters),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
