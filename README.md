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

## Known simplifications (skeleton stage)

These are deliberate shortcuts for the walking-skeleton stage, called out
here so they're easy to explain and are tracked for follow-up:

- **`customer_id` stands in for `entity_id`.** FRD §8.2's P02 structuring
  pattern aggregates per *entity*, but entity resolution isn't built yet, so
  the detection job (see `backend/app/services/detection/p02_structuring.py`,
  added in a later stage) aggregates by `customer_id` instead. Each customer
  is treated as its own entity for now.
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
