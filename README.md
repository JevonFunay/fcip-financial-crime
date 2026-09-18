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

## Auth

Login/refresh/logout is JWT-based, with rotating refresh tokens tracked
server-side in the `sessions` table (TRD §12.2) so they can be revoked and
audited — not stateless JWTs.

Seed one test user per role (idempotent, safe to re-run):

```bash
docker compose exec backend python -m app.scripts.seed
```

This creates `dataops@fcip.internal`, `analyst@fcip.internal`,
`triage@fcip.internal`, `investigator@fcip.internal`, all with password
`DevPassword123!` (dev-only, never use in production).

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
is a FastAPI dependency that later endpoints (alerts, cases, ingestion) will
use to restrict access per the persona/permission matrix from FRD §4.1.

## Known simplifications (skeleton stage)

These are deliberate shortcuts for the walking-skeleton stage, called out
here so they're easy to explain and are tracked for follow-up:

- **`customer_id` stands in for `entity_id`.** FRD §8.2's P02 structuring
  pattern aggregates per *entity*, but entity resolution isn't built yet, so
  the detection job (see `backend/app/services/detection/p02_structuring.py`,
  added in a later stage) aggregates by `customer_id` instead. Each customer
  is treated as its own entity for now.
- **CSV ingestion covers transactions only.** Customer and account records
  are pre-seeded reference data (see `backend/app/scripts/seed.py`, added in
  a later stage), not part of the ingestion pipeline.
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
