"""Pydantic response/request schemas for the API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    role: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_name: str
    uploaded_by: int
    total_records: int
    status: str
    created_at: datetime
    completed_at: datetime | None = None


class ReconciliationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    order_id: str
    payment_id: str | None
    settlement_id: str | None
    bank_transaction_id: str | None
    order_date: date | None
    payment_date: date | None
    settlement_date: date | None
    bank_date: date | None
    status: str
    match_type: str | None
    exception_type: str | None
    expected_amount: Decimal
    actual_amount: Decimal
    difference: Decimal
    currency: str
    confidence: float
    explanation: str
    recommended_action: str
    requires_human_review: bool


class ExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reconciliation_id: int
    type: str
    severity: str
    description: str
    ai_explanation: str
    ai_confidence: float
    recommended_action: str
    status: str
    resolved_by: str | None
    resolved_at: datetime | None
    reviewer_comment: str


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str
    entity_id: str
    action: str
    actor: str
    old_value: str
    new_value: str
    reason: str
    timestamp: datetime


class AnalysisOut(BaseModel):
    classification: str
    confidence: float
    reasoning_summary: str
    recommended_action: str
    requires_human_review: bool
    source: str  # "deterministic" | "llm"


class PagedResponse(BaseModel):
    items: list
    total: int
    limit: int
    offset: int


class DashboardSummary(BaseModel):
    total_transactions: int
    matched: int
    review: int
    unresolved: int
    match_rate: float
    total_value: dict[str, float]
    reconciled_value: dict[str, float]
    at_risk_value: dict[str, float]
    exception_breakdown: dict[str, int]
    high_value_review_count: int
    avg_ai_confidence: float | None
    latest_batch_id: int | None
    latest_batch_status: str | None


class ReconcileResponse(BaseModel):
    batch_id: int
    status: str
    total: int
    matched: int
    review: int
    unresolved: int
    match_rate: float