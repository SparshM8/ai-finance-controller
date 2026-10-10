"""Audit log endpoints (SRS 30, 56)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.schemas import AuditLogOut, PagedResponse

router = APIRouter(prefix="/api/audit-logs", tags=["audit"])


@router.get("", response_model=PagedResponse)
def list_audit_logs(
    entity_type: str | None = Query(default=None),
    action: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: models.User = Depends(require_roles("admin", "finance_controller", "analyst", "viewer")),
    db: Session = Depends(get_db),
) -> PagedResponse:
    q = db.query(models.AuditLog)
    if entity_type is not None:
        q = q.filter_by(entity_type=entity_type)
    if action is not None:
        q = q.filter_by(action=action)
    if actor is not None:
        q = q.filter_by(actor=actor)
    total = q.count()
    items = q.order_by(models.AuditLog.timestamp.desc(), models.AuditLog.id.desc()).offset(offset).limit(limit).all()
    return PagedResponse(
        items=[AuditLogOut.model_validate(a).model_dump() for a in items],
        total=total,
        limit=limit,
        offset=offset,
    )