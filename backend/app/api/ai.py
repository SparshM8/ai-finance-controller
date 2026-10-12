"""AI Q&A endpoint (SRS 34)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.ai.analyzer import call_llm_text
from backend.app.config import settings
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
    
    source = "deterministic"
    answer = None
    if settings.ai_api_key:
        summary_ctx = _answer_summary(db)
        unrec_ctx = _answer_unreconciled(db)
        exc_ctx = _answer_exceptions(db, limit=5)
        context_prompt = f"""You are an enterprise AI Finance Controller assistant.
Current Ledger Context:
- Summary: {summary_ctx}
- Outstanding / At Risk: {unrec_ctx}
- Top Discrepancies: {exc_ctx}

User Question: {q}

Instructions:
Answer clearly, concisely, and professionally. Ground your answer in the financial data above when relevant. For greetings or questions about capabilities, respond helpfully and briefly."""
        llm_reply = call_llm_text(
            system="You are an enterprise AI Finance Controller for an automated reconciliation platform.",
            user=context_prompt,
            temperature=0.3,
        )
        if llm_reply:
            answer = llm_reply
            source = "llm"

    if not answer:
        answer = _answer(q, lc, db, limit)
        source = "deterministic"

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
    return {"question": q, "answer": answer, "source": source}



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
    total_rows = db.query(models.Reconciliation.currency, func.sum(models.Reconciliation.expected_amount)).group_by(models.Reconciliation.currency).all()
    at_risk_rows = (
        db.query(models.Reconciliation.currency, func.sum(models.Reconciliation.expected_amount))
        .filter(models.Reconciliation.status.notin_(("RECONCILED", "MATCHED_WITH_FEE")))
        .group_by(models.Reconciliation.currency)
        .all()
    )
    
    total_value = {c: float(v) for c, v in total_rows if c and v}
    at_risk = {c: float(v) for c, v in at_risk_rows if c and v}
    
    unresolved = db.query(models.Reconciliation).filter_by(status="UNRESOLVED").count()
    review = db.query(models.Reconciliation).filter_by(status="REVIEW_REQUIRED").count()
    
    if not at_risk:
        return f"Nothing is at risk across {unresolved} unresolved and {review} under review."
        
    at_risk_str = " + ".join([f"{v:,.0f} {c}" for c, v in at_risk.items()])
    return f"{at_risk_str} across {unresolved} unresolved and {review} under review remains to be reconciled."


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
    return f"{oid}: expected {rec.expected_amount:,.0f} {rec.currency}, received {rec.actual_amount:,.0f} {rec.currency} (difference {rec.difference:,.0f} {rec.currency}). Status: {rec.status}. Exception: {rec.exception_type or 'none'}. Confidence: {rec.confidence:.0%}. Explanation: {rec.explanation or 'no explanation recorded'}."


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
