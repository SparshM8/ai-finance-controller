"""Evaluate the reconciliation engine against ground truth (SRS 37–39).

Reads the generated CSVs from data/, runs the deterministic engine, and
reports match rate, precision, recall, F1 and false-positive financial
impact. Reports are honest — no cherry-picking.

Usage:
    python scripts/evaluate.py [--data data] [--records 500] [--seed 42]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.app.services import ingestion  # noqa: E402
from backend.app.services.matching_engine import run_reconciliation  # noqa: E402
from backend.app.services.report_service import evaluate  # noqa: E402


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the reconciliation engine")
    parser.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args()

    data_dir = args.data
    required = ["orders.csv", "payments.csv", "settlements.csv", "bank_transactions.csv", "ground_truth.csv"]
    missing = [f for f in required if not (data_dir / f).exists()]
    if missing:
        print(f"Missing files in {data_dir}: {missing}")
        print("Run: uv run python scripts/generate_dataset.py --out data")
        sys.exit(1)

    files = {
        "orders": (data_dir / "orders.csv").read_bytes(),
        "payments": (data_dir / "payments.csv").read_bytes(),
        "settlements": (data_dir / "settlements.csv").read_bytes(),
        "bank_transactions": (data_dir / "bank_transactions.csv").read_bytes(),
    }
    records_by_kind, errors = ingestion.ingest_batch_files(files)
    if errors:
        print("Ingestion errors:", errors)
        sys.exit(1)

    results = run_reconciliation(
        records_by_kind["orders"],
        records_by_kind["payments"],
        records_by_kind["settlements"],
        records_by_kind["bank_transactions"],
    )
    ground_truth = load_csv(data_dir / "ground_truth.csv")
    metrics = evaluate(results, ground_truth)

    print("=" * 60)
    print("AI FINANCE CONTROLLER — ENGINE EVALUATION")
    print("=" * 60)
    print(f"Total transactions      : {metrics.total}")
    print(f"Matched (reconciled)    : {metrics.matched}  ({metrics.match_rate:.2%})")
    print(f"Unresolved              : {metrics.unresolved}")
    print("-" * 60)
    print("Exception classification (vs ground truth):")
    print(f"  Actual exceptions      : {metrics.actual_exceptions}")
    print(f"  Detected               : {metrics.detected_exceptions}")
    print(f"  Correctly classified   : {metrics.correct_classifications}")
    print(f"  False positives        : {metrics.false_positives}")
    print(f"  False negatives        : {metrics.false_negatives}")
    print(f"  Precision              : {metrics.precision:.2%}")
    print(f"  Recall                 : {metrics.recall:.2%}")
    print(f"  F1                     : {metrics.f1:.2%}")
    print(f"  FP financial impact    : ₹{metrics.false_positive_financial_impact:,.2f}")
    print("=" * 60)


if __name__ == "__main__":
    main()