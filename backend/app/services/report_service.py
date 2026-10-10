"""Reconciliation metrics and evaluation against ground truth (SRS 36–39)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.services.matching_engine import ReconciliationResult, RESOLVED_STATUSES


@dataclass
class EvaluationMetrics:
    total: int
    matched: int
    match_rate: float
    actual_exceptions: int
    detected_exceptions: int
    correct_classifications: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    false_positive_financial_impact: float
    unresolved: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "matched": self.matched,
            "match_rate": round(self.match_rate, 4),
            "actual_exceptions": self.actual_exceptions,
            "detected_exceptions": self.detected_exceptions,
            "correct_classifications": self.correct_classifications,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "false_positive_financial_impact": round(self.false_positive_financial_impact, 2),
            "unresolved": self.unresolved,
        }


def summary_from_results(results: list[ReconciliationResult]) -> dict[str, Any]:
    """KPI summary straight from engine output (used before persistence)."""
    matched = sum(1 for r in results if r.status in RESOLVED_STATUSES)
    review = sum(1 for r in results if r.status == "REVIEW_REQUIRED")
    unresolved = sum(1 for r in results if r.status == "UNRESOLVED")
    total_value = sum(r.expected_amount for r in results)
    reconciled_value = sum(r.actual_amount for r in results if r.status in RESOLVED_STATUSES)
    # Accepted gateway fees are explained costs, not unreconciled exposure.
    at_risk_value = sum(r.expected_amount for r in results if r.status not in RESOLVED_STATUSES)

    breakdown: dict[str, int] = {}
    for r in results:
        if r.exception_type:
            breakdown[r.exception_type] = breakdown.get(r.exception_type, 0) + 1

    return {
        "total": len(results),
        "matched": matched,
        "review": review,
        "unresolved": unresolved,
        "match_rate": round(matched / len(results), 4) if results else 0.0,
        "total_value": round(total_value, 2),
        "reconciled_value": round(reconciled_value, 2),
        "at_risk_value": round(at_risk_value, 2),
        "exception_breakdown": breakdown,
    }


def evaluate(results: list[ReconciliationResult], ground_truth: list[dict[str, Any]]) -> EvaluationMetrics:
    """Compare engine output to ground_truth.csv (SRS 37–39).

    Classification is considered *correct* only when the engine's
    exception_type exactly matches the ground-truth exception_type.
    """
    gt_by_order = {str(gt["order_id"]): gt for gt in ground_truth}
    matched = 0
    actual_exceptions = 0
    detected = 0
    correct = 0
    false_positives = 0
    false_negatives = 0
    fp_impact = 0.0
    unresolved = 0

    for r in results:
        gt = gt_by_order.get(r.order_id)
        gt_type = str(gt.get("exception_type", "NONE")) if gt else "NONE"

        if r.status in RESOLVED_STATUSES:
            matched += 1
        if r.status == "UNRESOLVED":
            unresolved += 1

        is_actual_exception = gt_type != "NONE"
        is_detected = r.exception_type is not None
        if is_detected:
            detected += 1
        if is_actual_exception:
            actual_exceptions += 1
            if is_detected:
                if r.exception_type == gt_type:
                    correct += 1
                else:
                    false_positives += 1  # flagged, but with the wrong category
            else:
                false_negatives += 1
        elif is_detected:
            false_positives += 1
            fp_impact += r.expected_amount

    precision = correct / detected if detected else 0.0
    recall = correct / actual_exceptions if actual_exceptions else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return EvaluationMetrics(
        total=len(results),
        matched=matched,
        match_rate=matched / len(results) if results else 0.0,
        actual_exceptions=actual_exceptions,
        detected_exceptions=detected,
        correct_classifications=correct,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        f1=f1,
        false_positive_financial_impact=fp_impact,
        unresolved=unresolved,
    )