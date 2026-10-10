"""End-to-end reconciliation tests: engine vs ground truth (SRS 37–39, 44)."""

from __future__ import annotations

from backend.app.services.matching_engine import run_reconciliation
from backend.app.services.report_service import evaluate, summary_from_results
from scripts.generate_dataset import generate_dataset


def test_reconciliation_matches_ground_truth():
    ds = generate_dataset(500, seed=42)
    results = run_reconciliation(ds.orders, ds.payments, ds.settlements, ds.bank_txns)

    assert len(results) == 500
    metrics = evaluate(results, ds.ground_truth)

    # The deterministic engine should reproduce every injected anomaly exactly.
    assert metrics.recall == 1.0
    assert metrics.precision == 1.0
    assert metrics.false_positives == 0
    assert metrics.false_negatives == 0
    # ~70% clean + ~10% fee-adjusted => ~80% match rate on this mix.
    assert 0.78 <= metrics.match_rate <= 0.82

    summary = summary_from_results(results)
    assert summary["total"] == 500
    assert summary["matched"] + summary["review"] + summary["unresolved"] == 500
    assert summary["exception_breakdown"]["FEE_ADJUSTMENT"] == 50
    assert summary["exception_breakdown"]["AMOUNT_MISMATCH"] == 25
    assert summary["exception_breakdown"]["DUPLICATE"] == 15


def test_every_anomaly_type_is_detected():
    # Large batch so even the rarest sub-variants of the 2% "other" bucket appear.
    ds = generate_dataset(2000, seed=7)
    results = run_reconciliation(ds.orders, ds.payments, ds.settlements, ds.bank_txns)
    detected_types = {r.exception_type for r in results if r.exception_type}
    expected_types = {
        "FEE_ADJUSTMENT", "DATE_DELAY", "AMOUNT_MISMATCH", "PARTIAL_PAYMENT",
        "DUPLICATE", "MISSING_PAYMENT", "MISSING_SETTLEMENT",
        "MISSING_BANK_TRANSACTION", "REFUND", "UNKNOWN",
    }
    assert expected_types <= detected_types


def test_high_value_policy_flags_matched_transactions():
    ds = generate_dataset(500, seed=42)
    results = run_reconciliation(ds.orders, ds.payments, ds.settlements, ds.bank_txns)
    high_value_matched = [
        r for r in results
        if r.expected_amount > 50_000 and r.status in ("RECONCILED", "MATCHED_WITH_FEE")
    ]
    assert high_value_matched, "expected some high-value matched transactions"
    assert all(r.requires_human_review for r in high_value_matched)
    assert all(r.recommended_action == "HIGH_VALUE_REVIEW" for r in high_value_matched)