"""Reconciliation endpoints: list and detail (SRS 33, 41)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.schemas import PagedResponse, ReconciliationOut

router = APIRouter(prefix="/api/reconciliations", tags=["reconciliation"])

VIEW_ROLES = ("admin", "finance_controller", "analyst", "viewer")


@router.get("", response_model=PagedResponse)
def list_reconciliations(
    batch_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    exception_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: models.User = Depends(require_roles(*VIEW_ROLES)),
    db: Session = Depends(get_db),
) -> PagedResponse:
    q = db.query(models.Reconciliation)
    if batch_id is not None:
        q = q.filter_by(batch_id=batch_id)
    if status is not None:
        q = q.filter_by(status=status)
    if exception_type is not None:
        q = q.filter_by(exception_type=exception_type)
    if search:
        term = f"%{search}%"
        q = q.filter(or_(
            models.Reconciliation.order_id.ilike(term),
            models.Reconciliation.payment_id.ilike(term),
            models.Reconciliation.settlement_id.ilike(term),
        ))
    total = q.count()
    items = q.order_by(models.Reconciliation.id).offset(offset).limit(limit).all()
    return PagedResponse(
        items=[ReconciliationOut.model_validate(r).model_dump() for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{rec_id}", response_model=ReconciliationOut)
def get_reconciliation(
    rec_id: int,
    user: models.User = Depends(require_roles(*VIEW_ROLES)),
    db: Session = Depends(get_db),
) -> ReconciliationOut:
    rec = db.query(models.Reconciliation).filter_by(id=rec_id).first()
    if rec is None:
        raise HTTPException(status_code=404, detail="Reconciliation not found")
    return ReconciliationOut.model_validate(rec)