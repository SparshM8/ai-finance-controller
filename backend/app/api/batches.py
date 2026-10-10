"""Batch endpoints: upload, list, get, reconcile (SRS 41)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.schemas import BatchOut, ReconcileResponse
from backend.app.services import ingestion
from backend.app.services.reconciliation_service import persist_records, run_batch_reconciliation

router = APIRouter(prefix="/api/batches", tags=["batches"])

UPLOAD_ROLES = ("admin", "finance_controller", "analyst")


@router.post("/upload", status_code=201)
def upload_batch(
    batch_name: str = Form("Batch "),
    orders: UploadFile = File(...),
    payments: UploadFile = File(...),
    settlements: UploadFile = File(...),
    bank_transactions: UploadFile = File(...),
    user: models.User = Depends(require_roles(*UPLOAD_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    files = {
        "orders": orders.file.read(),
        "payments": payments.file.read(),
        "settlements": settlements.file.read(),
        "bank_transactions": bank_transactions.file.read(),
    }
    for name, content in files.items():
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"File {name} exceeds 5MB limit")
            
    records_by_kind, errors = ingestion.ingest_batch_files(files)
    if errors:
        raise HTTPException(status_code=422, detail={"message": "Upload failed", "errors": errors[:50]})

    batch = models.Batch(batch_name=batch_name.strip() or f"Batch", uploaded_by=user.id)
    db.add(batch)
    db.flush()  # get batch.id without committing

    try:
        total = persist_records(db, batch.id, records_by_kind)
        batch.total_records = total
        db.add(models.AuditLog(
            entity_type="batch",
            entity_id=str(batch.id),
            action="UPLOAD",
            actor=user.email,
            old_value="",
            new_value=f"{total} records across {len(records_by_kind)} sources",
            reason=f"batch_name={batch.batch_name}",
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to persist records: {str(e)}")

    return {"batch_id": batch.id, "total_records": total, "status": batch.status}


@router.get("", response_model=list[BatchOut])
def list_batches(
    user: models.User = Depends(require_roles("admin", "finance_controller", "analyst", "viewer")),
    db: Session = Depends(get_db),
) -> list[BatchOut]:
    return [BatchOut.model_validate(b) for b in db.query(models.Batch).order_by(models.Batch.id.desc()).limit(50).all()]


@router.get("/{batch_id}", response_model=BatchOut)
def get_batch(
    batch_id: int,
    user: models.User = Depends(require_roles("admin", "finance_controller", "analyst", "viewer")),
    db: Session = Depends(get_db),
) -> BatchOut:
    batch = db.query(models.Batch).filter_by(id=batch_id).first()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return BatchOut.model_validate(batch)


@router.post("/{batch_id}/reconcile", response_model=ReconcileResponse)
def reconcile_batch(
    batch_id: int,
    user: models.User = Depends(require_roles(*UPLOAD_ROLES)),
    db: Session = Depends(get_db),
) -> ReconcileResponse:
    batch = db.query(models.Batch).filter_by(id=batch_id).first()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch.status == "PROCESSING":
        raise HTTPException(status_code=409, detail="Batch is already being processed")
    return ReconcileResponse(**run_batch_reconciliation(db, batch, actor=user.email))