"""Knowledge management API routes for MEDI-COMPLY."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.knowledge.knowledge_updater import KnowledgeVersion, UpdateSource

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


class UpdateRequest(BaseModel):
    source: Optional[str] = None


class ApproveRequest(BaseModel):
    approved_by: str


class RollbackRequest(BaseModel):
    reason: str


def _get_knowledge_manager(request: Request) -> KnowledgeManager:
    km = getattr(request.app.state, "knowledge_manager", None)
    if km is None:
        km = KnowledgeManager()
        setattr(request.app.state, "knowledge_manager", km)
    return km


def _serialize_version(version: KnowledgeVersion | str) -> dict[str, Any]:
    if isinstance(version, str):
        return {
            "version_id": version,
            "version_number": None,
            "created_at": None,
            "promoted_at": None,
            "source": None,
            "description": None,
            "is_active": False,
            "is_shadow": False,
        }
    return {
        "version_id": version.version_id,
        "version_number": version.version_number,
        "created_at": version.created_at,
        "promoted_at": version.promoted_at,
        "source": version.source,
        "description": version.description,
        "is_active": version.is_active,
        "is_shadow": version.is_shadow,
    }


@router.get("/version")
async def get_version(request: Request) -> dict[str, Any]:
    km = _get_knowledge_manager(request)
    version = km.get_knowledge_version(as_record=True)
    return _serialize_version(version)


@router.post("/update")
async def trigger_update(request: Request, payload: UpdateRequest) -> dict[str, Any]:
    km = _get_knowledge_manager(request)

    source: Optional[UpdateSource] = None
    if payload.source:
        try:
            source = UpdateSource(payload.source)
        except ValueError as exc:  # invalid source
            raise HTTPException(status_code=400, detail=f"Invalid source: {payload.source}") from exc

    updates = await km.check_for_knowledge_updates()
    selected_update: Optional[dict[str, Any]] = None

    if source:
        for upd in updates:
            upd_source = upd.get("source")
            if upd_source == source or upd_source == source.value:
                selected_update = upd
                break
    else:
        selected_update = updates[0] if updates else None

    if not selected_update:
        return {"update_available": False, "version_id": None, "status": "NO_UPDATES"}

    effective_source = source
    if not effective_source:
        upd_src = selected_update.get("source")
        if isinstance(upd_src, UpdateSource):
            effective_source = upd_src
        elif isinstance(upd_src, str):
            effective_source = UpdateSource(upd_src)
        else:
            effective_source = UpdateSource.MANUAL

    version = await km.apply_knowledge_update(selected_update, effective_source)
    return {
        "update_available": True,
        "version_id": version.version_id,
        "status": version.metadata.get("status") if hasattr(version, "metadata") else "UNKNOWN",
    }


@router.get("/history")
async def get_history(request: Request) -> dict[str, Any]:
    km = _get_knowledge_manager(request)
    versions = km.get_knowledge_version_history()
    return {"versions": [_serialize_version(v) for v in versions]}


@router.post("/approve/{version_id}")
async def approve_update(request: Request, version_id: str, payload: ApproveRequest) -> dict[str, Any]:
    km = _get_knowledge_manager(request)
    success = km.approve_knowledge_update(version_id, payload.approved_by)
    if not success:
        raise HTTPException(status_code=404, detail="Version not found or not pending review")
    return {"success": True, "message": "Update approved and promoted"}


@router.post("/rollback/{version_id}")
async def rollback_version(request: Request, version_id: str, payload: RollbackRequest) -> dict[str, Any]:
    km = _get_knowledge_manager(request)
    success = km.rollback_knowledge(version_id, payload.reason)
    if not success:
        raise HTTPException(status_code=404, detail="Rollback failed: version not found or already active")
    return {"success": True, "message": "Rollback completed"}


@router.get("/feeds")
async def feeds(request: Request) -> dict[str, Any]:
    km = _get_knowledge_manager(request)
    return {"feeds": km.get_feed_status()}
