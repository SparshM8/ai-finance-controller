"""CSV ingestion: validation, normalization, deduplication (SRS 13–15).

Every uploaded file is converted into normalized records (dates as
``date`` objects, amounts as floats) or rejected with per-row errors.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

SOURCE_KINDS = ("orders", "payments", "settlements", "bank_transactions")

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "orders": ["order_id", "order_date", "amount"],
    "payments": ["payment_id", "order_id", "payment_date", "amount"],
    "settlements": ["settlement_id", "payment_id", "settlement_date", "gross_amount", "net_amount"],
    "bank_transactions": ["bank_txn_id", "transaction_date", "reference", "amount"],
}

_AMOUNT_RE = re.compile(r"[^\d.-]")


def parse_amount(value: Any) -> Decimal | None:
    """Parse '₹2,500', '2,500.00', '2500' -> Decimal('2500.00'). Returns None if invalid."""
    if value is None:
        return None
    cleaned = _AMOUNT_RE.sub("", str(value).strip())
    if cleaned in ("", "-", "."):
        return None
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def parse_date(value: Any) -> date | None:
    """Accept ISO (2026-09-01), DD/MM/YYYY and DD-MM-YYYY. Returns None if invalid."""
    if value is None:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_headers(headers: list[str]) -> list[str]:
    return [h.strip().lower() for h in headers]


def _row_to_dict(headers: list[str], row: list[str]) -> dict[str, str]:
    return {headers[i].strip().lower(): (row[i].strip() if i < len(row) else "") for i in range(len(headers))}


class IngestionError(ValueError):
    """Raised when an uploaded file cannot be accepted at all."""


def parse_csv_file(kind: str, content: bytes) -> list[dict[str, str]]:
    """Parse raw bytes into a list of header-normalized row dicts."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestionError(f"{kind}: file must be UTF-8 encoded text") from exc

    reader = csv.reader(io.StringIO(text))
    try:
        header_row = next(reader)
    except StopIteration as exc:
        raise IngestionError(f"{kind}: file is empty") from exc

    headers = _normalize_headers(header_row)
    missing = [c for c in REQUIRED_COLUMNS[kind] if c not in headers]
    if missing:
        raise IngestionError(f"{kind}: missing required column(s): {', '.join(missing)}")

    rows: list[dict[str, str]] = []
    for line_no, row in enumerate(reader, start=2):
        if not row or all(not cell.strip() for cell in row):
            continue
        rows.append(_row_to_dict(headers, row))
    return rows


def normalize_records(kind: str, rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Convert raw rows into typed records. Returns (records, errors)."""
    records: list[dict[str, Any]] = []
    errors: list[str] = []

    for idx, raw in enumerate(rows):
        line_no = idx + 2  # header is line 1
        record: dict[str, Any] = {}

        if kind == "orders":
            amount = parse_amount(raw.get("amount"))
            order_date = parse_date(raw.get("order_date"))
            if amount is None:
                errors.append(f"orders: invalid amount at row {line_no}")
            if order_date is None:
                errors.append(f"orders: invalid order_date at row {line_no}")
            record.update({
                "order_id": raw.get("order_id", "").strip(),
                "customer_id": raw.get("customer_id", "").strip(),
                "order_date": order_date,
                "amount": amount,
                "currency": raw.get("currency", "INR").strip().upper() or "INR",
                "status": raw.get("status", "completed").strip().lower(),
            })
        elif kind == "payments":
            amount = parse_amount(raw.get("amount"))
            payment_date = parse_date(raw.get("payment_date"))
            if amount is None:
                errors.append(f"payments: invalid amount at row {line_no}")
            if payment_date is None:
                errors.append(f"payments: invalid payment_date at row {line_no}")
            record.update({
                "payment_id": raw.get("payment_id", "").strip(),
                "order_id": raw.get("order_id", "").strip(),
                "payment_date": payment_date,
                "amount": amount,
                "status": raw.get("payment_status", raw.get("status", "success")).strip().lower(),
            })
        elif kind == "settlements":
            gross = parse_amount(raw.get("gross_amount"))
            fee = parse_amount(raw.get("fee"))
            if fee is None:
                fee = Decimal("0.00")
            net = parse_amount(raw.get("net_amount"))
            settlement_date = parse_date(raw.get("settlement_date"))
            if gross is None:
                errors.append(f"settlements: invalid gross_amount at row {line_no}")
            if net is None:
                errors.append(f"settlements: invalid net_amount at row {line_no}")
            if settlement_date is None:
                errors.append(f"settlements: invalid settlement_date at row {line_no}")
            record.update({
                "settlement_id": raw.get("settlement_id", "").strip(),
                "payment_id": raw.get("payment_id", "").strip(),
                "settlement_date": settlement_date,
                "gross_amount": gross,
                "fee": fee,
                "net_amount": net,
                "status": raw.get("status", "success").strip().lower(),
            })
        else:  # bank_transactions
            amount = parse_amount(raw.get("amount"))
            txn_date = parse_date(raw.get("transaction_date"))
            if amount is None:
                errors.append(f"bank_transactions: invalid amount at row {line_no}")
            if txn_date is None:
                errors.append(f"bank_transactions: invalid transaction_date at row {line_no}")
            record.update({
                "bank_txn_id": raw.get("bank_txn_id", raw.get("bank_transaction_id", "")).strip(),
                "transaction_date": txn_date,
                "reference": raw.get("reference", "").strip(),
                "amount": amount,
                "type": raw.get("type", "CREDIT").strip().upper(),
            })

        id_key = record_id_key(kind)
        if not record.get(id_key):
            errors.append(f"{kind}: missing id at row {line_no}")
        amount_key = {"orders": "amount", "payments": "amount", "settlements": "net_amount", "bank_transactions": "amount"}[kind]
        if record.get(amount_key) is None:
            continue
        records.append(record)
        record["id_key_for_dup"] = id_key

    # Duplicate detection within the file (SRS FR-010).
    id_key = record_id_key(kind)
    counts: dict[str, int] = {}
    for rec in records:
        key = str(rec.get("id_key_for_dup", rec.get(id_key, "")))
        counts[key] = counts.get(key, 0) + 1
    _HAS_DUPLICATE_COL = {"payments", "settlements"}
    for rec in records:
        if counts.get(str(rec.get("id_key_for_dup", rec.get(id_key, "")))) and counts[str(rec.get("id_key_for_dup", rec.get(id_key, "")))] > 1:
            if kind in _HAS_DUPLICATE_COL:
                rec["duplicate"] = True

    return records, errors


def record_id_key(kind: str) -> str:
    return {
        "orders": "order_id",
        "payments": "payment_id",
        "settlements": "settlement_id",
        "bank_transactions": "bank_txn_id",
    }[kind]


def ingest_batch_files(files: dict[str, bytes]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Validate + normalize all four source files. Returns (records_by_kind, errors)."""
    records_by_kind: dict[str, list[dict[str, Any]]] = {}
    all_errors: list[str] = []

    for kind in SOURCE_KINDS:
        content = files.get(kind)
        if content is None:
            all_errors.append(f"Missing file: {kind}.csv")
            continue
        try:
            rows = parse_csv_file(kind, content)
        except IngestionError as exc:
            all_errors.append(str(exc))
            continue
        records, errors = normalize_records(kind, rows)
        all_errors.extend(errors)
        records_by_kind[kind] = records

    return records_by_kind, all_errors
