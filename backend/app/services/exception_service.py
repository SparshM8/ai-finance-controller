"""Exception classification, severity and resolution policy (SRS 25–29, 51)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.app.services.matching_engine import ReconciliationResult, RESOLVED_STATUSES

SEVERITY_HIGH_TYPES = frozenset({"AMOUNT_MISMATCH", "UNKNOWN"})

CONFIDENCE_POLICY = {
    "AUTO_RECOMMEND": 0.90,   # >= 0.90: safe to auto-recommend (deterministic rules)
    "HUMAN_REVIEW_MIN": 0.70,  # 0.70–0.90: human review
    # < 0.70: escalate / unresolved
}


@dataclass
class ExceptionSpec:
    type: str
    severity: str
    description: str
    ai_explanation: str
    ai_confidence: float
    recommended_action: str
    status: str  # AUTO_RESOLVED | OPEN | UNRESOLVED


def build_exception_spec(result: ReconciliationResult) -> ExceptionSpec | None:
    """Derive the exception record for a reconciliation result (None if clean)."""
    if result.exception_type is None:
        return None

    severity = _severity(result)
    if result.status in RESOLVED_STATUSES:
        exception_status = "AUTO_RESOLVED"
    elif result.status == "UNRESOLVED" or result.confidence < CONFIDENCE_POLICY["HUMAN_REVIEW_MIN"]:
        exception_status = "UNRESOLVED"
    else:
        exception_status = "OPEN"

    description = (
        f"Expected {result.expected_amount:,.2f} {result.currency}, "
        f"received {result.actual_amount:,.2f} {result.currency}; "
        f"difference {abs(result.difference):,.2f} {result.currency}."
    )
    return ExceptionSpec(
        type=result.exception_type,
        severity=severity,
        description=description,
        ai_explanation=result.explanation,
        ai_confidence=result.confidence,
        recommended_action=result.recommended_action,
        status=exception_status,
    )


def _severity(result: ReconciliationResult) -> str:
    if result.exception_type in SEVERITY_HIGH_TYPES:
        return "HIGH"
    if abs(result.difference) > Decimal("0.05") * max(result.expected_amount, Decimal("1.00")) or result.expected_amount > Decimal("50000"):
        return "HIGH"
    return "MEDIUM"


def resolution_allowed(current_user_role: str, action: str) -> bool:
    """Role-based guard for review actions (SRS 7)."""
    if action in ("approve", "reject"):
        return current_user_role in {"admin", "finance_controller", "analyst"}
    if action == "override":
        return current_user_role in {"admin", "finance_controller"}
    return False