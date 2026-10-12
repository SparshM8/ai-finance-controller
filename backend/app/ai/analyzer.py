"""AI exception analyzer (SRS 26–28, 46–50).

Architecture:
    AI recommendation -> rule validation -> policy -> human/system approval

The deterministic analyzer always runs. The LLM is only consulted for
ambiguous cases (UNKNOWN or confidence < 0.70) when an API key is
configured, and its output is validated against deterministic facts before
being accepted. The AI never writes to the database.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import httpx

from backend.app.ai import prompts
from backend.app.config import settings
from backend.app.services.matching_engine import ReconciliationResult

DEFAULT_ANALYSIS = {
    "classification": "UNKNOWN",
    "confidence": 0.35,
    "reasoning_summary": "Insufficient evidence to classify with confidence.",
    "recommended_action": "ESCALATE",
    "requires_human_review": True,
}


@dataclass
class AIAnalysis:
    classification: str
    confidence: float
    reasoning_summary: str
    recommended_action: str
    requires_human_review: bool
    source: str = "deterministic"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_context(result: ReconciliationResult) -> dict[str, Any]:
    """Structured evidence for the AI (SRS 26). No raw CSVs — just facts."""
    data = result.data
    return {
        "order_id": result.order_id,
        "order_amount": result.expected_amount,
        "payment_amount": data.get("payment_amount"),
        "settlement_gross": data.get("settlement_gross"),
        "settlement_fee": data.get("settlement_fee"),
        "settlement_net": data.get("settlement_net"),
        "bank_amount": data.get("bank_amount"),
        "payment_date": result.payment_date.isoformat() if result.payment_date else None,
        "settlement_date": result.settlement_date.isoformat() if result.settlement_date else None,
        "bank_date": result.bank_date.isoformat() if result.bank_date else None,
        "payment_status": data.get("payment_status", "success"),
        "duplicate_payment_id": str(data.get("duplicate_payment_id", False)).lower(),
        "observed_difference": result.difference,
    }


def analyze(result: ReconciliationResult, llm_client: Callable[[str, str], dict] | None = None) -> AIAnalysis:
    """Analyze a reconciliation result. Deterministic first; LLM for ambiguity."""
    if result.exception_type is None:
        return AIAnalysis(
            classification="MATCHED",
            confidence=result.confidence,
            reasoning_summary=result.explanation,
            recommended_action="APPROVE_RECONCILIATION",
            requires_human_review=result.requires_human_review,
        )

    analysis = AIAnalysis(
        classification=result.exception_type,
        confidence=result.confidence,
        reasoning_summary=result.explanation,
        recommended_action=_map_action(result.recommended_action),
        requires_human_review=result.status != "MATCHED_WITH_FEE",
    )

    # LLM escalation for ambiguous cases only.
    if llm_client is not None and (analysis.classification == "UNKNOWN" or analysis.confidence < 0.70):
        try:
            context = build_context(result)
            llm_out = llm_client(prompts.SYSTEM_PROMPT, prompts.build_user_message(context))
            validated = validate_analysis(llm_out, context)
            if validated["classification"] != "UNKNOWN":
                return AIAnalysis(
                    classification=validated["classification"],
                    confidence=max(0.0, min(1.0, float(validated["confidence"]))),
                    reasoning_summary=validated["reasoning_summary"],
                    recommended_action=validated["recommended_action"],
                    requires_human_review=True,  # LLM output always keeps a human in the loop
                    source="llm",
                )
        except Exception:
            # AI unavailable -> deterministic fallback, retain for manual review (SRS 53).
            pass

    return analysis


def _map_action(action: str) -> str:
    if action in ("AUTO_RECONCILED", "HIGH_VALUE_REVIEW"):
        return "APPROVE_RECONCILIATION"
    if action == "ESCALATE":
        return "ESCALATE"
    return "REQUIRES_REVIEW"


def validate_analysis(analysis: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Guardrail: reject LLM classifications contradicted by deterministic facts.

    The model's category is accepted only when the underlying arithmetic
    supports it (SRS 27, 50). On failure we fall back to UNKNOWN.
    """
    classification = str(analysis.get("classification", "UNKNOWN")).upper()
    eps = 0.01
    gross = _f(context.get("settlement_gross"))
    fee = _f(context.get("settlement_fee"))
    net = _f(context.get("settlement_net"))
    order = _f(context.get("order_amount"))
    payment = _f(context.get("payment_amount"))
    bank = _f(context.get("bank_amount"))
    difference = _f(context.get("observed_difference"))

    def _fail() -> dict[str, Any]:
        out = dict(DEFAULT_ANALYSIS)
        out["reasoning_summary"] = (
            f"LLM classification '{classification}' failed deterministic rule "
            "validation and was rejected; retained for manual review."
        )
        return out

    valid = False
    if classification == "FEE_ADJUSTMENT":
        # The recorded fee must be non-zero AND fully explain the difference.
        valid = (
            gross is not None and fee is not None and net is not None
            and fee > eps
            and abs((gross - fee) - net) <= eps
            and difference is not None and abs(difference - fee) <= eps
        )
    elif classification == "PARTIAL_PAYMENT":
        valid = payment is not None and order is not None and payment < order - eps
    elif classification == "AMOUNT_MISMATCH":
        valid = difference is not None and abs(difference) > eps
    elif classification == "DUPLICATE":
        valid = context.get("duplicate_payment_id") == "true"
    elif classification == "MISSING_SETTLEMENT":
        valid = context.get("settlement_net") is None
    elif classification == "MISSING_BANK_TRANSACTION":
        valid = context.get("bank_amount") is None
    elif classification == "MISSING_PAYMENT":
        valid = context.get("payment_amount") is None
    elif classification == "REFUND":
        valid = context.get("payment_status") == "refunded"
    elif classification == "DATE_DELAY":
        valid = context.get("settlement_date") is not None and context.get("payment_date") is not None
    elif classification == "UNKNOWN":
        valid = True

    if not valid:
        return _fail()

    confidence = _clamp_float(analysis.get("confidence", 0.5))
    action = str(analysis.get("recommended_action", "REQUIRES_REVIEW")).upper()
    if action not in ("APPROVE_RECONCILIATION", "REQUIRES_REVIEW", "ESCALATE"):
        action = "REQUIRES_REVIEW"
    return {
        "classification": classification,
        "confidence": confidence,
        "reasoning_summary": str(analysis.get("reasoning_summary", ""))[:500],
        "recommended_action": action,
        "requires_human_review": True,
    }


def openai_compatible_client(api_key: str, base_url: str, model: str, timeout: int) -> Callable[[str, str], dict]:
    """Build an OpenAI-compatible chat-completions client (httpx)."""

    def _call(system: str, user: str) -> dict:
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
        }
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return parse_llm_json(content)

    return _call


def call_llm_text(system: str, user: str, temperature: float = 0.2) -> str | None:
    """Send a prompt to the configured LLM and return the generated text response."""
    if not settings.ai_api_key:
        return None
    try:
        url = f"{settings.ai_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {settings.ai_api_key}"}
        payload = {
            "model": settings.ai_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        resp = httpx.post(url, json=payload, headers=headers, timeout=settings.ai_timeout_seconds)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def parse_llm_json(content: str) -> dict[str, Any]:
    """Parse model output, tolerating markdown code fences."""
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    return json.loads(text)


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_float(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5