"""Exception endpoints: list, analyze, approve/reject/override (SRS 29, 41)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.ai.analyzer import AIAnalysis, analyze, openai_compatible_client
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.deps import require_review_access, require_roles
from backend.app.schemas import AnalysisOut, ExceptionOut, PagedResponse

router = APIRouter(prefix="/api/exceptions", tags=["exceptions"])

VIEW_ROLES = ("admin", "finance_controller", "analyst", "viewer")


class ReviewRequest(BaseModel):
    comment: str = ""
    reason: str = ""


def _get_exception(db: Session, exception_id: int) -> models.ExceptionRecord:
    exc = db.query(models.ExceptionRecord).filter_by(id=exception_id).first()
    if exc is None:
        raise HTTPException(status_code=404, detail="Exception not found")
    return exc


def _llm_client():
    if settings.ai_api_key:
        return openai_compatible_client(
            settings.ai_api_key, settings.ai_base_url, settings.ai_model, settings.ai_timeout_seconds
        )
    return None


@router.get("", response_model=PagedResponse)
def list_exceptions(
    batch_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    exception_type: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: models.User = Depends(require_roles(*VIEW_ROLES)),
    db: Session = Depends(get_db),
) -> PagedResponse:
    q = db.query(models.ExceptionRecord)
    if batch_id is not None:
        q = q.join(models.Reconciliation).filter(models.Reconciliation.batch_id == batch_id)
    if status is not None:
        q = q.filter_by(status=status)
    if exception_type is not None:
        q = q.filter_by(type=exception_type)
    if severity is not None:
        q = q.filter_by(severity=severity)
    total = q.count()
    items = q.order_by(models.ExceptionRecord.id).offset(offset).limit(limit).all()
    return PagedResponse(
        items=[ExceptionOut.model_validate(e).model_dump() for e in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/export")
def export_exceptions(
    batch_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    exception_type: str | None = Query(default=None),
    user: models.User = Depends(require_roles(*VIEW_ROLES)),
    db: Session = Depends(get_db),
):
    import io
    import csv
    from fastapi import Response

    q = db.query(models.ExceptionRecord)
    if batch_id is not None:
        q = q.join(models.Reconciliation).filter(models.Reconciliation.batch_id == batch_id)
    if status is not None:
        q = q.filter_by(status=status)
    if exception_type is not None:
        q = q.filter_by(type=exception_type)
        
    items = q.order_by(models.ExceptionRecord.id).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Batch ID", "Type", "Status", "Severity", "Description", "Resolved By", "Resolved At"])
    for e in items:
        writer.writerow([
            e.id, 
            e.reconciliation.batch_id if e.reconciliation else "", 
            e.type, 
            e.status, 
            e.severity, 
            e.description, 
            e.resolved_by or "", 
            e.resolved_at.isoformat() if e.resolved_at else ""
        ])
        
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=exceptions.csv"}
    )


@router.get("/{exception_id}", response_model=ExceptionOut)
def get_exception(
    exception_id: int,
    user: models.User = Depends(require_roles(*VIEW_ROLES)),
    db: Session = Depends(get_db),
) -> ExceptionOut:
    return ExceptionOut.model_validate(_get_exception(db, exception_id))


@router.post("/{exception_id}/analyze", response_model=AnalysisOut)
def analyze_exception(
    exception_id: int,
    user: models.User = Depends(require_roles(*VIEW_ROLES)),
    db: Session = Depends(get_db),
) -> AnalysisOut:
    """Run the AI analyzer (deterministic, plus LLM for ambiguous cases).

    The AI only produces a recommendation — it never modifies records
    (SRS 27, 50). Classification is re-validated against deterministic
    rules before it is returned.
    """
    exc = _get_exception(db, exception_id)
    rec = db.query(models.Reconciliation).filter_by(id=exc.reconciliation_id).first()
    if rec is None:
        raise HTTPException(status_code=404, detail="Reconciliation not found")

    from backend.app.services.matching_engine import ReconciliationResult

    result = ReconciliationResult(
        order_id=rec.order_id,
        status=rec.status,
        match_type=rec.match_type,
        exception_type=rec.exception_type,
        payment_id=rec.payment_id,
        settlement_id=rec.settlement_id,
        bank_transaction_id=rec.bank_transaction_id,
        order_date=rec.order_date,
        payment_date=rec.payment_date,
        settlement_date=rec.settlement_date,
        bank_date=rec.bank_date,
        expected_amount=rec.expected_amount,
        actual_amount=rec.actual_amount,
        difference=rec.difference,
        currency=rec.currency,
        confidence=rec.confidence,
        explanation=rec.explanation,
        recommended_action=rec.recommended_action,
        requires_human_review=rec.requires_human_review,
        data={
            "payment_amount": None,
            "payment_status": "success",
            "settlement_gross": None,
            "settlement_fee": None,
            "settlement_net": None,
            "bank_amount": None,
        },
    )
    # Rehydrate structured evidence from the persisted raw records.
    payment = db.query(models.Payment).filter_by(batch_id=rec.batch_id, payment_id=rec.payment_id).first() if rec.payment_id else None
    if payment:
        result.data["payment_amount"] = payment.amount
        result.data["payment_status"] = payment.status
    settlement = db.query(models.Settlement).filter_by(batch_id=rec.batch_id, settlement_id=rec.settlement_id).first() if rec.settlement_id else None
    if settlement:
        result.data["settlement_gross"] = settlement.gross_amount
        result.data["settlement_fee"] = settlement.fee
        result.data["settlement_net"] = settlement.net_amount
    bank = db.query(models.BankTransaction).filter_by(batch_id=rec.batch_id, bank_txn_id=rec.bank_transaction_id).first() if rec.bank_transaction_id else None
    if bank:
        result.data["bank_amount"] = bank.amount

    analysis: AIAnalysis = analyze(result, llm_client=_llm_client())

    db.add(models.AuditLog(
        entity_type="exception",
        entity_id=str(exc.id),
        action="AI_ANALYSIS",
        actor="AI",
        old_value="",
        new_value=f"{analysis.classification} @ {analysis.confidence:.2f} ({analysis.source})",
        reason=analysis.reasoning_summary[:500],
    ))
    db.commit()
    return AnalysisOut(**analysis.as_dict())


class DraftEmailOut(BaseModel):
    subject: str
    body: str

@router.post("/{exception_id}/draft_email", response_model=DraftEmailOut)
def draft_email(
    exception_id: int,
    user: models.User = Depends(require_roles(*VIEW_ROLES)),
    db: Session = Depends(get_db),
) -> DraftEmailOut:
    """Generate a professional customer-facing email to resolve an exception."""
    exc = _get_exception(db, exception_id)
    rec = db.query(models.Reconciliation).filter_by(id=exc.reconciliation_id).first()
    
    # We create a simple prompt for the LLM to draft an email
    prompt = f"""
    You are a professional Finance Controller. Draft an email to the customer regarding a payment discrepancy.
    Order ID: {rec.order_id}
    Order Amount: {rec.expected_amount} {rec.currency}
    Actual Paid: {rec.actual_amount} {rec.currency}
    Discrepancy: {rec.difference} {rec.currency}
    Issue Type: {exc.type}
    Issue Description: {exc.description}
    
    Please provide ONLY the email content in the following format:
    SUBJECT: [Subject here]
    BODY:
    [Body here]
    """
    
    client = _llm_client()
    if not client:
        return DraftEmailOut(
            subject=f"Action Required: Payment discrepancy for Order {rec.order_id}",
            body=f"Dear Customer,\n\nWe noticed a discrepancy of {rec.difference} {rec.currency} for your recent order {rec.order_id}. Please review this and let us know.\n\nThanks,\nFinance Team"
        )
        
    try:
        resp = client.chat.completions.create(
            model=settings.ai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        content = resp.choices[0].message.content.strip()
        lines = content.split('\n')
        subject = "Payment Discrepancy"
        body = content
        for idx, line in enumerate(lines):
            if line.upper().startswith("SUBJECT:"):
                subject = line[8:].strip()
                body = "\n".join(lines[idx+1:]).replace("BODY:", "").strip()
                break
                
        return DraftEmailOut(subject=subject, body=body)
    except Exception as e:
        return DraftEmailOut(subject="Error", body=str(e))


@router.post("/{exception_id}/approve", response_model=ExceptionOut)
def approve_exception(
    exception_id: int,
    body: ReviewRequest,
    user: models.User = Depends(require_review_access("approve")),
    db: Session = Depends(get_db),
) -> ExceptionOut:
    exc = _get_exception(db, exception_id)
    _resolve(db, exc, "RESOLVED", user, body.comment)
    return ExceptionOut.model_validate(exc)


@router.post("/{exception_id}/reject", response_model=ExceptionOut)
def reject_exception(
    exception_id: int,
    body: ReviewRequest,
    user: models.User = Depends(require_review_access("reject")),
    db: Session = Depends(get_db),
) -> ExceptionOut:
    exc = _get_exception(db, exception_id)
    _resolve(db, exc, "REJECTED", user, body.comment)
    return ExceptionOut.model_validate(exc)


@router.post("/{exception_id}/override", response_model=ExceptionOut)
def override_exception(
    exception_id: int,
    body: ReviewRequest,
    user: models.User = Depends(require_review_access("override")),
    db: Session = Depends(get_db),
) -> ExceptionOut:
    exc = _get_exception(db, exception_id)
    _resolve(db, exc, "OVERRIDDEN", user, body.comment or body.reason)
    return ExceptionOut.model_validate(exc)


def _resolve(db: Session, exc: models.ExceptionRecord, new_status: str, user: models.User, comment: str) -> None:
    if exc.status in ("RESOLVED", "REJECTED", "OVERRIDDEN"):
        raise HTTPException(status_code=409, detail=f"Exception already {exc.status}")
    old_status = exc.status
    
    # Maker-Checker Workflow
    if user.role == "analyst" and exc.severity == "HIGH" and new_status in ("RESOLVED", "OVERRIDDEN"):
        new_status = "PENDING_APPROVAL"
        
    exc.status = new_status
    exc.resolved_by = user.email
    exc.resolved_at = datetime.now(timezone.utc)
    exc.reviewer_comment = comment
    db.add(models.AuditLog(
        entity_type="exception",
        entity_id=str(exc.id),
        action=f"MANUAL_{new_status}",
        actor=user.email,
        old_value=old_status,
        new_value=new_status,
        reason=comment,
    ))
    db.commit()