"""AI Q&A endpoint (SRS 34)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.database import get_db
from backend.app.deps import require_roles

router = APIRouter(prefix="/api/ai", tags=["ai"])

VIEW_ROLES = ("admin", "finance_controller", "analyst", "viewer")

@router.post("/ask")
def ask(
    question: str,
    limit: int = Query(default=50, le=200, ge=1),
    user: models.User = Depends(require_roles(*VIEW_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    """Answer a natural-language finance question from the current data."""
    q = question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    lc = q.lower()
    answer = _answer(q, lc, db, limit)
    db.add(models.AuditLog(
        entity_type="ai_qa",
        entity_id="",
        action="AI_ASK",
        actor=user.email,
        old_value="",
        new_value=answer[:500],
        reason=q[:500],
    ))
    db.commit()
    return {"question": q, "answer": answer, "source": "deterministic"}


def _answer(q: str, lc: str, db: Session, limit: int) -> str:
    if re.search(r"\b(ORD\d+)\b", q, re.IGNORECASE):
        return _answer_specific(db, q, limit)
    if "unreconciled" in lc or "at risk" in lc:
        return _answer_unreconciled(db)
    if "exception" in lc or "review" in lc:
        return _answer_exceptions(db, limit)
    if "matched" in lc or "match rate" in lc:
        return _answer_match_rate(db)
    if "which" in lc or "why" in lc or re.search(r"\b(ORD\d+)\b", lc, re.IGNORECASE):
        return _answer_specific(db, q, limit)
    return _answer_summary(db)



def _answer_unreconciled(db: Session) -> str:
    total_value = db.query(func.coalesce(func.sum(models.Reconciliation.expected_amount), 0.0)).scalar() or 0.0
    at_risk = (
        db.query(func.coalesce(func.sum(models.Reconciliation.expected_amount), 0.0))
        .filter(models.Reconciliation.status.notin_(("RECONCILED", "MATCHED_WITH_FEE")))
        .scalar()
    ) or 0.0
    unresolved = db.query(models.Reconciliation).filter_by(status="UNRESOLVED").count()
    review = db.query(models.Reconciliation).filter_by(status="REVIEW_REQUIRED").count()
    pct = (at_risk / total_value * 100) if total_value else 0
    return f"{at_risk:,.0f} across {unresolved} unresolved and {review} under review remains to be reconciled. That is {pct:.1f}% of the total {total_value:,.0f} processed."


def _answer_exceptions(db: Session, limit: int) -> str:
    rows = (
        db.query(models.Reconciliation.exception_type, func.count())
        .filter(models.Reconciliation.exception_type.isnot(None))
        .group_by(models.Reconciliation.exception_type)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return "No exceptions recorded yet."
    parts = [f"{t.replace("_", " ").title()}: {c}" for t, c in rows]
    return "The main reconciliation issues are: " + ", ".join(parts) + "."


def _answer_match_rate(db: Session) -> str:
    total = db.query(models.Reconciliation).count()
    if not total:
        return "No transactions processed yet."
    matched = db.query(models.Reconciliation).filter(
        models.Reconciliation.status.in_(("RECONCILED", "MATCHED_WITH_FEE"))
    ).count()
    rate = matched / total * 100
    return f"{matched} of {total} transactions reconciled ({rate:.1f}% match rate)."


def _answer_specific(db: Session, q: str, limit: int) -> str:
    m = re.search(r"\b(ORD\d+)\b", q, re.IGNORECASE)
    if not m:
        return _answer_summary(db)
    oid = m.group(1).upper()
    records = db.query(models.Reconciliation).filter(models.Reconciliation.order_id.ilike(oid)).limit(2).all()
    if not records:
        return f"No transaction found for {oid}."
    if len(records) > 1:
        return f"{oid} appears in multiple batches. Open the transaction explorer to select the intended batch; a unique answer cannot be determined."
    rec = records[0]
    return f"{oid}: expected {rec.expected_amount:,.0f}, received {rec.actual_amount:,.0f} (difference {rec.difference:,.0f}). Status: {rec.status}. Exception: {rec.exception_type or "none"}. Confidence: {rec.confidence:.0%}. Explanation: {rec.explanation or "no explanation recorded"}."


def _answer_summary(db: Session) -> str:
    total = db.query(models.Reconciliation).count()
    matched = db.query(models.Reconciliation).filter(
        models.Reconciliation.status.in_(("RECONCILED", "MATCHED_WITH_FEE"))
    ).count()
    review = db.query(models.Reconciliation).filter_by(status="REVIEW_REQUIRED").count()
    unresolved = db.query(models.Reconciliation).filter_by(status="UNRESOLVED").count()
    parts = [f"{total} transactions processed"]
    if matched:
        parts.append(f"{matched} matched")
    if review:
        parts.append(f"{review} under review")
    if unresolved:
        parts.append(f"{unresolved} unresolved")
    return ". ".join(parts) + "."
