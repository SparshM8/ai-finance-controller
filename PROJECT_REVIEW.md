# Project review and delivery status


## Purpose and requirements

AI Finance Controller helps finance teams reconcile four transaction sources:
orders, payments, gateway settlements, and bank credits. The intended workflow is:

1. Authenticate with role-based permissions.
2. Upload and validate four CSV sources.
3. Link transactions and calculate discrepancies deterministically.
4. Explain exceptions; optionally consult an LLM for ambiguous cases only.
5. Have authorized people approve, reject, or override exception cases.
6. Track metrics and preserve an audit trail.

The repository README and source-code SRS references were used to infer these
requirements. A separate authoritative SRS was not available in the reviewed files.
The essential architectural boundary is that model output must not control money
arithmetic or silently change reconciliation facts.

## Stack and local setup

- Python 3.12+, uv, FastAPI, SQLAlchemy, SQLite by default.
- HTML/CSS/JavaScript frontend served by FastAPI; no frontend build required.
- Optional OpenAI-compatible model service for ambiguous exception analysis.
- Run `uv sync` from the project root.
- If sample CSVs are absent, run `uv run python scripts/generate_dataset.py --records 500 --seed 42 --out data`.
- Start with `uv run uvicorn backend.app.main:app --reload`.
- Open http://127.0.0.1:8000/ and select **Try local demo**.
- Demo credentials: `admin@example.com` / `admin123`.
- API documentation: http://127.0.0.1:8000/docs.

Use local/demo data only until the production blockers below are addressed.
Do not regenerate sample CSVs over user-maintained input files without a backup.

## Changes delivered

- Replaced a truncated, nonfunctional HTML fragment with a complete dashboard.
- Added separate stylesheet and JavaScript, served as static assets.
- Connected login, explicit demo loading, metrics, paginated transaction search,
  transaction detail, four-file upload/reconciliation, batch list, exception
  analysis/review, deterministic finance Q&A, and audit views.
- UI respects write/review roles; server authorization remains authoritative.
- Escaped data rendered into HTML and handled request/validation errors.
- Scoped raw evidence lookups in exception analysis to the reconciliation batch.
  Reused payment/settlement/bank IDs can no longer pull evidence from another batch.
- Corrected classification precision so false alarms on clean records count in
  the denominator.
- Removed accepted fees from at-risk totals in summaries and Q&A.
- Routed order-specific Q&A before general keyword intents. Duplicate order IDs
  across batches produce an ambiguity message rather than an arbitrary answer.
- Added six regression tests and updated stale README descriptions.

## Verification

- Baseline: 29 passing tests, 1 failing test (broken dashboard).
- After changes: 36 passing tests.
- JavaScript syntax check: `node --check backend/app/static/app.js` passed.
- Existing 500-record synthetic evaluation: 400 matched (80%), 150 detected and
  correctly classified exceptions, 100% precision/recall/F1, no false positives
  or false negatives.
- Tests exercise API flows and static asset serving. Real-browser click-through
  and layout testing were not performed; Playwright was not installed.
- Existing dependency/deprecation and short test JWT-key warnings remain.
- Synthetic data performance is not a production accuracy guarantee.

## Production blockers and next work

### Financial correctness (highest priority)

The engine is currently a simplified, order-centric demo. Broader regression
coverage and implementation work are required for:

- Decimal or integer minor-unit money arithmetic instead of binary floats.
- Multiple/split payments and settlements; explicit overpayment handling.
- Correct duplicate settlement detection (current code compares a payment ID
  against the set of duplicate settlement IDs).
- One-to-one allocation of bank credits: prevent a reference-free credit from
  being reused for multiple orders; reject ambiguous explicit-reference credits.
- Negative date windows, failed payments, duplicate orders and orphan records.
- Merchant/currency isolation and per-currency reporting. Current totals can mix
  currencies and batches; they are not a consolidated converted financial total.
- Dedicated high-value review lifecycle, not just a persistent review flag.

### Security and operations

- Remove automatic known-password admin seeding and disable demo routes outside
  an explicit development mode.
- Require a strong JWT secret, restrict CORS, add login rate limiting and HTTPS.
- Define account provisioning and tenant permissions; current self-registration
  permits analyst/viewer roles in a shared workspace.
- Add upload size limits, atomic batch persistence, concurrency-safe processing,
  and policies preventing reruns from discarding prior review state.
- Add schema migrations, backups, monitoring, and a tested deployment workflow.
- PostgreSQL needs an appropriate installed driver and integration validation;
  SQLite is the verified local configuration.

### Product improvements

- CSV/PDF report exports and batch/currency-scoped dashboard filters.
- Pagination for exception/audit views beyond current display limits.
- Durable job queue and scheduled reconciliation for larger datasets.
- Broader Q&A intents and support for arbitrary merchant order ID formats.
- Browser automation, accessibility checks, and independent labeled datasets.

Recommended order: demonstrate and validate the restored local workflow, harden
financial invariants, secure deployment, then expand reports and automation.
