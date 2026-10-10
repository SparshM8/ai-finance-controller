"""Tests for the AI exception analyzer (SRS 26–28, 46–50)."""

from __future__ import annotations

from datetime import date

from backend.app.ai.analyzer import (
    AIAnalysis,
    analyze,
    build_context,
    parse_llm_json,
    validate_analysis,
)
from backend.app.services.matching_engine import ReconciliationResult


def _result(exception_type="AMOUNT_MISMATCH", confidence=0.8, status="REVIEW_REQUIRED",
            expected=5000.0, actual=4700.0, data=None):
    return ReconciliationResult(
        order_id="ORD001",
        status=status,
        match_type="AMOUNT_MISMATCH",
        exception_type=exception_type,
        payment_id="PAY001",
        payment_date=date(2026, 9, 1),
        expected_amount=expected,
        actual_amount=actual,
        difference=round(expected - actual, 2),
        currency="INR",
        confidence=confidence,
        explanation="received less than expected",
        recommended_action="REQUIRES_REVIEW",
        data=data or {},
    )


def test_deterministic_analysis_of_amount_mismatch():
    result = _result(data={"payment_amount": 5000.0})
    analysis = analyze(result)
    assert isinstance(analysis, AIAnalysis)
    assert analysis.classification == "AMOUNT_MISMATCH"
    assert analysis.confidence == 0.8
    assert analysis.requires_human_review is True


def test_matched_transaction_analysis():
    result = ReconciliationResult(
        order_id="ORD001", status="RECONCILED", match_type="MATCHED",
        exception_type=None, expected_amount=5000.0, actual_amount=5000.0,
        difference=0.0, confidence=0.99, explanation="records agree",
        recommended_action="AUTO_RECONCILED", requires_human_review=False,
    )
    analysis = analyze(result)
    assert analysis.classification == "MATCHED"
    assert analysis.recommended_action == "APPROVE_RECONCILIATION"


def test_llm_not_called_when_deterministic_is_confident():
    result = _result(exception_type="DUPLICATE", confidence=0.99, data={"duplicate_payment_id": "true"})
    calls = []

    def fake_llm(system, user):
        calls.append(user)
        return {"classification": "AMOUNT_MISMATCH", "confidence": 0.9, "reasoning_summary": "wrong",
                "recommended_action": "REQUIRES_REVIEW", "requires_human_review": True}

    analysis = analyze(result, llm_client=fake_llm)
    assert calls == []          # deterministic answer is confident enough
    assert analysis.classification == "DUPLICATE"


def test_llm_escalated_for_ambiguous_case_and_validated():
    result = _result(exception_type="UNKNOWN", confidence=0.35, status="UNRESOLVED",
                     expected=5000.0, actual=4500.0,
                     data={"payment_amount": 5000.0, "settlement_gross": 5000.0,
                           "settlement_fee": 500.0, "settlement_net": 4500.0,
                           "bank_amount": 4500.0, "payment_status": "success",
                           "duplicate_payment_id": "false"})

    def fake_llm(system, user):
        return {"classification": "FEE_ADJUSTMENT", "confidence": 0.93,
                "reasoning_summary": "fee explains difference",
                "recommended_action": "APPROVE_RECONCILIATION", "requires_human_review": False}

    analysis = analyze(result, llm_client=fake_llm)
    # gross(5000) - fee(500) == net(4500): the claim passes rule validation.
    assert analysis.classification == "FEE_ADJUSTMENT"
    assert analysis.source == "llm"
    assert analysis.requires_human_review is True  # LLM output never auto-applies


def test_guardrail_rejects_unsupported_llm_claim():
    context = {
        "order_amount": 5000.0, "payment_amount": 5000.0,
        "settlement_gross": 5000.0, "settlement_fee": 0.0, "settlement_net": 5000.0,
        "bank_amount": 4700.0, "payment_status": "success",
        "duplicate_payment_id": "false", "observed_difference": 300.0,
    }
    # Model claims FEE_ADJUSTMENT, but gross - fee != net difference and fee = 0.
    bad = {"classification": "FEE_ADJUSTMENT", "confidence": 0.97,
           "reasoning_summary": "it's a fee", "recommended_action": "APPROVE_RECONCILIATION",
           "requires_human_review": False}
    validated = validate_analysis(bad, context)
    assert validated["classification"] == "UNKNOWN"
    assert validated["confidence"] < 0.70


def test_guardrail_accepts_supported_claim():
    context = {
        "order_amount": 2500.0, "payment_amount": 2500.0,
        "settlement_gross": 2500.0, "settlement_fee": 50.0, "settlement_net": 2450.0,
        "bank_amount": 2450.0, "payment_status": "success",
        "duplicate_payment_id": "false", "observed_difference": 50.0,
    }
    good = {"classification": "FEE_ADJUSTMENT", "confidence": 0.96,
            "reasoning_summary": "net = gross - fee", "recommended_action": "APPROVE_RECONCILIATION",
            "requires_human_review": False}
    validated = validate_analysis(good, context)
    assert validated["classification"] == "FEE_ADJUSTMENT"
    assert validated["confidence"] == 0.96


def test_context_is_structured_evidence():
    result = _result(data={"payment_amount": 5000.0, "settlement_gross": 5000.0,
                           "settlement_fee": 50.0, "settlement_net": 4950.0,
                           "bank_amount": 4950.0, "payment_status": "success",
                           "duplicate_payment_id": "false"})
    ctx = build_context(result)
    assert ctx["order_amount"] == 5000.0
    assert ctx["settlement_fee"] == 50.0
    assert "csv" not in str(ctx).lower()


def test_parse_llm_json_tolerates_code_fences():
    content = '```json\n{"classification": "REFUND", "confidence": 0.9}\n```'
    parsed = parse_llm_json(content)
    assert parsed["classification"] == "REFUND"