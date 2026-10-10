"""Synthetic dataset generator for the AI Finance Controller.

Generates orders / payments / settlements / bank_transactions CSVs plus a
``ground_truth.csv`` used for objective evaluation of the reconciliation
engine (match rate, precision, recall, F1, false-positive financial impact).

Anomaly mix (per SRS section 44):
    70% clean, 10% fee adjustments, 5% date delays, 5% amount mismatches,
    3% duplicates, 3% missing settlements, 2% partial payments, 2% other.

Usage:
    python scripts/generate_dataset.py --records 500 --seed 42 --out data
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

ORDER_ID_FMT = "ORD{:04d}"
PAYMENT_ID_FMT = "PAY{:04d}"
SETTLEMENT_ID_FMT = "SET{:04d}"
BANK_ID_FMT = "BANK{:04d}"
TXN_ID_FMT = "TXN{:04d}"

BASE_DATE = date(2026, 9, 1)

# Nice round INR amounts; a small share exceed the 50k high-value threshold.
AMOUNTS = [
    500, 800, 1000, 1200, 1500, 1800, 2000, 2500, 3000, 3500, 4000, 4500,
    5000, 6000, 7500, 8000, 9000, 10000, 12500, 15000, 18000, 20000, 25000,
    30000, 35000, 40000, 45000, 50000, 60000, 75000, 100000, 150000,
]

BUCKET_WEIGHTS = [
    ("clean", 0.70),
    ("fee", 0.10),
    ("date_delay", 0.05),
    ("amount_mismatch", 0.05),
    ("duplicate", 0.03),
    ("missing_settlement", 0.03),
    ("partial", 0.02),
    ("other", 0.02),
]


@dataclass
class GeneratedDataset:
    """Normalized records in the engine's input format, plus ground truth."""

    orders: list[dict] = field(default_factory=list)
    payments: list[dict] = field(default_factory=list)
    settlements: list[dict] = field(default_factory=list)
    bank_txns: list[dict] = field(default_factory=list)
    ground_truth: list[dict] = field(default_factory=list)

    def to_files(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(out_dir / "orders.csv", _ORDER_HEADERS, self.orders, _order_row)
        _write_csv(out_dir / "payments.csv", _PAYMENT_HEADERS, self.payments, _payment_row)
        _write_csv(out_dir / "settlements.csv", _SETTLEMENT_HEADERS, self.settlements, _settlement_row)
        _write_csv(out_dir / "bank_transactions.csv", _BANK_HEADERS, self.bank_txns, _bank_row)
        _write_csv(out_dir / "ground_truth.csv", _GT_HEADERS, self.ground_truth, _gt_row)


# --------------------------------------------------------------------------
# CSV column layouts (SRS section 10)
# --------------------------------------------------------------------------
_ORDER_HEADERS = ["order_id", "customer_id", "order_date", "amount", "currency", "status"]
_PAYMENT_HEADERS = ["payment_id", "order_id", "payment_date", "amount", "payment_status"]
_SETTLEMENT_HEADERS = ["settlement_id", "payment_id", "settlement_date", "gross_amount", "fee", "net_amount"]
_BANK_HEADERS = ["bank_txn_id", "transaction_date", "reference", "amount", "type"]
_GT_HEADERS = ["transaction_id", "order_id", "actual_status", "exception_type", "resolved"]


def _order_row(r: dict) -> list:
    return [r["order_id"], r["customer_id"], r["order_date"].isoformat(), f"{r['amount']:.2f}", r["currency"], r["status"]]


def _payment_row(r: dict) -> list:
    return [r["payment_id"], r["order_id"], r["payment_date"].isoformat(), f"{r['amount']:.2f}", r["status"]]


def _settlement_row(r: dict) -> list:
    return [r["settlement_id"], r["payment_id"], r["settlement_date"].isoformat(),
            f"{r['gross_amount']:.2f}", f"{r['fee']:.2f}", f"{r['net_amount']:.2f}"]


def _bank_row(r: dict) -> list:
    return [r["bank_txn_id"], r["transaction_date"].isoformat(), r["reference"], f"{r['amount']:.2f}", r["type"]]


def _gt_row(r: dict) -> list:
    return [r["transaction_id"], r["order_id"], r["actual_status"], r["exception_type"], "true" if r["resolved"] else "false"]


def _write_csv(path: Path, headers: list[str], rows: list[dict], row_fn) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row_fn(row))


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
def _assign_buckets(n: int, rng: random.Random) -> list[str]:
    counts = {name: round(weight * n) for name, weight in BUCKET_WEIGHTS}
    counts["other"] = n - sum(v for k, v in counts.items() if k != "other")
    buckets: list[str] = []
    for name, count in counts.items():
        buckets.extend([name] * count)
    rng.shuffle(buckets)
    return buckets


def generate_dataset(n: int = 500, seed: int = 42) -> GeneratedDataset:
    """Create *n* transactions with a controlled anomaly mix."""
    rng = random.Random(seed)
    ds = GeneratedDataset()
    buckets = _assign_buckets(n, rng)

    for i, bucket in enumerate(buckets):
        order_id = ORDER_ID_FMT.format(i + 1)
        txn_id = TXN_ID_FMT.format(i + 1)
        customer_id = f"CUST{rng.randint(1, 200):03d}"
        amount = float(rng.choice(AMOUNTS))
        order_date = BASE_DATE + timedelta(days=rng.randint(0, 24))
        payment_date = order_date + timedelta(days=rng.randint(0, 1))

        order = {
            "order_id": order_id,
            "customer_id": customer_id,
            "order_date": order_date,
            "amount": amount,
            "currency": "INR",
            "status": "completed",
        }
        payment_id = PAYMENT_ID_FMT.format(i + 1)
        payment = {
            "payment_id": payment_id,
            "order_id": order_id,
            "payment_date": payment_date,
            "amount": amount,
            "status": "success",
            "duplicate": False,
        }
        settlement: dict | None = None
        bank: dict | None = None

        status = "MATCHED"
        exception_type = "NONE"
        resolved = True

        if bucket == "clean":
            settlement = _make_settlement(i, payment_id, payment_date, amount, 0.0, amount, rng, delay=0)
            bank = _make_bank(i, payment_id, settlement["settlement_date"], amount, rng)
        elif bucket == "fee":
            fee = round(amount * rng.uniform(0.01, 0.03), 2)
            settlement = _make_settlement(i, payment_id, payment_date, amount, fee, amount - fee, rng, delay=0)
            bank = _make_bank(i, payment_id, settlement["settlement_date"], settlement["net_amount"], rng)
            status, exception_type = "MATCHED_WITH_FEE", "FEE_ADJUSTMENT"
        elif bucket == "date_delay":
            # Amounts are fully consistent, but the settlement arrives well
            # beyond the configured 3-day window.
            settlement = _make_settlement(i, payment_id, payment_date, amount, 0.0, amount, rng, delay=rng.randint(5, 8))
            bank = _make_bank(i, payment_id, settlement["settlement_date"], amount, rng)
            status, exception_type, resolved = "EXCEPTION", "DATE_DELAY", False
        elif bucket == "amount_mismatch":
            delta = round(amount * rng.uniform(0.02, 0.05), 2)
            if rng.random() < 0.5:
                # Settlement gross is short, with no fee to explain it.
                settlement = _make_settlement(i, payment_id, payment_date, amount - delta, 0.0, amount - delta, rng, delay=0)
                bank = _make_bank(i, payment_id, settlement["settlement_date"], amount - delta, rng)
            else:
                # Settlement is fine; the bank credit is short.
                settlement = _make_settlement(i, payment_id, payment_date, amount, 0.0, amount, rng, delay=0)
                bank = _make_bank(i, payment_id, settlement["settlement_date"], amount - delta, rng)
            status, exception_type, resolved = "EXCEPTION", "AMOUNT_MISMATCH", False
        elif bucket == "duplicate":
            # Same payment id appears twice in the payments file.
            settlement = _make_settlement(i, payment_id, payment_date, amount, 0.0, amount, rng, delay=0)
            bank = _make_bank(i, payment_id, settlement["settlement_date"], amount, rng)
            dup = dict(payment)
            ds.payments.append(dup)
            payment["duplicate"] = True
            status, exception_type, resolved = "EXCEPTION", "DUPLICATE", False
        elif bucket == "missing_settlement":
            # Payment recorded, settlement never arrives.
            status, exception_type, resolved = "EXCEPTION", "MISSING_SETTLEMENT", False
        elif bucket == "partial":
            # Only a fraction of the order is paid; no second payment exists.
            payment["amount"] = round(amount * rng.uniform(0.6, 0.7), 2)
            status, exception_type, resolved = "EXCEPTION", "PARTIAL_PAYMENT", False
        else:  # "other": rotate through harder anomaly shapes
            variant = rng.choice(["missing_payment", "refund", "missing_bank", "inconsistent"])
            if variant == "missing_payment":
                ds.orders.append(order)
                ds.ground_truth.append(_gt(txn_id, order_id, "EXCEPTION", "MISSING_PAYMENT", False))
                continue
            elif variant == "refund":
                payment["status"] = "refunded"
                status, exception_type, resolved = "EXCEPTION", "REFUND", False
            elif variant == "missing_bank":
                settlement = _make_settlement(i, payment_id, payment_date, amount, 0.0, amount, rng, delay=0)
                status, exception_type, resolved = "EXCEPTION", "MISSING_BANK_TRANSACTION", False
            else:  # inconsistent settlement record: gross - fee != net
                settlement = _make_settlement(i, payment_id, payment_date, amount, 0.0, amount - 500.0, rng, delay=0)
                bank = _make_bank(i, payment_id, settlement["settlement_date"], settlement["net_amount"], rng)
                status, exception_type, resolved = "EXCEPTION", "UNKNOWN", False

        ds.orders.append(order)
        ds.payments.append(payment)
        if settlement is not None:
            ds.settlements.append(settlement)
        if bank is not None:
            ds.bank_txns.append(bank)
        ds.ground_truth.append(_gt(txn_id, order_id, status, exception_type, resolved))

    return ds


def _gt(txn_id: str, order_id: str, status: str, exception_type: str, resolved: bool) -> dict:
    return {
        "transaction_id": txn_id,
        "order_id": order_id,
        "actual_status": status,
        "exception_type": exception_type,
        "resolved": resolved,
    }


def _make_settlement(i: int, payment_id: str, payment_date: date, gross: float, fee: float,
                     net: float, rng: random.Random, delay: int) -> dict:
    settlement_date = payment_date + timedelta(days=delay)
    return {
        "settlement_id": SETTLEMENT_ID_FMT.format(i + 1),
        "payment_id": payment_id,
        "settlement_date": settlement_date,
        "gross_amount": round(gross, 2),
        "fee": round(fee, 2),
        "net_amount": round(net, 2),
        "status": "success",
        "duplicate": False,
    }


def _make_bank(i: int, payment_id: str, settlement_date: date, amount: float, rng: random.Random) -> dict:
    return {
        "bank_txn_id": BANK_ID_FMT.format(i + 1),
        "transaction_date": settlement_date + timedelta(days=rng.randint(0, 1)),
        "reference": payment_id,
        "amount": round(amount, 2),
        "type": "CREDIT",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic reconciliation data")
    parser.add_argument("--records", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data"))
    args = parser.parse_args()

    ds = generate_dataset(args.records, args.seed)
    ds.to_files(args.out)
    print(f"Wrote {len(ds.orders)} transactions to {args.out}/")
    print(f"  orders={len(ds.orders)} payments={len(ds.payments)} "
          f"settlements={len(ds.settlements)} bank_txns={len(ds.bank_txns)}")
    by_type: dict[str, int] = {}
    for row in ds.ground_truth:
        by_type[row["exception_type"]] = by_type.get(row["exception_type"], 0) + 1
    print("  anomaly mix:", {k: v for k, v in sorted(by_type.items())})


if __name__ == "__main__":
    main()