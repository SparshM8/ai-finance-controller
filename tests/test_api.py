"""End-to-end API tests: auth, upload, reconcile, review, audit (SRS 41)."""

from __future__ import annotations

import io
from pathlib import Path

from scripts.generate_dataset import generate_dataset

from tests.conftest import auth_headers


def _upload_files(ds) -> dict:
    def _csv_bytes(rows, headers):
        buf = io.StringIO()
        buf.write(",".join(headers) + "\n")
        for row in rows:
            buf.write(",".join(str(v) for v in row) + "\n")
        return buf.getvalue().encode("utf-8")

    files = {
        "orders": ("orders.csv", _csv_bytes(
            [(r["order_id"], r["customer_id"], r["order_date"].isoformat(), f"{r['amount']:.2f}", r["currency"], r["status"]) for r in ds.orders],
            ["order_id", "customer_id", "order_date", "amount", "currency", "status"]), "text/csv"),
        "payments": ("payments.csv", _csv_bytes(
            [(r["payment_id"], r["order_id"], r["payment_date"].isoformat(), f"{r['amount']:.2f}", r["status"]) for r in ds.payments],
            ["payment_id", "order_id", "payment_date", "amount", "payment_status"]), "text/csv"),
        "settlements": ("settlements.csv", _csv_bytes(
            [(r["settlement_id"], r["payment_id"], r["settlement_date"].isoformat(),
              f"{r['gross_amount']:.2f}", f"{r['fee']:.2f}", f"{r['net_amount']:.2f}") for r in ds.settlements],
            ["settlement_id", "payment_id", "settlement_date", "gross_amount", "fee", "net_amount"]), "text/csv"),
        "bank_transactions": ("bank_transactions.csv", _csv_bytes(
            [(r["bank_txn_id"], r["transaction_date"].isoformat(), r["reference"], f"{r['amount']:.2f}", r["type"]) for r in ds.bank_txns],
            ["bank_txn_id", "transaction_date", "reference", "amount", "type"]), "text/csv"),
    }
    return files


def test_full_flow(client, admin_token):
    ds = generate_dataset(60, seed=3)
    headers = auth_headers(admin_token)

    # --- upload -----------------------------------------------------------
    resp = client.post(
        "/api/batches/upload",
        headers=headers,
        data={"batch_name": "E2E batch"},
        files=_upload_files(ds),
    )
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["batch_id"]
    assert resp.json()["total_records"] >= 60

    # --- reconcile --------------------------------------------------------
    resp = client.post(f"/api/batches/{batch_id}/reconcile", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 60
    assert body["matched"] + body["review"] + body["unresolved"] == 60
    assert 0.75 <= body["match_rate"] <= 0.85

    # --- dashboard --------------------------------------------------------
    resp = client.get("/api/dashboard/summary", headers=headers)
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["total_transactions"] == 60
    assert summary["matched"] == body["matched"]
    assert "exception_breakdown" in summary
    assert "at_risk_value" in summary

    # --- reconciliation list + search -------------------------------------
    resp = client.get(f"/api/reconciliations?batch_id={batch_id}&limit=500", headers=headers)
    items = resp.json()["items"]
    assert len(items) == 60
    first = items[0]
    resp = client.get(f"/api/reconciliations/{first['id']}", headers=headers)
    assert resp.status_code == 200

    # --- exceptions -------------------------------------------------------
    resp = client.get(f"/api/exceptions?batch_id={batch_id}&limit=500", headers=headers)
    exceptions = resp.json()["items"]
    assert exceptions, "expected exceptions in a batch with anomalies"
    exc = exceptions[0]

    # --- AI analysis ------------------------------------------------------
    resp = client.post(f"/api/exceptions/{exc['id']}/analyze", headers=headers)
    assert resp.status_code == 200, resp.text
    analysis = resp.json()
    assert analysis["classification"] in {
        "FEE_ADJUSTMENT", "DATE_DELAY", "AMOUNT_MISMATCH", "PARTIAL_PAYMENT",
        "DUPLICATE", "MISSING_PAYMENT", "MISSING_SETTLEMENT",
        "MISSING_BANK_TRANSACTION", "REFUND", "UNKNOWN",
    }
    assert 0.0 <= analysis["confidence"] <= 1.0
    assert analysis["source"] == "deterministic"

    # --- human review -----------------------------------------------------
    resp = client.post(f"/api/exceptions/{exc['id']}/approve", headers=headers, json={"comment": "verified"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "RESOLVED"
    resp = client.post(f"/api/exceptions/{exc['id']}/approve", headers=headers, json={"comment": "again"})
    assert resp.status_code == 409  # already resolved

    # --- audit trail ------------------------------------------------------
    resp = client.get("/api/audit-logs?limit=500", headers=headers)
    logs = resp.json()["items"]
    actions = {log["action"] for log in logs}
    assert {"UPLOAD", "RECONCILIATION_COMPLETED", "AI_CLASSIFICATION", "MANUAL_RESOLVED"} <= actions
    assert any(log["actor"] == "AI" for log in logs)


def test_upload_rejects_invalid_csv(client, admin_token):
    headers = auth_headers(admin_token)
    bad = io.BytesIO(b"order_id,amount\nORD1,2500\n")
    resp = client.post(
        "/api/batches/upload",
        headers=headers,
        data={"batch_name": "bad"},
        files={
            "orders": ("orders.csv", bad, "text/csv"),
            "payments": ("payments.csv", io.BytesIO(b"payment_id,order_id\nP1,O1\n"), "text/csv"),
            "settlements": ("settlements.csv", io.BytesIO(b"settlement_id,payment_id\nS1,P1\n"), "text/csv"),
            "bank_transactions": ("bank_transactions.csv", io.BytesIO(b"bank_txn_id,reference\nB1,R1\n"), "text/csv"),
        },
    )
    assert resp.status_code == 422
    errors = resp.json()["detail"]["errors"]
    assert any("orders: missing required column" in e for e in errors)


def test_upload_rejects_invalid_amount(client, admin_token):
    headers = auth_headers(admin_token)
    resp = client.post(
        "/api/batches/upload",
        headers=headers,
        data={"batch_name": "bad-amount"},
        files={
            "orders": ("orders.csv", io.BytesIO(b"order_id,customer_id,order_date,amount,currency,status\nORD1,C1,2026-09-01,ABC,INR,completed\n"), "text/csv"),
            "payments": ("payments.csv", io.BytesIO(b"payment_id,order_id,payment_date,amount,payment_status\nPAY1,ORD1,2026-09-01,2500,success\n"), "text/csv"),
            "settlements": ("settlements.csv", io.BytesIO(b"settlement_id,payment_id,settlement_date,gross_amount,fee,net_amount\nSET1,PAY1,2026-09-02,2500,0,2500\n"), "text/csv"),
            "bank_transactions": ("bank_transactions.csv", io.BytesIO(b"bank_txn_id,transaction_date,reference,amount,type\nBANK1,2026-09-02,PAY1,2500,CREDIT\n"), "text/csv"),
        },
    )
    assert resp.status_code == 422
    assert any("invalid amount at row 2" in e for e in resp.json()["detail"]["errors"])


def test_role_based_access(client):
    # A viewer cannot upload.
    resp = client.post("/api/auth/register", json={
        "name": "Viewer", "email": "viewer@example.com", "password": "password1", "role": "viewer",
    })
    assert resp.status_code == 201
    viewer_token = resp.json()["access_token"]

    resp = client.post(
        "/api/batches/upload",
        headers=auth_headers(viewer_token),
        data={"batch_name": "x"},
        files=_upload_files(generate_dataset(10, seed=9)),
    )
    assert resp.status_code == 403

    # But a viewer can read the dashboard.
    resp = client.get("/api/dashboard/summary", headers=auth_headers(viewer_token))
    assert resp.status_code == 200

    # A plain analyst cannot override.
    resp = client.post("/api/auth/register", json={
        "name": "Analyst", "email": "analyst@example.com", "password": "password1", "role": "analyst",
    })
    analyst_token = resp.json()["access_token"]

    # seed an exception to try to override
    admin = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin123"}).json()["access_token"]
    ds = generate_dataset(10, seed=11)
    batch_id = client.post("/api/batches/upload", headers=auth_headers(admin),
                           data={"batch_name": "roles"}, files=_upload_files(ds)).json()["batch_id"]
    client.post(f"/api/batches/{batch_id}/reconcile", headers=auth_headers(admin))
    exc = client.get("/api/exceptions?batch_id=999", headers=auth_headers(admin)).json()  # empty probe
    excs = client.get(f"/api/exceptions?batch_id={batch_id}", headers=auth_headers(admin)).json()["items"]
    assert excs
    resp = client.post(f"/api/exceptions/{excs[0]['id']}/override", headers=auth_headers(analyst_token), json={"reason": "nope"})
    assert resp.status_code == 403


def test_login_failure(client):
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "wrong"})
    assert resp.status_code == 401


def test_demo_load_and_static_page(client, admin_token):
    import csv as _csv
    import pytest as _pytest

    data_dir = Path(__file__).resolve().parents[1] / "data"
    orders_path = data_dir / "orders.csv"
    if not orders_path.exists():
        _pytest.skip("demo data not generated")
    with orders_path.open(encoding="utf-8-sig") as fh:
        order_count = sum(1 for _ in _csv.DictReader(fh))

    headers = auth_headers(admin_token)
    resp = client.post("/api/demo/load", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == order_count
    assert body["matched"] + body["review"] + body["unresolved"] == order_count

    # Idempotent: a second call reuses the same demo batch.
    resp2 = client.post("/api/demo/load", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["batch_id"] == body["batch_id"]

    # The single-file viewer is served at /.
    page = client.get("/")
    assert page.status_code == 200
    assert "AI Finance Controller" in page.text