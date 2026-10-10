"""Deterministic reconciliation core (SRS 16–24).

All arithmetic, ID linking, date-window and fee logic lives here. The AI
layer consumes the output of this engine to produce explanations and
classifications — the engine itself never calls a model.

Design principle (SRS 48): deterministic code for calculations and state,
AI only for interpretation of ambiguous cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

AMOUNT_EPS = 0.01

# Statuses that count as reconciled in match-rate metrics.
RESOLVED_STATUSES = frozenset({"RECONCILED", "MATCHED_WITH_FEE"})

EXCEPTION_TYPES = frozenset({
    "FEE_ADJUSTMENT", "DATE_DELAY", "AMOUNT_MISMATCH", "PARTIAL_PAYMENT",
    "DUPLICATE", "MISSING_PAYMENT", "MISSING_SETTLEMENT",
    "MISSING_BANK_TRANSACTION", "REFUND", "UNKNOWN",
})


@dataclass
class ReconciliationConfig:
    high_value_threshold: float = 50_000.0
    settlement_delay_window_days: int = 3
    bank_delay_window_days: int = 3
    amount_eps: float = AMOUNT_EPS


@dataclass
class ReconciliationResult:
    order_id: str
    status: str = "UNRESOLVED"
    match_type: str | None = None
    exception_type: str | None = None
    payment_id: str | None = None
    settlement_id: str | None = None
    bank_transaction_id: str | None = None
    order_date: date | None = None
    payment_date: date | None = None
    settlement_date: date | None = None
    bank_date: date | None = None
    expected_amount: float = 0.0
    actual_amount: float = 0.0
    difference: float = 0.0
    currency: str = "INR"
    confidence: float = 0.0
    explanation: str = ""
    recommended_action: str = "REQUIRES_REVIEW"
    requires_human_review: bool = True
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = {
            "order_id": self.order_id,
            "status": self.status,
            "match_type": self.match_type,
            "exception_type": self.exception_type,
            "payment_id": self.payment_id,
            "settlement_id": self.settlement_id,
            "bank_transaction_id": self.bank_transaction_id,
            "order_date": self.order_date,
            "payment_date": self.payment_date,
            "settlement_date": self.settlement_date,
            "bank_date": self.bank_date,
            "expected_amount": self.expected_amount,
            "actual_amount": self.actual_amount,
            "difference": self.difference,
            "currency": self.currency,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "recommended_action": self.recommended_action,
            "requires_human_review": self.requires_human_review,
        }
        return out


def _r2(x: float) -> float:
    return round(float(x), 2)


def _fmt(x: float) -> str:
    return f"₹{x:,.2f}"


def run_reconciliation(
    orders: list[dict[str, Any]],
    payments: list[dict[str, Any]],
    settlements: list[dict[str, Any]],
    bank_txns: list[dict[str, Any]],
    config: ReconciliationConfig | None = None,
) -> list[ReconciliationResult]:
    """Reconcile one batch. Returns one result per order, sorted by order id."""
    cfg = config or ReconciliationConfig()
    eps = cfg.amount_eps

    # --- indexes ---------------------------------------------------------
    payments_by_order: dict[str, list[dict]] = {}
    for p in payments:
        payments_by_order.setdefault(p.get("order_id", ""), []).append(p)

    settlements_by_payment: dict[str, list[dict]] = {}
    for s in settlements:
        settlements_by_payment.setdefault(s.get("payment_id", ""), []).append(s)

    bank_by_reference: dict[str, list[dict]] = {}
    for b in bank_txns:
        if b.get("type", "CREDIT").upper() != "CREDIT":
            continue  # only credits are expected settlements
        bank_by_reference.setdefault(b.get("reference", ""), []).append(b)

    dup_payment_ids = _duplicate_ids(payments, "payment_id")
    dup_settlement_ids = _duplicate_ids(settlements, "settlement_id")

    results: list[ReconciliationResult] = []
    for order in sorted(orders, key=lambda o: str(o.get("order_id", ""))):
        oid = str(order.get("order_id", ""))
        expected = _r2(order.get("amount", 0.0))
        order_date = order.get("order_date")
        result = ReconciliationResult(
            order_id=oid,
            order_date=order_date,
            expected_amount=expected,
            currency=str(order.get("currency", "INR")),
        )
        results.append(_reconcile_order(
            result, order, payments_by_order.get(oid, []),
            settlements_by_payment, bank_by_reference,
            dup_payment_ids, dup_settlement_ids, cfg,
        ))
    return results


def _duplicate_ids(records: list[dict], key: str) -> set[str]:
    counts: dict[str, int] = {}
    for rec in records:
        k = str(rec.get(key, ""))
        counts[k] = counts.get(k, 0) + 1
    return {k for k, v in counts.items() if v > 1}


def _reconcile_order(
    result: ReconciliationResult,
    order: dict,
    payments: list[dict],
    settlements_by_payment: dict[str, list[dict]],
    bank_by_reference: dict[str, list[dict]],
    dup_payment_ids: set[str],
    dup_settlement_ids: set[str],
    cfg: ReconciliationConfig,
) -> ReconciliationResult:
    expected = result.expected_amount
    eps = cfg.amount_eps

    def settle(status: str, match_type: str, exception_type: str | None, confidence: float,
               explanation: str, action: str, actual: float | None = None) -> ReconciliationResult:
        result.status = status
        result.match_type = match_type
        result.exception_type = exception_type
        result.confidence = confidence
        result.explanation = explanation
        result.recommended_action = action
        if actual is not None:
            result.actual_amount = _r2(actual)
        result.difference = _r2(expected - result.actual_amount)
        return result

    # --- no payment ------------------------------------------------------
    if not payments:
        return settle("REVIEW_REQUIRED", "MISSING_PAYMENT", "MISSING_PAYMENT", 0.95,
                      "Order exists with no payment record.",
                      "REQUIRES_REVIEW", actual=0.0)

    primary = payments[0]
    result.payment_id = str(primary.get("payment_id", ""))
    result.payment_date = primary.get("payment_date")
    result.data["payments"] = len(payments)
    result.data["payment_amount"] = _r2(primary.get("amount", 0.0))
    result.data["payment_status"] = str(primary.get("status", "success")).lower()
    result.data["duplicate_payment_id"] = str(primary.get("payment_id")) in dup_payment_ids

    refunded = [p for p in payments if str(p.get("status", "")).lower() == "refunded"]
    non_refunded = [p for p in payments if str(p.get("status", "")).lower() != "refunded"]

    # --- refund ----------------------------------------------------------
    if refunded and not non_refunded:
        return settle("REVIEW_REQUIRED", "REFUND", "REFUND", 0.90,
                      "Payment is marked refunded; confirm the refund completed in bank records.",
                      "REQUIRES_REVIEW", actual=0.0)

    # --- duplicate payment -----------------------------------------------
    if str(primary.get("payment_id")) in dup_payment_ids:
        return settle("REVIEW_REQUIRED", "DUPLICATE", "DUPLICATE", 0.99,
                      "The payment id appears more than once in the source data; requires a deduplication decision.",
                      "REQUIRES_REVIEW", actual=expected)

    total_paid = _r2(sum(p.get("amount", 0.0) for p in non_refunded))

    # --- partial payment -------------------------------------------------
    if total_paid < expected - eps:
        return settle("REVIEW_REQUIRED", "PARTIAL_PAYMENT", "PARTIAL_PAYMENT", 0.90,
                      f"Payment covers only {total_paid / expected * 100:.0f}% of the order amount "
                      f"({_fmt(total_paid)} of {_fmt(expected)}); no second payment found.",
                      "REQUIRES_REVIEW", actual=total_paid)

    # --- settlement ------------------------------------------------------
    ss = settlements_by_payment.get(str(primary.get("payment_id")), [])
    if not ss:
        return settle("REVIEW_REQUIRED", "MISSING_SETTLEMENT", "MISSING_SETTLEMENT", 0.95,
                      "Payment recorded but no settlement found for it.",
                      "REQUIRES_REVIEW", actual=total_paid)

    s = ss[0]
    result.settlement_id = str(s.get("settlement_id", ""))
    
    # --- duplicate settlement --------------------------------------------
    if result.settlement_id in dup_settlement_ids:
        return settle("REVIEW_REQUIRED", "DUPLICATE", "DUPLICATE", 0.99,
                      "The settlement id appears more than once in the source data; requires a deduplication decision.",
                      "REQUIRES_REVIEW", actual=total_paid)

    result.settlement_date = s.get("settlement_date")
    gross = _r2(s.get("gross_amount", 0.0))
    fee = _r2(s.get("fee", 0.0))
    net = _r2(s.get("net_amount", 0.0))
    result.data["settlement_gross"] = gross
    result.data["settlement_fee"] = fee
    result.data["settlement_net"] = net

    if abs(_r2(gross - fee) - net) > eps:
        return settle("UNRESOLVED", "UNKNOWN", "UNKNOWN", 0.35,
                      f"Settlement record is internally inconsistent: gross {_fmt(gross)} "
                      f"- fee {_fmt(fee)} != net {_fmt(net)}.",
                      "ESCALATE", actual=net)

    # --- bank ------------------------------------------------------------
    banks = bank_by_reference.get(result.payment_id, [])
    bank = None
    if banks:
        bank = banks[0]
    if bank is None and result.payment_id:
        # Fallback: amount + date matching, but ONLY for bank rows that carry
        # no reference at all. An explicit reference points elsewhere, so the
        # credit belongs to that payment — using it here would be wrong.
        candidates = [
            cand for cand in _bank_by_amount(bank_by_reference, net)
            if not cand.get("reference")
            and _date_diff(cand.get("transaction_date"), result.settlement_date) <= cfg.bank_delay_window_days
        ]
        if len(candidates) == 1:
            bank = candidates[0]
        elif len(candidates) > 1:
            # Multiple plausible credits: fail safely instead of guessing (SRS 54).
            return settle("UNRESOLVED", "UNKNOWN", "UNKNOWN", 0.45,
                          "Multiple possible bank credits match the settlement net amount; "
                          "cannot determine the correct one without a reference.",
                          "ESCALATE", actual=net)

    # --- amount check vs payment/gross -----------------------------------
    gap = _r2(_r2(primary.get("amount", 0.0)) - gross)
    delay_days = _date_diff(s.get("settlement_date"), primary.get("payment_date"))

    if abs(gap) > eps:
        return settle("REVIEW_REQUIRED", "AMOUNT_MISMATCH", "AMOUNT_MISMATCH", 0.80,
                      f"Settlement gross {_fmt(gross)} differs from payment {_fmt(primary.get('amount', 0.0))} "
                      f"by {_fmt(abs(gap))} with no recorded fee or partial payment to explain it.",
                      "REQUIRES_REVIEW", actual=net)

    if delay_days > cfg.settlement_delay_window_days:
        # amounts agree; settlement arrived late
        result = settle("REVIEW_REQUIRED", "DATE_DELAY", "DATE_DELAY", 0.85,
                        f"Amounts agree, but settlement arrived {delay_days} day(s) after payment, "
                        f"beyond the allowed {cfg.settlement_delay_window_days}-day window.",
                        "REQUIRES_REVIEW", actual=net)
    elif fee > eps:
        result = settle("MATCHED_WITH_FEE", "MATCHED_WITH_FEE", "FEE_ADJUSTMENT", 0.96,
                        f"Settlement net {_fmt(net)} equals gross {_fmt(gross)} minus recorded fee "
                        f"{_fmt(fee)}; the difference vs the order matches the processing fee.",
                        "AUTO_RECONCILED", actual=net)
    else:
        result = settle("RECONCILED", "MATCHED", None, 0.99,
                        "Order, payment, settlement and bank records agree.",
                        "AUTO_RECONCILED", actual=net)

    # --- bank checks (only for settled rows) ------------------------------
    if result.exception_type in (None, "FEE_ADJUSTMENT", "DATE_DELAY") and result.status != "UNRESOLVED":
        if bank is None:
            return settle("REVIEW_REQUIRED", "MISSING_BANK_TRANSACTION", "MISSING_BANK_TRANSACTION", 0.95,
                          "Settlement exists but no matching bank credit was found.",
                          "REQUIRES_REVIEW", actual=net)
        result.bank_transaction_id = str(bank.get("bank_txn_id", ""))
        result.bank_date = bank.get("transaction_date")
        bank_amount = _r2(bank.get("amount", 0.0))
        result.data["bank_amount"] = bank_amount
        if abs(bank_amount - net) > eps:
            return settle("REVIEW_REQUIRED", "AMOUNT_MISMATCH", "AMOUNT_MISMATCH", 0.80,
                          f"Bank credit {_fmt(bank_amount)} differs from settlement net {_fmt(net)} "
                          f"by {_fmt(abs(bank_amount - net))} with no fee or partial payment to explain it.",
                          "REQUIRES_REVIEW", actual=bank_amount)
        bank_delay = _date_diff(bank.get("transaction_date"), s.get("settlement_date"))
        if bank_delay > cfg.bank_delay_window_days and result.exception_type is None:
            result.exception_type = "DATE_DELAY"
            result.status = "REVIEW_REQUIRED"
            result.confidence = 0.85
            result.explanation = (f"Bank credit arrived {bank_delay} day(s) after settlement, "
                                  f"beyond the allowed {cfg.bank_delay_window_days}-day window.")
            result.recommended_action = "REQUIRES_REVIEW"

    # --- high-value policy (SRS 51): mandatory human review ---------------
    if result.status in RESOLVED_STATUSES and expected > cfg.high_value_threshold:
        result.requires_human_review = True
        result.recommended_action = "HIGH_VALUE_REVIEW"
    elif result.status in RESOLVED_STATUSES:
        result.requires_human_review = False

    result.difference = _r2(expected - result.actual_amount)
    return result


def _date_diff(a: Any, b: Any) -> int:
    """Days between two dates; returns a large number if either is missing."""
    if not isinstance(a, date) or not isinstance(b, date):
        return 10_000
    return (a - b).days


def _bank_by_amount(bank_by_reference: dict[str, list[dict]], amount: float) -> list[dict]:
    out: list[dict] = []
    for banks in bank_by_reference.values():
        for b in banks:
            if abs(_r2(b.get("amount", 0.0)) - amount) <= AMOUNT_EPS:
                out.append(b)
    return out