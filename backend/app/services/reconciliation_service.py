"""Batch orchestration: persist uploaded records, run the engine, store results."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.app import models
from backend.app.services import report_service
from backend.app.services.exception_service import build_exception_spec
from backend.app.services.matching_engine import ReconciliationConfig, run_reconciliation


def persist_records(db: Session, batch_id: int, records_by_kind: dict[str, list[dict[str, Any]]]) -> int:
    """Insert normalized records for a batch. Returns total record count."""
    total = 0
    _HELPER_KEYS = ('id_key_for_dup',)  # ingestion helper, not an ORM column
    for kind, records in records_by_kind.items():
        total += len(records)
        for r in records:
            cleaned = {k: v for k, v in r.items() if k not in _HELPER_KEYS}
            if kind == "orders":
                db.add(models.Order(batch_id=batch_id, **cleaned))
            elif kind == "payments":
                db.add(models.Payment(batch_id=batch_id, **cleaned))
            elif kind == "settlements":
                db.add(models.Settlement(batch_id=batch_id, **cleaned))
            else:
                db.add(models.BankTransaction(batch_id=batch_id, **cleaned))
    db.commit()
    return total


def run_batch_reconciliation(
    db: Session,
    batch: models.Batch,
    actor: str,
    config: ReconciliationConfig | None = None,
) -> dict[str, Any]:
    """Execute reconciliation for a batch and persist results + exceptions + audit."""
    batch.status = "PROCESSING"
    db.commit()

    if config is None:
        from decimal import Decimal
        rows = db.query(models.SystemSetting).all()
        config_data = {}
        for r in rows:
            config_data[r.key] = r.value
        config = ReconciliationConfig(
            high_value_threshold=Decimal(config_data.get("high_value_threshold", "50000.00")),
            bank_delay_window_days=int(config_data.get("bank_delay_window_days", "7")),
            amount_eps=Decimal(config_data.get("amount_eps", "1.00"))
        )

    orders = [o.__dict__ for o in db.query(models.Order).filter_by(batch_id=batch.id).all()]
    payments = [p.__dict__ for p in db.query(models.Payment).filter_by(batch_id=batch.id).all()]
    settlements = [s.__dict__ for s in db.query(models.Settlement).filter_by(batch_id=batch.id).all()]
    banks = [b.__dict__ for b in db.query(models.BankTransaction).filter_by(batch_id=batch.id).all()]

    results = run_reconciliation(orders, payments, settlements, banks, config)

    for result in results:
        row = models.Reconciliation(batch_id=batch.id, **result.as_dict())
        db.add(row)
        db.flush()

        spec = build_exception_spec(result)
        if spec is not None:
            db.add(models.ExceptionRecord(
                reconciliation_id=row.id,
                type=spec.type,
                severity=spec.severity,
                description=spec.description,
                ai_explanation=spec.ai_explanation,
                ai_confidence=spec.ai_confidence,
                recommended_action=spec.recommended_action,
                status=spec.status,
            ))

        db.add(models.AuditLog(
            entity_type="reconciliation",
            entity_id=result.order_id,
            action="AI_CLASSIFICATION",
            actor="AI",
            old_value="UNMATCHED",
            new_value=result.status,
            reason=result.explanation[:500],
        ))

    summary = report_service.summary_from_results(results)

    batch.status = "COMPLETED"
    batch.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(models.AuditLog(
        entity_type="batch",
        entity_id=str(batch.id),
        action="RECONCILIATION_COMPLETED",
        actor=actor,
        old_value="PROCESSING",
        new_value=f"COMPLETED ({summary['matched']}/{summary['total']} matched)",
        reason=f"match_rate={summary['match_rate']:.2%}",
    ))
    db.commit()

    return {
        "batch_id": batch.id,
        "status": "COMPLETED",
        "total": summary["total"],
        "matched": summary["matched"],
        "review": summary["review"],
        "unresolved": summary["unresolved"],
        "match_rate": summary["match_rate"],
    }