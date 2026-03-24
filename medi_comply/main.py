"""
MEDI-COMPLY — Application entry point.

Starts a FastAPI server with:
* ``/health`` – readiness / liveness probe
* ``/status`` – system overview (agent count, bus stats)
* On startup: initializes structured logging and the async message bus.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from medi_comply.core.config import get_settings
from medi_comply.core.logger import get_logger, setup_logging
from medi_comply.core.message_bus import AsyncMessageBus
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.api.routes.knowledge import router as knowledge_router
from medi_comply.api.routes.claims import router as claims_router

try:  # Optional dependency; app must run even if compliance module is absent
    from medi_comply.compliance.hipaa_guard import HIPAAAccessLogger
except Exception:  # pragma: no cover - graceful degradation
    HIPAAAccessLogger = None  # type: ignore

# ---------------------------------------------------------------------------
# Lifespan (startup & shutdown)
# ---------------------------------------------------------------------------

message_bus = AsyncMessageBus()
hipaa_access_logger: Optional[HIPAAAccessLogger] = HIPAAAccessLogger() if HIPAAAccessLogger else None


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
    # Initialize knowledge manager for API routes
    app.state.knowledge_manager = KnowledgeManager()
    try:
        app.state.knowledge_manager.initialize()
    except Exception as exc:  # pragma: no cover - safety net for degraded startup
        logger.warning("Knowledge manager initialization issue: %s", exc)
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
# Access logging middleware (non-blocking, best-effort)
# ---------------------------------------------------------------------------


def _method_to_action(method: str) -> str:
    method = (method or "").upper()
    if method == "GET":
        return "READ"
    if method == "POST":
        return "WRITE"
    if method in {"PUT", "PATCH"}:
        return "UPDATE"
    if method == "DELETE":
        return "DELETE"
    return "OTHER"


def _path_to_resource(path: str) -> tuple[str, bool]:
    path_lower = path.lower()
    if "/coding" in path_lower:
        return "coding", True
    if "/audit" in path_lower:
        return "audit", False
    if "/compliance" in path_lower:
        return "compliance", False
    if "/knowledge" in path_lower:
        return "knowledge", False
    return "generic", False


async def _log_access_async(request: Request, success: bool) -> None:
    if hipaa_access_logger is None:
        return
    try:
        user_id = request.headers.get("x-user-id") or request.headers.get("authorization", "anonymous")
        action = _method_to_action(request.method)
        resource_type, phi_accessed = _path_to_resource(request.url.path)
        resource_id = request.path_params.get("id") or request.path_params.get("resource_id") or "unknown"
        client_host = request.client.host if request.client else None

        # Run in executor to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: hipaa_access_logger.log_access(  # type: ignore[union-attr]
                user_id=user_id,
                user_role="unknown",
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                phi_accessed=phi_accessed,
                success=success,
                ip_address=client_host,
                phi_types=None,
                denial_reason=None,
            ),
        )
    except Exception:  # pragma: no cover - logging must not break API
        logger = get_logger(agent_name="access-logger")
        logger.warning("Access logging failed for path=%s", request.url.path)


if hipaa_access_logger is not None:
    @app.middleware("http")
    async def access_logging_middleware(request: Request, call_next):  # type: ignore[unused-variable]
        try:
            response = await call_next(request)
            asyncio.create_task(_log_access_async(request, success=response.status_code < 400))
            return response
        except Exception:
            asyncio.create_task(_log_access_async(request, success=False))
            raise

# Routers
app.include_router(knowledge_router)
app.include_router(claims_router)


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
