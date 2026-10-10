# AI Finance Controller

**Track 04 — AI-assisted financial reconciliation platform.**

An AI-assisted finance operations platform that ingests merchant orders, payments, gateway
settlements and bank transactions, reconciles them deterministically, classifies and explains
exceptions with an AI reasoning layer, and produces measurable, auditable reconciliation metrics.

> **"Turn raw financial transactions into a continuously reconciled, explainable and auditable financial position."**

The AI **never touches the money math**. Deterministic code performs all arithmetic, ID linking,
date-window and fee calculations; the AI layer only interprets ambiguous cases and explains
exceptions. Every AI recommendation passes through rule validation before it is accepted, and
every action is written to an audit trail.

---

## Architecture

```
                  ┌────────────────────┐
                  │   Web dashboard   │
                  └─────────┬──────────┘
                            │
                  ┌─────────▼──────────┐
                  │   FastAPI / API    │  JWT auth · role-based access
                  └─────────┬──────────┘
                            │
      ┌─────────────────────┼─────────────────────┐
      ▼                     ▼                     ▼
┌──────────────┐   ┌────────────────┐   ┌─────────────────┐
│  Ingestion   │   │  AI Analyzer   │   │   Reports /     │
│  + matching  │   │  deterministic │   │   Metrics       │
│  engine      │   │  + optional LLM│   │                 │
└──────┬───────┘   └───────┬────────┘   └────────┬────────┘
       └───────────────────┼─────────────────────┘
                           ▼
                 ┌──────────────────┐
                 │    SQLite /      │
                 │    PostgreSQL    │
                 └──────────────────┘
```

**Three separated layers (the interview-defensible decision):**

```
AI Layer            "What does this mean?"     classification + explanation
Decision/Policy     "Is this allowed?"         confidence thresholds · high-value rule
Deterministic Core  "What actually happened?"  all arithmetic, matching, state changes
```

- **AI = reasoning.** Interprets exceptions, writes explanations, classifies ambiguous cases.
- **Policy = authorization.** Confidence ≥ 0.90 auto-recommends; 0.70–0.90 human review;
  < 0.70 escalate. Transactions above ₹50,000 always require human review (SRS 51).
- **Finance engine = truth.** All calculations, ID matching, fee verification and database
  writes are deterministic. LLM output is validated against these facts before acceptance.

---

## Quickstart

```bash
# 1. Install dependencies (uv)
uv sync

# 2. Generate the synthetic dataset (500 records, seeded, with ground truth)
uv run python scripts/generate_dataset.py --records 500 --seed 42 --out data

# 3. Run the evaluation (match rate, precision, recall, F1)
uv run python scripts/evaluate.py

# 4. Start the API
uv run uvicorn backend.app.main:app --reload
```

Demo credentials (seeded automatically): `admin@example.com` / `admin123`

- **Web dashboard (no build step):** http://127.0.0.1:8000/ —
  select **Try local demo** to sign in and load/reconcile the generated dataset,
  or sign in with your credentials. Includes CSV upload, transaction search,
  exception analysis/review, finance Q&A, and the audit trail.
- **API docs:** http://127.0.0.1:8000/docs
- **One-click demo data:** `POST /api/demo/load` ingests `data/*.csv` and
  reconciles it (idempotent — reuses the existing demo batch).

### Full demo cycle

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"admin123"}' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

BATCH=$(curl -s -X POST http://127.0.0.1:8000/api/batches/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "batch_name=Demo" -F "orders=@data/orders.csv" -F "payments=@data/payments.csv" \
  -F "settlements=@data/settlements.csv" -F "bank_transactions=@data/bank_transactions.csv")
BID=$(echo $BATCH | python -c 'import sys,json;print(json.load(sys.stdin)["batch_id"])')

curl -s -X POST "http://127.0.0.1:8000/api/batches/$BID/reconcile" -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:8000/api/dashboard/summary -H "Authorization: Bearer $TOKEN"
curl -s "http://127.0.0.1:8000/api/exceptions?limit=20" -H "Authorization: Bearer $TOKEN"
```

### Optional LLM analysis

Set `AI_API_KEY` (any OpenAI-compatible endpoint — OpenAI, Groq, Ollama…) in `.env`. The
deterministic analyzer always runs; the LLM is consulted **only** for ambiguous cases
(UNKNOWN or confidence < 0.70), and its classification must pass deterministic rule
validation or it is rejected and retained for manual review. Without a key, the system
works fully — this is deliberate: the demo must not depend on an external model.

---

## What the engine does

For every order, the deterministic engine (SRS 16–24):

1. **Duplicate detection** — payment/settlement ids appearing more than once.
2. **Refund detection** — payments marked `refunded`.
3. **Missing records** — no payment / no settlement / no bank credit.
4. **Partial payments** — payment covers < 100% of the order.
5. **Fee-adjusted matching** — verifies `gross − fee = net` and that the fee explains the
   difference vs the order → `MATCHED_WITH_FEE` (SRS 19, 26).
6. **Date-window matching** — settlements more than N days (default 3) after the payment
   are flagged `DATE_DELAY` (SRS 18).
7. **Bank matching** — reference-ID first; amount+date fallback **only** for bank rows with
   no reference (an explicit reference always wins). Multiple plausible credits → fail
   safely as ambiguous (SRS 54).
8. **Amount mismatch** — unexplained gaps are flagged; internally inconsistent settlement
   records (gross − fee ≠ net) are classified `UNKNOWN` at low confidence.
9. **High-value policy** — matched transactions above ₹50,000 are marked
   `requires_human_review` regardless of confidence (SRS 51).

Statuses: `RECONCILED` · `MATCHED_WITH_FEE` (count toward match rate) · `REVIEW_REQUIRED`
(0.70–0.90 confidence) · `UNRESOLVED` (< 0.70 confidence).

---

## Evaluation (honest numbers, no cherry-picking)

`scripts/evaluate.py` compares engine output against `ground_truth.csv` on the full batch:

| Metric | Value |
| --- | --- |
| Total transactions | 500 |
| Match rate | 80.0% |
| Exception precision | 100% |
| Exception recall | 100% |
| F1 | 100% |
| False positives | 0 |
| False-positive financial impact | ₹0 |

The dataset is 70% clean / 30% anomalies (fee adjustments, date delays, amount mismatches,
duplicates, missing settlements, partial payments, refunds, missing bank/payment, and
inconsistent records), so ~80% match rate is the honest expectation — the same mix the SRS
specifies (SRS 44). The dashboard's illustrative "89.4%" in the SRS assumed a cleaner mix;
these metrics reflect what the data actually contains.

## Tests

```bash
uv run pytest
```

- `tests/test_matching.py` — every matching rule in isolation.
- `tests/test_reconciliation.py` — full-batch evaluation against ground truth.
- `tests/test_ai.py` — deterministic analysis, LLM escalation, guardrail validation.
- `tests/test_api.py` — auth, upload, reconcile, dashboard, review, audit, RBAC.

---

## API surface

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/auth/register` · `/login` | Auth (JWT) |
| POST | `/api/batches/upload` | Upload 4 CSVs (validated + normalized) |
| POST | `/api/batches/{id}/reconcile` | Run reconciliation |
| GET | `/api/batches` · `/api/batches/{id}` | Batch list / detail |
| GET | `/api/reconciliations` · `/{id}` | Transaction explorer + search |
| GET | `/api/exceptions` · `/{id}` | Exception list / detail |
| POST | `/api/exceptions/{id}/analyze` | Recommendation only; writes an audit event |
| POST | `/api/exceptions/{id}/approve\|reject\|override` | Human review (audited) |
| GET | `/api/dashboard/summary` | KPIs |
| GET | `/api/audit-logs` | Audit trail |
| POST | `/api/ai/ask?question=...` | Deterministic database-backed Q&A |

Roles (SRS 7): `admin` · `finance_controller` · `analyst` · `viewer` — upload requires
admin/controller/analyst, override requires admin/controller, everything else is viewable
by all authenticated users.

## Configuration

See `.env.example`. Notable knobs: `DATABASE_URL` (SQLite default, Postgres-ready),
`HIGH_VALUE_THRESHOLD`, `SETTLEMENT_DELAY_WINDOW_DAYS`, `JWT_SECRET`, `AI_API_KEY`.

## Project layout

```
backend/app/           FastAPI app
  api/                 routers (auth, batches, reconciliation, exceptions, dashboard, audit)
  services/            ingestion, matching_engine, reconciliation_service, exception_service, report_service
  ai/                  analyzer.py (deterministic + optional LLM), prompts.py
  models.py schemas.py security.py deps.py database.py config.py
scripts/               generate_dataset.py (synthetic data + ground truth), evaluate.py
tests/                 pytest suite
data/                  generated CSVs + ground truth (regenerable, gitignored)
```

## Current status and next steps

The local MVP includes a responsive HTML/CSS/JavaScript dashboard, CSV upload,
reconciliation, exception review, deterministic Q&A, and audit views. Python 3.12+
with uv is required; Node and an LLM API key are not needed to run it.

At-risk value means the full expected order value of non-matched transactions;
accepted processing fees are excluded. This is review exposure, not proven loss.
Review decisions close exception cases without rewriting deterministic findings.
Dashboard totals span all batches and are not currency-converted.

See [PROJECT_REVIEW.md](PROJECT_REVIEW.md) for requirements, verification, and
known limitations. Priorities before real financial use:

1. Harden matching for split payments, duplicate settlements, and bank-credit reuse.
2. Replace floating-point money with decimal/minor-unit storage and calculations.
3. Remove demo authentication defaults; add deployment security and migrations.
4. Add CSV/PDF exports, scheduled jobs, and merchant/currency isolation.
5. Add real-browser tests and validate against independently labeled real-world data.