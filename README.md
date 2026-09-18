# FCIP — Financial Crime Intelligence Platform (walking skeleton)

Capstone project. This repo is being built as a thin end-to-end slice first
(auth, core data model, CSV ingestion, one detection pattern, alert/case
triage, minimal UI), following FRD-FCIP and TRD-FCIP. Out of scope for this
stage: entity resolution, risk scoring, watchlist/PEP screening, graph
investigation, AI narrative, maker-checker approval, dashboards, and
additional detection patterns — these come in later iterations.

## Stack

- Backend: FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, Python 3.11
- Frontend: React + TypeScript + Vite, TanStack Query
- Database: PostgreSQL 16
- Auth: JWT (access + rotating refresh token), argon2 password hashing
- Packaging: Docker + Docker Compose
- Testing: pytest

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

- Backend: http://localhost:8000 (docs at `/docs`, health check at `/health`)
- Frontend: http://localhost:5173
- Postgres: localhost:5432

The backend container runs `alembic upgrade head` before starting the API,
so the schema is always up to date on boot.

## Running backend tests

```bash
docker compose exec backend pytest
```

Tests run against a separate database named `<your db>_test` (e.g. `fcip_test`),
created and migrated automatically and truncated before every test, so they
never touch your dev data. The suite refuses to run if the target database
name doesn't end in `_test`.

## Auth

Login/refresh/logout is JWT-based, with rotating refresh tokens tracked
server-side in the `sessions` table (TRD §12.2) so they can be revoked and
audited — not stateless JWTs.

Seed dev data (idempotent, safe to re-run):

```bash
docker compose exec backend python -m app.scripts.seed
```

This creates one user per role — `dataops@fcip.internal`,
`analyst@fcip.internal`, `triage@fcip.internal`, `investigator@fcip.internal`,
all with password `DevPassword123!` (dev-only, never use in production) — plus
6 synthetic customers (`CUST-001`..`CUST-006`) and their 8 accounts.

```bash
# Login
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"triage@fcip.internal","password":"DevPassword123!"}'

# Use the access_token from the response above
curl -s http://localhost:8000/auth/me -H "Authorization: Bearer <access_token>"

# Rotate the refresh_token from the login response
curl -s -X POST http://localhost:8000/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"<refresh_token>"}'

# Logout (revokes the session)
curl -s -X POST http://localhost:8000/auth/logout \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"<refresh_token>"}'
```

Role-based access control lives in `app/core/rbac.py` — `require_role(...)`
is a FastAPI dependency that endpoints use to restrict access per the
persona/permission matrix from FRD §4.1.

## Ingestion (CSV upload + quarantine)

`POST /ingestion/transactions` (ROLE_DATA_OPS only) takes a multipart CSV
upload. Valid rows become `transaction` records; rows that fail validation are
written to `quarantine_item` with the original raw values and the reason, never
dropped silently.

Required columns: `transaction_ref`, `account_number`, `transaction_date`,
`amount`, `currency`, `direction`, `channel`. Optional: `counterparty_ref`,
`description`. Header names are matched case-insensitively.

| Field | Rule |
|---|---|
| `amount` | plain positive number, max 2 decimals (`1500000.00`); no thousands separators |
| `transaction_date` | ISO 8601 date or datetime; no offset means UTC |
| `currency` | 3 letters (normalized to upper case) |
| `direction` | `CREDIT` or `DEBIT` (case-insensitive) |
| `transaction_ref` | must be unique — already-ingested or repeated refs are quarantined |
| `account_number` | must already exist (accounts are seeded, not ingested) |

A file that can't be interpreted at all (missing required column, not UTF-8,
malformed CSV, over 10 MB) is rejected with `422`/`413` and nothing is stored.
`row_number` in quarantine is spreadsheet-style: the header is row 1, the first
data row is row 2.

Try it with the bundled synthetic file (22 valid rows + 7 intentionally invalid
ones). Log in as `dataops@fcip.internal` first, then:

```bash
curl -s -X POST http://localhost:8000/ingestion/transactions \
  -H "Authorization: Bearer <access_token>" \
  -F "file=@backend/sample_data/transactions_sample.csv"
# expect: total_rows 29, accepted 22, quarantined 7
```

Uploading the same file again quarantines all 22 valid rows as "already exists",
so re-uploads are visible instead of silently ignored. Inspect quarantine with:

```bash
docker compose exec db psql -U fcip -d fcip \
  -c "select row_number, error_reason from quarantine_item order by created_at, row_number;"
```

You can also upload from the Swagger UI at http://localhost:8000/docs (click
"Authorize" and paste the access token).

## Detection: P02 Structuring

Implemented in `backend/app/services/detection/p02_structuring.py`. Parameters
(FRD §8.2) live in `P02Parameters`:

| Parameter | Value | Meaning |
|---|---|---|
| REPORTING_THRESHOLD | IDR 500,000,000 | the reporting threshold being evaded |
| BAND_LOWER | 70% → IDR 350,000,000 | a transaction is "in band" when `350,000,000 <= amount < 500,000,000` |
| MIN_COUNT | 3 | at least this many in-band transactions in one window |
| AGGREGATE_MULTIPLE | 1.0× → IDR 500,000,000 | the window's in-band total must reach this |
| Window | 7 days rolling | opens at an in-band transaction, end is exclusive |

Aggregation is per entity across **all** of its accounts and channels, in both
directions (CREDIT and DEBIT). Only IDR transactions are considered because
the threshold is in IDR and there is no FX conversion yet. Windows don't
overlap: once a window qualifies, its transactions become the evidence for one
alert and scanning resumes after it, so a transaction never backs two alerts.

Every alert stores the FRD §8.2 factors in `alert.detection_details`
(`TXN_COUNT_IN_BAND`, `AGGREGATE_AMOUNT`, `THRESHOLD_APPLIED`,
`BAND_LOWER_APPLIED`, `AMOUNT_DISPERSION_CV`, `DISTINCT_ACCOUNTS`,
`DISTINCT_COUNTERPARTIES`, `CHANNEL_MIX`) plus, for FR-307 reproducibility,
the exact `window_start`/`window_end` (UTC) and the parameters that were
applied. The evidence transactions are linked through `alert_transaction`.
Each alert creation writes an `audit_log` row (`ALERT`, `NULL -> OPEN`) with a
fresh `correlation_id` that is also stored on the alert, so disposition and
case events can later be chained to it.

Run it manually (no scheduler yet) either as a script or through the API
(ROLE_DATA_OPS or ROLE_ANALYST):

```bash
docker compose exec backend python -m app.scripts.run_detection

curl -s -X POST http://localhost:8000/detection/p02/run -H "Authorization: Bearer <access_token>"
```

Re-running on the same data is idempotent: alerts already raised for the same
evidence are reported with `created: false`, never duplicated.

**Expected result on the sample file** (after seeding and uploading
`transactions_sample.csv`): exactly 2 alerts. Each seeded customer is a
hand-checkable scenario:

| Customer | Scenario | Result |
|---|---|---|
| CUST-001 | 450M, 480M, 420M, 470M across 2 accounts within 6 days | **alert** (4 txns, IDR 1.82bn, CASH+TRANSFER) |
| CUST-002 | only 2 in-band deposits | no alert (below MIN_COUNT) |
| CUST-003 | 3 in-band transfers, 8 days apart each | no alert (never 3 in one window) |
| CUST-004 | 3 × exactly 500M | no alert (at threshold, not in band — would be reported normally) |
| CUST-005 | 4 × 300M, plus a USD transaction | no alert (below band; non-IDR ignored) |
| CUST-006 | 3 × exactly 350M over 3 days | **alert** (band lower bound is inclusive; CV = 0) |

## Alerts and triage

| Endpoint | Roles | Notes |
|---|---|---|
| `GET /alerts?status=OPEN&limit=50&offset=0` | all | queue; `status` is optional (`OPEN`, `DISPOSED`, `ESCALATED`), newest first |
| `GET /alerts/{id}` | all | detail: `detection_details`, `correlation_id`, disposition info, and the evidence `transactions` |
| `POST /alerts/{id}/disposition` | ROLE_TRIAGE | body `{"decision": "false_positive" \| "escalate", "reason": "..."}` |

Disposition rules (FRD §5.0): `reason` is mandatory and must be at least 20
characters after trimming, otherwise `422`. Only `OPEN` alerts can be
dispositioned — a second attempt returns `409`. `false_positive` moves the alert
to `DISPOSED`, `escalate` to `ESCALATED`; both write an `audit_log` row
(`ALERT`, `OPEN -> DISPOSED|ESCALATED`) with the actor, role, reason and the
alert's `correlation_id`.

```bash
# as triage@fcip.internal
curl -s "http://localhost:8000/alerts?status=OPEN" -H "Authorization: Bearer <token>"
curl -s http://localhost:8000/alerts/<alert_id> -H "Authorization: Bearer <token>"
curl -s -X POST http://localhost:8000/alerts/<alert_id>/disposition \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"decision":"escalate","reason":"Multiple sub-threshold cash deposits across two accounts"}'
```

## Cases

| Endpoint | Roles | Notes |
|---|---|---|
| `POST /cases` | ROLE_TRIAGE, ROLE_INVESTIGATOR | body `{"alert_id": "..."}`; the alert must be `ESCALATED` and not yet linked to a case (`409` otherwise) |
| `GET /cases/{id}` | all | case header plus the linked alerts |

Case numbers come from a Postgres sequence (`CASE-000001`, ...). Creating a
case sets `alert.case_id` and writes an `audit_log` row (`CASE`, `NULL ->
OPEN`) that reuses the originating alert's `correlation_id`, so one query
returns the whole chain:

```bash
docker compose exec db psql -U fcip -d fcip -c \
  "select object_type, from_state, to_state, actor_role, created_at from audit_log \
   where correlation_id = (select correlation_id from alert where id = '<alert_id>') order by created_at;"
#  ALERT  NULL -> OPEN       (detection)
#  ALERT  OPEN -> ESCALATED  (triage)
#  CASE   NULL -> OPEN       (investigator)
```

`IN_PROGRESS`/`CLOSED` transitions and assignment aren't exposed yet — the
columns exist, the endpoints come with the investigation workflow.

## Frontend

http://localhost:5173 — React + TypeScript + Vite, TanStack Query for data,
react-router for pages. Functional, not polished.

| Route | Page | What it does |
|---|---|---|
| `/login` | Login | email + password; backend errors (wrong password, lockout) are shown as-is |
| `/alerts` | Alert queue | table with status filter (Open / Escalated / Disposed / All), click a row for detail |
| `/alerts/:id` | Alert detail | reason, correlation id, evidence transactions, detection factors; triage form and "open case" button depending on role and status |
| `/cases/:id` | Case detail | case header (number, status, opened by) and the linked alerts |

Role-aware UI: the disposition form only renders for ROLE_TRIAGE on `OPEN`
alerts (submit stays disabled until the reason has 20 characters); the "Open
case" button only for ROLE_TRIAGE / ROLE_INVESTIGATOR on `ESCALATED` alerts
without a case. Other roles get the same pages read-only. The backend enforces
the same rules, the UI just doesn't offer what would be rejected.

Session handling (`src/api/client.ts`, `src/context/AuthContext.tsx`): tokens
are kept in `localStorage`; every request carries the access token; on a `401`
the client rotates the refresh token once (shared across concurrent requests)
and retries, and if that fails the session is cleared and the user lands on
`/login`. Reloading the page keeps you signed in.

Try it: seed, upload the sample CSV and run detection (sections above), then
sign in as `triage@fcip.internal` → open the CUST-006 alert → escalate with a
reason → "Open case from this alert". Sign in as `analyst@fcip.internal` to
see the read-only view. Screenshots of that flow are in `docs/screenshots/`.

## Known simplifications (skeleton stage)

These are deliberate shortcuts for the walking-skeleton stage, called out
here so they're easy to explain and are tracked for follow-up:

- **`customer_id` stands in for `entity_id`.** FRD §8.2's P02 structuring
  pattern aggregates per *entity*, but entity resolution isn't built yet, so
  the detection job (`backend/app/services/detection/p02_structuring.py`)
  aggregates by `customer_id` instead. Each customer is treated as its own
  entity for now; the grouping key is the one thing to swap once entity
  resolution exists.
- **P02 interpretation choices** (to confirm against FRD §8.2): the aggregate
  is the sum of the *in-band* transactions in the window; both CREDIT and
  DEBIT count; non-IDR transactions are ignored (no FX conversion); windows
  are non-overlapping. Each is a one-line change in `P02Parameters` /
  `find_clusters` if the FRD says otherwise.
- **No scheduler.** Detection is triggered manually (script or endpoint).
- **Tokens in `localStorage`.** Simple and enough for the skeleton; the
  hardened version keeps the refresh token in an httpOnly cookie so it isn't
  readable by page scripts.
- **Frontend has no automated tests in the repo.** It was verified with a
  scripted Playwright click-through (login, queue, disposition, case, role
  restrictions, token refresh); wiring that into CI is a follow-up.
- **CSV ingestion covers transactions only.** Customer and account records
  are pre-seeded reference data (see `backend/app/scripts/seed.py`), not part
  of the ingestion pipeline.
- **Naive timestamps are read as UTC.** A CSV `transaction_date` without a UTC
  offset is treated as UTC; source-system timezones aren't modelled yet.
- **Session security is simplified.** TRD §12.2's idle timeout (30 min) and
  absolute timeout (8h) are enforced using the `sessions` table's
  `last_used_at`/`expires_at` columns, but login lockout and refresh
  rotation are implemented with straightforward checks rather than a
  hardened auth service.

## Repository layout

```
backend/    FastAPI app, SQLAlchemy models, Alembic migrations, pytest tests
frontend/   React + TypeScript + Vite app
docker-compose.yml
```
