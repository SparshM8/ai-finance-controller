"""One-click demo data: load the generated CSVs and reconcile them (SRS 58–61).

This is a convenience for demos and the live preview page — production data
flows through the normal upload endpoint instead.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.services import ingestion, report_service
from backend.app.services.reconciliation_service import persist_records, run_batch_reconciliation

router = APIRouter(prefix="/api/demo", tags=["demo"])

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _summary_for_batch(db: Session, batch_id: int) -> dict:
    from backend.app.services.matching_engine import ReconciliationResult

    rows = db.query(models.Reconciliation).filter_by(batch_id=batch_id).all()
    results = [
        ReconciliationResult(
            order_id=r.order_id,
            status=r.status,
            match_type=r.match_type,
            exception_type=r.exception_type,
            expected_amount=r.expected_amount,
            actual_amount=r.actual_amount,
            difference=r.difference,
            confidence=r.confidence,
            recommended_action=r.recommended_action,
            requires_human_review=r.requires_human_review,
        )
        for r in rows
    ]
    summary = report_service.summary_from_results(results)
    return {
        "batch_id": batch_id,
        "status": "COMPLETED",
        "total": summary["total"],
        "matched": summary["matched"],
        "review": summary["review"],
        "unresolved": summary["unresolved"],
        "match_rate": summary["match_rate"],
    }


@router.post("/load")
def load_demo_data(
    user: models.User = Depends(require_roles("admin", "finance_controller", "analyst")),
    db: Session = Depends(get_db),
) -> dict:
    """Idempotently load the generated dataset and reconcile it."""
    latest = (
        db.query(models.Batch)
        .filter(models.Batch.batch_name.like("Demo%"))
        .order_by(models.Batch.id.desc())
        .first()
    )
    if latest is not None and latest.status == "COMPLETED":
        return _summary_for_batch(db, latest.id)

    missing = [f"{kind}.csv" for kind in ingestion.SOURCE_KINDS if not (DATA_DIR / f"{kind}.csv").exists()]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Demo data missing ({', '.join(missing)}). Run: uv run python scripts/generate_dataset.py --out data",
        )

    files = {kind: (DATA_DIR / f"{kind}.csv").read_bytes() for kind in ingestion.SOURCE_KINDS}
    records_by_kind, errors = ingestion.ingest_batch_files(files)
    if errors:
        raise HTTPException(status_code=422, detail={"message": "Upload failed", "errors": errors[:50]})

    batch = models.Batch(batch_name="Demo data (synthetic)", uploaded_by=user.id)
    db.add(batch)
    db.commit()
    db.refresh(batch)

    total = persist_records(db, batch.id, records_by_kind)
    batch.total_records = total
    db.commit()

    return run_batch_reconciliation(db, batch, actor=user.email)