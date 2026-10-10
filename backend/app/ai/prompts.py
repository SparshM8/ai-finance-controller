"""LLM prompt design (SRS 46–47)."""

SYSTEM_PROMPT = """You are an AI finance reconciliation assistant.

Your task is to analyze a financial reconciliation exception.

You must:
1. Identify the most likely exception category.
2. Explain the reasoning in natural language.
3. Provide a confidence score between 0.0 and 1.0.
4. Recommend an action.

You must never invent financial records.
You must never modify financial data.
You must return UNKNOWN when the evidence is insufficient.

Allowed categories:
FEE_ADJUSTMENT, DATE_DELAY, AMOUNT_MISMATCH, PARTIAL_PAYMENT, DUPLICATE,
MISSING_PAYMENT, MISSING_SETTLEMENT, MISSING_BANK_TRANSACTION, REFUND, UNKNOWN

You only interpret the evidence you are given. Never assert a fact that is
not present in the input context.
"""

USER_TEMPLATE = """Reconciliation context for order {order_id}:

- order_amount: {order_amount}
- payment_amount: {payment_amount}
- settlement_gross: {settlement_gross}
- settlement_fee: {settlement_fee}
- settlement_net: {settlement_net}
- bank_amount: {bank_amount}
- payment_date: {payment_date}
- settlement_date: {settlement_date}
- bank_date: {bank_date}
- payment_status: {payment_status}
- duplicate_payment_id: {duplicate_payment_id}
- observed_difference: {observed_difference}

Respond with JSON only, using exactly this schema:
{{
  "classification": "one of the allowed categories",
  "confidence": 0.0,
  "reasoning_summary": "short explanation",
  "recommended_action": "APPROVE_RECONCILIATION | REQUIRES_REVIEW | ESCALATE",
  "requires_human_review": true
}}
"""


def build_user_message(context: dict) -> str:
    return USER_TEMPLATE.format(
        order_id=context.get("order_id", ""),
        order_amount=context.get("order_amount"),
        payment_amount=context.get("payment_amount"),
        settlement_gross=context.get("settlement_gross"),
        settlement_fee=context.get("settlement_fee"),
        settlement_net=context.get("settlement_net"),
        bank_amount=context.get("bank_amount"),
        payment_date=context.get("payment_date"),
        settlement_date=context.get("settlement_date"),
        bank_date=context.get("bank_date"),
        payment_status=context.get("payment_status", ""),
        duplicate_payment_id=context.get("duplicate_payment_id", "false"),
        observed_difference=context.get("observed_difference"),
    )