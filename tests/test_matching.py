"""Unit tests for the deterministic matching rules (SRS 16–24)."""

from __future__ import annotations

from datetime import date

from backend.app.services.matching_engine import ReconciliationConfig, run_reconciliation


def _order(order_id="ORD001", amount=5000.0, order_date=date(2026, 9, 1)):
    return {"order_id": order_id, "customer_id": "C001", "order_date": order_date,
            "amount": amount, "currency": "INR", "status": "completed"}


def _payment(payment_id="PAY001", order_id="ORD001", amount=5000.0, payment_date=date(2026, 9, 1), status="success"):
    return {"payment_id": payment_id, "order_id": order_id, "payment_date": payment_date,
            "amount": amount, "status": status}


def _settlement(settlement_id="SET001", payment_id="PAY001", settlement_date=date(2026, 9, 2),
                gross=5000.0, fee=0.0, net=5000.0):
    return {"settlement_id": settlement_id, "payment_id": payment_id, "settlement_date": settlement_date,
            "gross_amount": gross, "fee": fee, "net_amount": net, "status": "success"}


def _bank(bank_id="BANK001", reference="PAY001", txn_date=date(2026, 9, 2), amount=5000.0):
    return {"bank_txn_id": bank_id, "transaction_date": txn_date, "reference": reference,
            "amount": amount, "type": "CREDIT"}


def _run(orders, payments, settlements, banks, config=None):
    results = run_reconciliation(orders, payments, settlements, banks, config)
    return results[0]


def test_exact_match():
    r = _run([_order()], [_payment()], [_settlement()], [_bank()])
    assert r.status == "RECONCILED"
    assert r.match_type == "MATCHED"
    assert r.exception_type is None
    assert r.difference == 0.0
    assert r.requires_human_review is False


def test_fee_adjusted_match():
    r = _run(
        [_order(amount=2500.0)],
        [_payment(amount=2500.0)],
        [_settlement(gross=2500.0, fee=50.0, net=2450.0)],
        [_bank(amount=2450.0)],
    )
    assert r.status == "MATCHED_WITH_FEE"
    assert r.exception_type == "FEE_ADJUSTMENT"
    assert r.difference == 50.0
    assert r.requires_human_review is False


def test_date_delay_beyond_window():
    cfg = ReconciliationConfig(settlement_delay_window_days=3)
    r = _run(
        [_order()],
        [_payment(payment_date=date(2026, 9, 1))],
        [_settlement(settlement_date=date(2026, 9, 7))],
        [_bank()],
        config=cfg,
    )
    assert r.status == "REVIEW_REQUIRED"
    assert r.exception_type == "DATE_DELAY"


def test_amount_mismatch_settlement_short():
    r = _run(
        [_order(amount=5000.0)],
        [_payment(amount=5000.0)],
        [_settlement(gross=4700.0, net=4700.0)],
        [_bank(amount=4700.0)],
    )
    assert r.status == "REVIEW_REQUIRED"
    assert r.exception_type == "AMOUNT_MISMATCH"
    assert r.difference == 300.0


def test_partial_payment():
    r = _run([_order(amount=5000.0)], [_payment(amount=3000.0)], [], [])
    assert r.exception_type == "PARTIAL_PAYMENT"
    assert r.difference == 2000.0


def test_duplicate_payment():
    p = _payment()
    p2 = dict(p)
    r = _run([_order()], [p, p2], [_settlement()], [_bank()])
    assert r.exception_type == "DUPLICATE"


def test_missing_settlement():
    r = _run([_order()], [_payment()], [], [])
    assert r.exception_type == "MISSING_SETTLEMENT"


def test_missing_bank_transaction():
    r = _run([_order()], [_payment()], [_settlement()], [])
    assert r.exception_type == "MISSING_BANK_TRANSACTION"


def test_missing_payment():
    r = _run([_order()], [], [], [])
    assert r.exception_type == "MISSING_PAYMENT"


def test_refund():
    r = _run([_order()], [_payment(status="refunded")], [], [])
    assert r.exception_type == "REFUND"


def test_inconsistent_settlement_unknown():
    r = _run(
        [_order(amount=5000.0)],
        [_payment(amount=5000.0)],
        [_settlement(gross=5000.0, fee=0.0, net=4500.0)],
        [_bank(amount=4500.0)],
    )
    assert r.exception_type == "UNKNOWN"
    assert r.status == "UNRESOLVED"
    assert r.confidence < 0.70


def test_bank_reference_wins_over_amount_fallback():
    # Another order's bank credit has the same amount; it must NOT be used.
    results = run_reconciliation(
        [_order(order_id="ORD001")], [_payment()], [_settlement()],
        [_bank(amount=5000.0), _bank(bank_id="BANK002", reference="PAY002", amount=999.0)],
    )
    assert results[0].status == "RECONCILED"
    assert results[0].bank_transaction_id == "BANK001"


def test_high_value_requires_human_review():
    cfg = ReconciliationConfig(high_value_threshold=50000.0)
    r = _run(
        [_order(amount=100000.0)],
        [_payment(amount=100000.0)],
        [_settlement(gross=100000.0, net=100000.0)],
        [_bank(amount=100000.0)],
        config=cfg,
    )
    assert r.status == "RECONCILED"          # counts as matched...
    assert r.requires_human_review is True   # ...but must be human-approved (SRS 51)
    assert r.recommended_action == "HIGH_VALUE_REVIEW"