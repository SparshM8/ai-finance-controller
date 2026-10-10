"""Regression coverage for the restored dashboard and finance reporting."""

from decimal import Decimal

from backend.app import models
from backend.app.database import SessionLocal
from backend.app.services.matching_engine import ReconciliationResult
from backend.app.services.report_service import evaluate, summary_from_results
from scripts.generate_dataset import generate_dataset
from tests.conftest import auth_headers
from tests.test_api import _upload_files


def test_dashboard_assets_are_complete(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "<!doctype html>" in page.text.lower()
    for asset in ("/app.js", "/styles.css"):
        assert asset in page.text
        response = client.get(asset)
        assert response.status_code == 200
        assert len(response.content) > 100
    for element in ("login-form", "upload-form", "exception-table", "ask-form", "audit-table"):
        assert f'id="{element}"' in page.text


def test_precision_penalizes_false_alarms():
    results = [
        ReconciliationResult(order_id="A", exception_type="MISSING_PAYMENT"),
        ReconciliationResult(order_id="B", exception_type="MISSING_PAYMENT"),
    ]
    metrics = evaluate(results, [
        {"order_id": "A", "exception_type": "MISSING_PAYMENT"},
        {"order_id": "B", "exception_type": "NONE"},
    ])
    assert metrics.detected_exceptions == 2
    assert metrics.false_positives == 1
    assert metrics.precision == 0.5
    assert metrics.recall == 1.0


def test_accepted_fees_are_not_at_risk():
    summary = summary_from_results([
        ReconciliationResult(order_id="A", status="MATCHED_WITH_FEE", expected_amount=100, actual_amount=97),
        ReconciliationResult(order_id="B", status="REVIEW_REQUIRED", expected_amount=50, actual_amount=20),
    ])
    assert summary["at_risk_value"] == 50
    assert summary["reconciled_value"] == 97


def test_analysis_uses_own_batch_evidence(client, admin_token, monkeypatch):
    headers = auth_headers(admin_token)
    dataset = generate_dataset(60, seed=77)
    batch_ids = []
    for name in ("Evidence first", "Evidence second"):
        response = client.post("/api/batches/upload", headers=headers, data={"batch_name": name}, files=_upload_files(dataset))
        assert response.status_code == 201
        batch_ids.append(response.json()["batch_id"])
        assert client.post(f"/api/batches/{batch_ids[-1]}/reconcile", headers=headers).status_code == 200
    with SessionLocal() as db:
        rec = db.query(models.Reconciliation).filter_by(batch_id=batch_ids[-1], exception_type="FEE_ADJUSTMENT").first()
        assert rec is not None
        exc = db.query(models.ExceptionRecord).filter_by(reconciliation_id=rec.id).first()
        assert exc is not None
        exc_id = exc.id
        payment = db.query(models.Payment).filter_by(batch_id=rec.batch_id, payment_id=rec.payment_id).first()
        settlement = db.query(models.Settlement).filter_by(batch_id=rec.batch_id, settlement_id=rec.settlement_id).first()
        bank = db.query(models.BankTransaction).filter_by(batch_id=rec.batch_id, bank_txn_id=rec.bank_transaction_id).first()
        payment.amount = 12345.67
        settlement.fee = 42.25
        bank.amount = 9876.54
        db.commit()
    from backend.app.api import exceptions
    original = exceptions.analyze
    captured = {}

    def capture(result, **kwargs):
        captured.update(result.data)
        return original(result, **kwargs)

    monkeypatch.setattr(exceptions, "analyze", capture)
    response = client.post(f"/api/exceptions/{exc_id}/analyze", headers=headers)
    assert response.status_code == 200
    assert captured["payment_amount"] == Decimal("12345.67")
    assert captured["settlement_fee"] == Decimal("42.25")
    assert captured["bank_amount"] == Decimal("9876.54")


def test_qa_specific_question_and_audit(client, admin_token):
    headers = auth_headers(admin_token)
    response = client.post("/api/ai/ask", params={"question": "Why is ORD999999999 under review?"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["answer"] == "No transaction found for ORD999999999."
    assert response.json()["source"] == "deterministic"
    assert client.post("/api/ai/ask", params={"question": " "}, headers=headers).status_code == 400
    assert client.post("/api/ai/ask", params={"question": "summary"}).status_code == 401
    logs = client.get("/api/audit-logs?limit=200", headers=headers).json()["items"]
    assert any(log["action"] == "AI_ASK" for log in logs)


def test_dashboard_exposure_matches_unmatched_orders(client, admin_token):
    headers = auth_headers(admin_token)
    with SessionLocal() as db:
        expected = sum(r.expected_amount for r in db.query(models.Reconciliation).all()
                       if r.status not in ("RECONCILED", "MATCHED_WITH_FEE"))
    summary = client.get("/api/dashboard/summary", headers=headers).json()
    assert sum(summary["at_risk_value"].values()) == round(expected, 2)
    answer = client.post("/api/ai/ask", params={"question": "How much is unreconciled?"}, headers=headers).json()["answer"]
    assert answer.startswith(f"{expected:,.0f} across")
