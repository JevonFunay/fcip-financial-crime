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

Login/refresh/logout follows TRD §12.2:

- The **access token** (15 min) is returned in the JSON body and sent as
  `Authorization: Bearer` — it's the only credential page scripts ever hold.
- The **refresh token** never appears in a response body. It's set as a
  `fcip_refresh_token` cookie with `HttpOnly; Secure; SameSite=Strict;
  Path=/auth`, rotated on every `/auth/refresh`, and tracked server-side in
  the `sessions` table (hash only) so it can be revoked. `Max-Age` equals the
  remaining absolute session lifetime (8h from login).
- `/auth/refresh` reads the token from the cookie only; `/auth/logout` revokes
  the session and expires the cookie. A rejected refresh also expires the
  cookie so browsers drop dead sessions.
- CORS runs with `allow_credentials=True` and an explicit origin list
  (`CORS_ORIGINS`), and the frontend sends `withCredentials: true`. Frontend
  and API must share a hostname (both `localhost`) — `SameSite=Strict` treats
  `127.0.0.1` and `localhost` as different sites.
- `COOKIE_SECURE=true` by default. Chrome and Firefox accept `Secure` cookies
  on `http://localhost`; Safari doesn't, so for Safari on plain-http local dev
  set `COOKIE_SECURE=false` in `.env`.

Seed dev data (idempotent, safe to re-run):

```bash
docker compose exec backend python -m app.scripts.seed
```

This creates one user per role — `dataops@fcip.internal`,
`analyst@fcip.internal`, `triage@fcip.internal`, `investigator@fcip.internal`,
all with password `DevPassword123!` (dev-only, never use in production) — plus
6 synthetic customers (`CUST-001`..`CUST-006`) and their 8 accounts.

```bash
# Login: access token in the body, refresh token in a Set-Cookie header (-c stores it)
curl -s -c cookies.txt -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"triage@fcip.internal","password":"DevPassword123!"}'

# Use the access_token from the response above
curl -s http://localhost:8000/auth/me -H "Authorization: Bearer <access_token>"

# Rotate: the cookie jar sends the refresh cookie (-b) and stores the new one (-c)
curl -s -b cookies.txt -c cookies.txt -X POST http://localhost:8000/auth/refresh

# Logout: revokes the session and expires the cookie
curl -s -b cookies.txt -c cookies.txt -X POST http://localhost:8000/auth/logout -o /dev/null -w "%{http_code}\n"
```

Role-based access control lives in `app/core/rbac.py` — `require_role(...)`
is a FastAPI dependency that endpoints use to restrict access per the
persona/permission matrix from FRD §4.1.

## Ingestion (batch + CSV upload + quarantine)

`POST /ingestion/transactions` (ROLE_DATA_OPS only) takes a multipart CSV
upload. Valid rows become `transaction` records; rows that fail validation are
written to `quarantine_item` with the original raw values and the reason, never
dropped silently.

### Batch registration (FR-101) and processing log (FR-105)

Every upload runs inside a registered **ingestion batch**, so a row can always
be traced back to the file, the checksum, the business date and the person who
loaded it.

| Form field | Required | Default | Meaning |
|---|---|---|---|
| `file` | yes | — | the CSV |
| `source_system` | no | `MANUAL_UPLOAD` | the upstream system the file came from |
| `business_date` | no | today (UTC) | the business day the file is filed against |
| `expected_records` | no | — | how many rows the sender claims; checked at reconciliation |

The batch gets a `BAT-000001`-style reference from a Postgres sequence, a
SHA-256 checksum of the file, and its own `correlation_id`. **The same file
cannot be registered twice for the same source system and business date** — the
second attempt gets `409` naming the existing batch. The same file *on a
different business date*, or from a *different source system*, is allowed: that
is a genuine re-send, and its rows are then quarantined one by one as "already
exists" rather than silently ignored.

Batch status follows what actually happened:

| Status | When |
|---|---|
| `REGISTERED` | batch created, parsing not finished |
| `COMPLETED` | every input row accounted for (`accepted + quarantined == rows read`) |
| `NEEDS_REVIEW` | reconciliation mismatch — e.g. `expected_records` doesn't match rows read (TRD C-02: flagged, never quietly completed) |
| `FAILED` | structural failure (bad header, not UTF-8, NUL bytes) — zero rows loaded, and the batch stays visible as FAILED (TRD C-01) |

`processing_log` records what the platform did with the file, in the order it
happened (`BATCH_REGISTERED` → `VALIDATION_COMPLETED` → `RECONCILIATION_OK` /
`RECONCILIATION_MISMATCH`, or `BATCH_FAILED`). It carries an identity `seq`
column because Postgres `now()` is transaction start time, so entries written
in one transaction share a timestamp and could not otherwise be ordered.

| Endpoint | Roles | Notes |
|---|---|---|
| `GET /ingestion/batches` | all | registered batches, newest first, with counts and who loaded them |
| `GET /ingestion/batches/{id}` | all | one batch plus its full processing log |

```bash
curl -s -X POST http://localhost:8000/ingestion/transactions \
  -H "Authorization: Bearer <access_token>" \
  -F "file=@backend/sample_data/transactions_sample.csv" \
  -F "source_system=CORE_BANKING" -F "business_date=2026-09-23" -F "expected_records=29"
# -> {"batch_ref": "BAT-000001", "batch_status": "COMPLETED", "total_rows": 29, "accepted": 22, "quarantined": 7, ...}
```

Both are on the Overview page too, so none of this needs Swagger.

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
malformed CSV, over 50 MB) is rejected with `422`/`413` and nothing is stored.
`row_number` in quarantine is spreadsheet-style: the header is row 1, the first
data row is row 2.

The easiest way to try it is the **Upload CSV** control on the Overview page,
signed in as `dataops@fcip.internal`. From the command line, with the bundled
synthetic file (22 valid rows + 7 intentionally invalid ones):

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

The interactive API docs at http://localhost:8000/docs work too (click
"Authorize" and paste the access token).

### NFR-05: 100k-row volume (FRD §14.1)

FRD §14.1 requires ingesting 100,000 transactions, including validation, in
under 10 minutes. Generate a synthetic 100k-row file (~95% valid rows against
the seeded accounts, ~5% invalid with the same error variety as
`transactions_sample.csv`):

```bash
docker compose exec backend python -m app.scripts.generate_bulk_transactions
# writes backend/sample_data/transactions_bulk.csv (~10.5 MB)
```

Upload it like any other file, or run the dedicated (slow, not part of the
default suite) test:

```bash
docker compose exec backend pytest -m slow -v -s tests/test_ingestion_bulk_nfr.py
```

**Measured on this machine:** 100,000 rows (95,000 accepted, 5,000
quarantined) in **4.4s** end-to-end through the real `POST
/ingestion/transactions` endpoint (~5s calling the ingestion service directly
in the pytest run) — about 130x inside the 10-minute budget. No optimization
was needed: the transaction insert was already a single bulk statement (Core
`insert()` executed with a list of ~95k row dicts, which SQLAlchemy 2.0 batches
into a handful of multi-row `INSERT ... VALUES (...), (...), ...` round trips
via `insertmanyvalues`, not one `INSERT` per row) and quarantine rows go
through the same ORM bulk-insert batching. The only real fix this NFR
surfaced: the upload cap was 10 MB while a realistic 100k-row file is ~10.5
MB, which would have rejected a legitimate NFR-05-sized file — raised to 50 MB.

## Synthetic raw source dataset (TRD §6.1, §11)

`app/scripts/generate_raw_dataset.py` generates the **full banking source
schema** — eight CSV files plus a manifest, matching the source file contract
in TRD §6.1 — rather than the narrow transaction CSV the skeleton ingests. This
is DEP-01 from the FRD's 16-week backlog.

```bash
docker compose exec backend python -m app.scripts.generate_raw_dataset --profile small
```

| Profile | Scale | Use | Transactions |
|---|---|---|---|
| `tiny` | 1% | CI | ~4,000 |
| `small` | 5% | local development (default) | ~20,000 |
| `full` | 100% | integration and final demo | ~408,000 |

Output lands in `backend/sample_data/raw/<profile>/`:

| File | Contents |
|---|---|
| `customers.csv` | 20 columns: identity, KYC status, address, contact, onboarding |
| `business_customers.csv` | 16 columns: legal name, registration, industry code, turnover band |
| `beneficial_owners.csv` | ownership percentage and control type per business |
| `accounts.csv` | wallet / virtual account / settlement, status, balance snapshot |
| `merchants.csv` | MCC, declared volume and ticket bands, settlement account, outlets |
| `devices.csv` | device type, OS, app version, emulator/rooted flags |
| `transactions.csv` | 16 columns: amount, currency, channel, transaction type, counterparty, merchant, device, IP, source status |
| `watchlist.csv` | synthetic PEP / sanctions / internal list records |
| `manifest.json` | `source_system_code`, business date, contract version, per-file SHA-256 and record counts, `synthetic_declaration: true` |
| `labels.csv` | ground truth for every injected scenario |
| `data_dictionary.md`, `scenario_catalogue.md` | TRD §11.7 deliverables, rendered from the same specs the CSVs are written from |
| `generation_report.json`, `seeds.json` | counts per object, defects per type, labels per pattern, seed provenance |

### What makes it usable as evidence, not just volume

**Deterministic (TRD §11.0.1).** The same seed reproduces every file
byte-for-byte, checked by `test_same_seed_reproduces_every_file_byte_for_byte`
(this is T-GEN-01). Without it no detection metric is reproducible, which is
why the dataset is regenerated rather than committed.

**Provably synthetic while structurally correct.** National IDs are NIK-shaped
and encode gender the real way (a female's birth day is stored +40), so entity
resolution and masking work against realistic input — but they sit on province
prefix `99`, which is never issued, so a generated value cannot collide with a
real NIK. Registration numbers use the same prefix, phones a reserved `+62899`
block, IP addresses the RFC 5737 documentation ranges.

**Shaped like Indonesian wallet activity (TRD §11.0.3).** Payday clustering
around the 25th, a month-end tail, quieter weekends, arisan collection, agent
kiosks and remittance corridors.

**Labelled (TRD §11.3).** All twelve patterns P01–P12 get injected positives,
behavioural look-alikes, and exact-threshold boundary cases. Every one writes a
row to `labels.csv` with its entity, window and expected reason code, so recall
is measured rather than eyeballed.

**Deliberately imperfect (TRD §11.4).** Twelve defect types at controlled
rates: malformed dates, invalid currencies, zero/negative amounts, unresolvable
accounts, exact duplicates, idempotency conflicts, late arrivals, missing
counterparties and devices, missing beneficial owners, placeholder addresses,
truncated names. The quality pipeline has real work to do.

**Entity resolution population (TRD §11.5).** Five constructions, each stating
what resolution must do with it — must auto-merge, must not auto-merge, must
force `PENDING_REVIEW` with `IDENTIFIER_CONFLICT`, or must land in the
0.75–0.95 manual review band.

### Loading it into the skeleton

The skeleton models customers, accounts and transactions, and its ingestion
endpoint takes a narrower CSV, so a bridge script loads the master data and
projects the transactions onto that shape:

```bash
docker compose exec backend python -m app.scripts.load_raw_dataset --profile small
```

It prints exactly which columns it could not carry across (merchant, device,
IP, source status, business date, transaction type) — that gap **is** the
distance between the skeleton and the full pipeline, so it is reported rather
than hidden. Then upload the projected file from the Overview page, or:

```bash
curl -s -X POST http://localhost:8000/ingestion/transactions \
  -H "Authorization: Bearer <token>" \
  -F "file=@backend/sample_data/raw/small/transactions_app_format.csv" \
  -F "source_system=NDP_WALLET_CORE" -F "business_date=2026-09-30"
```

**Measured on the `small` profile:** 20,173 rows read, 19,751 accepted, 422
quarantined across every defect type; P02 detection then catches **2 of 2**
injected positives, fires on **0 of 2** labelled P02 look-alikes, and raises
**nothing** on the 42-entity control cohort (FRD §8.14). One further alert
emerges from ordinary background traffic, which is what TRD §11.1 expects —
alerts emerge, they are never generated directly.

> **Spec inconsistency, flagged not resolved:** TRD §11.1 describes "~600
> duplicate source records" for entity resolution, but the §11.5 table sums to
> 820. The generator follows §11.5 as the more specific of the two. Worth a
> mentor ruling.

## Browsing the data

| Endpoint | Roles | Notes |
|---|---|---|
| `GET /overview` | all | counts behind the landing-page tiles, in one call |
| `GET /transactions?search=&direction=&limit=&offset=` | all | `search` matches transaction ref, account number, customer ref or name |
| `GET /ingestion/quarantine?limit=&offset=` | all | rows that failed validation, with their original values and reason |

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
curl -s "http://localhost:8000/audit?correlation_id=<correlation_id>" -H "Authorization: Bearer <token>"
#  ALERT  NULL -> OPEN       (detection)
#  ALERT  OPEN -> ESCALATED  (triage)
#  CASE   NULL -> OPEN       (investigator)
```

`IN_PROGRESS`/`CLOSED` transitions and assignment aren't exposed yet — the
columns exist, the endpoints come with the investigation workflow.

## Audit trail (FR-1104, FR-1105)

Every material action writes an `audit_log` row inside the same database
transaction as the change itself, so a failed audit write rolls the change back
(TRD ADR-004). Four object types are audited today:

| `object_type` | Transitions written |
|---|---|
| `BATCH` | `NULL -> REGISTERED`, then `REGISTERED -> COMPLETED \| NEEDS_REVIEW \| FAILED` |
| `DETECTION_RUN` | `NULL -> COMPLETED` on every run — so "ran and found nothing" stays distinguishable from "never ran" |
| `ALERT` | `NULL -> OPEN` (detection), `OPEN -> DISPOSED \| ESCALATED` (triage) |
| `CASE` | `NULL -> OPEN` |
| `AUDIT_EXPORT` | `NULL -> EXPORTED`, recording the filters used |

| Endpoint | Roles | Notes |
|---|---|---|
| `GET /audit` | all | filters: `correlation_id`, `object_type`, `object_id`, `actor_email`, `date_from`, `date_to`, `limit`, `offset` |
| `GET /audit/export` | all | same filters, returns CSV (capped at 10,000 rows) |

Results are ordered oldest-first, so searching by `correlation_id` reads as the
chain in the order it happened (NFR-13). Exporting is itself an audited action
(FRD §4.1.10) — the export event records how many rows went out and under which
filters.

The **Audit trail** page in the UI does the same thing, and the correlation ID
on alert detail and case detail links straight into it filtered to that chain,
which is UAT-12 in one click.

> RBAC note: FRD §4.1.10 gives `ROLE_AUDITOR` read access to the audit trail.
> That role doesn't exist yet (4 of the FRD's 9 roles are built), so for now
> every authenticated role can read it. Narrowing this belongs with the
> outstanding RBAC work, not here.

## Frontend

http://localhost:5173 — React + TypeScript + Vite, TanStack Query for data,
react-router for pages. Functional, not polished.

| Route | Page | What it does |
|---|---|---|
| `/login` | Login | email + password; backend errors (wrong password, lockout) are shown as-is |
| `/` | Overview | landing page: summary tiles, the full transaction table (search + direction filter + paging), the quarantine list, and the data operations below |
| `/alerts` | Alert queue | table with status filter (Open / Escalated / Disposed / All), click a row for detail |
| `/alerts/:id` | Alert detail | reason, correlation id, evidence transactions, detection factors; triage form and "open case" button depending on role and status |
| `/cases/:id` | Case detail | case header (number, status, opened by) and the linked alerts |
| `/audit` | Audit trail | filter by correlation ID / object type / object ID / actor, and export the result as CSV |

The whole workflow runs from the UI — **no Swagger needed**. Overview carries the
two data operations, role-gated like everything else: **Upload CSV** (ROLE_DATA_OPS)
and **Run detection** (ROLE_DATA_OPS, ROLE_ANALYST). Both report what happened
(`22 accepted, 7 quarantined`, `2 alerts created`) and refresh the tiles in place.
Other roles see the same data read-only.

Role-aware UI: the disposition form only renders for ROLE_TRIAGE on `OPEN`
alerts (submit stays disabled until the reason has 20 characters); the "Open
case" button only for ROLE_TRIAGE / ROLE_INVESTIGATOR on `ESCALATED` alerts
without a case. Other roles get the same pages read-only. The backend enforces
the same rules, the UI just doesn't offer what would be rejected.

Session handling (`src/api/client.ts`, `src/context/AuthContext.tsx`): the
access token lives only in memory — nothing is written to `localStorage` or
`sessionStorage`. On first load the app calls `/auth/refresh`; if the browser
still holds a valid refresh cookie it gets a fresh access token and renders
the protected pages, otherwise it shows the login page. Every API call carries
the access token; on a `401` the client refreshes once (shared across
concurrent requests) and retries, and if that fails the session is cleared and
the user lands on `/login`. Reloading the page keeps you signed in without
re-entering credentials.

Try it end to end without leaving the browser: seed the users and master data
(`python -m app.scripts.seed`), sign in as `dataops@fcip.internal` → upload
`backend/sample_data/transactions_sample.csv` from Overview → **Run detection**
→ switch to `triage@fcip.internal` → open the CUST-006 alert → escalate with a
reason → "Open case from this alert". Sign in as `analyst@fcip.internal` to see
the read-only view. Screenshots of that flow are in `docs/screenshots/`.

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
- **No CSRF token on `/auth/*`.** `SameSite=Strict` on the refresh cookie
  already blocks cross-site requests from carrying it; a dedicated CSRF token
  isn't added on top for the skeleton.
- **Frontend has no automated tests in the repo.** It was verified with a
  scripted Playwright click-through (login, queue, disposition, case, role
  restrictions, token refresh); wiring that into CI is a follow-up.
- **CSV ingestion covers transactions only.** Customer and account records
  are pre-seeded reference data (see `backend/app/scripts/seed.py`), not part
  of the ingestion pipeline.
- **A batch and an alert are separate audit chains.** FRD UAT-12 asks for "the
  whole chain from batch to report" under one correlation ID, but an alert's
  evidence window can span several batches, so a single chain from batch to
  report isn't well defined. For now a batch chains its own lifecycle and an
  alert chains detection → disposition → case; the batch link is reachable
  through `transaction.ingestion_batch_id` on the alert's evidence rows. **This
  needs a mentor/design decision, not a silent default.**
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
