"""Dashboard summary endpoint (SRS 31–32)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.schemas import DashboardSummary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def dashboard_summary(
    user: models.User = Depends(require_roles("admin", "finance_controller", "analyst", "viewer")),
    db: Session = Depends(get_db),
) -> DashboardSummary:
    total = db.query(models.Reconciliation).count()
    matched = db.query(models.Reconciliation).filter(
        models.Reconciliation.status.in_(("RECONCILED", "MATCHED_WITH_FEE"))
    ).count()
    review = db.query(models.Reconciliation).filter_by(status="REVIEW_REQUIRED").count()
    unresolved = db.query(models.Reconciliation).filter_by(status="UNRESOLVED").count()

    total_value_rows = db.query(
        models.Reconciliation.currency, 
        func.sum(models.Reconciliation.expected_amount)
    ).group_by(models.Reconciliation.currency).all()
    total_value = {c: round(float(v), 2) for c, v in total_value_rows if c and v}

    reconciled_value_rows = db.query(
        models.Reconciliation.currency, 
        func.sum(models.Reconciliation.actual_amount)
    ).filter(models.Reconciliation.status.in_(("RECONCILED", "MATCHED_WITH_FEE"))).group_by(models.Reconciliation.currency).all()
    reconciled_value = {c: round(float(v), 2) for c, v in reconciled_value_rows if c and v}

    at_risk_value_rows = db.query(
        models.Reconciliation.currency, 
        func.sum(models.Reconciliation.expected_amount)
    ).filter(models.Reconciliation.status.notin_(("RECONCILED", "MATCHED_WITH_FEE"))).group_by(models.Reconciliation.currency).all()
    at_risk_value = {c: round(float(v), 2) for c, v in at_risk_value_rows if c and v}

    breakdown_rows = (
        db.query(models.Reconciliation.exception_type, func.count())
        .filter(models.Reconciliation.exception_type.isnot(None))
        .group_by(models.Reconciliation.exception_type)
        .all()
    )
    breakdown = {str(t): int(c) for t, c in breakdown_rows if t}

    high_value = db.query(models.Reconciliation).filter(
        models.Reconciliation.requires_human_review.is_(True),
        models.Reconciliation.status.in_(("RECONCILED", "MATCHED_WITH_FEE")),
    ).count()

    avg_conf = db.query(func.avg(models.Reconciliation.confidence)).filter(
        models.Reconciliation.confidence > 0
    ).scalar()

    latest = db.query(models.Batch).order_by(models.Batch.id.desc()).first()

    return DashboardSummary(
        total_transactions=total,
        matched=matched,
        review=review,
        unresolved=unresolved,
        match_rate=round(matched / total, 4) if total else 0.0,
        total_value=total_value,
        reconciled_value=reconciled_value,
        at_risk_value=at_risk_value,
        exception_breakdown=breakdown,
        high_value_review_count=high_value,
        avg_ai_confidence=round(float(avg_conf), 4) if avg_conf is not None else None,
        latest_batch_id=latest.id if latest else None,
        latest_batch_status=latest.status if latest else None,
    )