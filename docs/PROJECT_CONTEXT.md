# FCIP — Project Context

**Read this first.** It is the complete working context for the Financial Crime
Intelligence Platform capstone: what the project is, what the spec demands, what
is actually built, what is deliberately not built, and what is still undecided.

Last updated: **23 September 2026** · Week 4 of 16 · ~17% of MVP scope

---

## 1. What this is

A **Financial Crime Intelligence Platform (FCIP)** — an AML/transaction-monitoring
system built as a university capstone (UKDW × V-TEKI). The fictional client is
"PT Nusantara Digital Payment", an Indonesian digital wallet. **All data is
synthetic.** Nothing in this project touches real people, real lists or real
money.

The platform is **advisory only**. Four hard stop-lines run through the entire
spec and must never be crossed:

| Stage | Stops at |
|---|---|
| Detection | an **alert** (a hypothesis), never an action on a transaction |
| Screening | a **potential match**, never a confirmed match — only a human confirms |
| AI | a **draft**, never usable output before a human approves |
| Reporting | an **internal document**, never a transmission path to a regulator |

### Who is building it

Nominally a team of three, split into Group A (detection engine) and Group B
(investigation UI). **In practice one person — Jevon — is building the entire
stack solo**: backend, database, and frontend. See §8 for why this matters.

---

## 2. The specification

Two Indonesian-language PDFs in `docs/`, both still **v0.9 DRAFT**:

- `FRD_ID_Financial Crime Intelligence Platform.docx.pdf` — 167 pages
- `TRD_ID_Financial Crime Intelligence Platform.docx.pdf` — 116 pages

The TRD states it "sits under FRD v1.0". **The FRD has not been frozen to v1.0
yet** (it needs mentor sign-off, see §8), so the build is technically running
against a draft.

### Total scope — the denominator for any progress figure

| Source | Scope |
|---|---|
| FRD §6 | **120 functional requirements** — 84 Must / 29 Should / 7 Could, across 15 domains D1–D15 |
| FRD §8 | **12 detection patterns** P01–P12 |
| FRD §14.1 | **18 business NFRs** |
| FRD §4.1 | **11 personas**, 9 `ROLE_*` values |
| FRD §14.4 | **15 UAT scenarios** |
| FRD §14.3 | **16-week backlog, 6 integration gates** |
| TRD §4 | **30 components** C-01…C-30 |
| TRD §18.6 | **~90 tables** across 6 schemas (`raw`/`core`/`detect`/`invest`/`gov`/`meta`) |
| TRD §18.7 | **80 endpoints** — A1–A36, B1–B35, S1–S9 |
| TRD §1.3 | 12 ADRs |
| TRD §17.3 | 11 A↔B interfaces I-01…I-11, frozen at the Week-4 gate |

The PDFs have no text layer tooling installed. To extract them again:
`python3 -m venv venv && venv/bin/pip install pypdf`, then read page by page.

### Domain map (FRD §6)

| Domain | Owner in spec | Subject |
|---|---|---|
| D1 | A | Ingestion and data quality |
| D2 | A | Entity resolution |
| D3 | A | Customer and merchant risk scoring |
| D4 | A | Transaction monitoring and AML rules engine |
| D5 | A | Anomaly signals and graph |
| D6 | A engine / B UI | Watchlist / sanctions / PEP screening |
| D7 | B | Alert management and triage |
| D8 | B | Entity 360, timeline, graph investigation |
| D9 | B | Case management |
| D10 | B | Evidence and maker-checker decisions |
| D11 | B | AI narrative assistance |
| D12 | B | Regulator reporting support |
| D13 | A | Rule and model performance monitoring |
| D14 | B | Dashboards and reporting |
| D15 | Joint | Governance, RBAC, audit, administration |

---

## 3. Current progress: ~17%

Roughly **10%** counting functional requirements alone; **~17%** weighting the
foundation work already done. Neither document assigns effort weights, so the
percentage is an estimate — the counts underneath it are not.

| Dimension | Built | % |
|---|---|---|
| FR (full-equivalent) | ~16 of 120 | 13% |
| FR priority **Must** only | ~15 of 84 | 18% |
| Detection patterns | 1 of 12 (P02) | 8% |
| Database tables | 12 of ~90 | 13% |
| API endpoints | 17 of 80 | 21% |
| RBAC roles | 4 of 9 | 44% |
| NFRs proven by measurement | 3 of 18 | 17% |
| TRD components | ~6 full + ~5 partial of 30 | ~25% |

**226 backend tests pass** (2 excluded as `slow`). Frontend has no automated
tests in the repo; it was verified with a scripted Playwright click-through.

### Per domain

| Domain | State |
|---|---|
| D1 Ingestion (10 FR) | **~40%** — batch registration, checksum, validation, quarantine, processing log, reconciliation all work. Missing: DQ metrics, reprocessing, late-arrival handling, source configuration |
| D2 Entity Resolution (6) | **0%** — `customer_id` stands in for `entity_id` |
| D3 Risk Scoring (7) | **0%** |
| D4 Rules Engine + Detection (12) | **~22%** — FR-307/308 solid. Rules are hardcoded Python: no `rule`/`rule_version` table, no simulation, no maker-checker, no priority scoring |
| D5 Anomaly + Graph (6) | **0%** |
| D6 Screening (8) | **0%** |
| D7 Alert & Triage (12) | **~35%** — strongest domain. Queue, detail, disposition, escalate, case linking. Missing: assignment, SLA, bulk, reopen, export |
| D8 Entity 360 / Timeline / Graph (9) | **~2%** |
| D9 Case Management (8) | **~14%** — create from alert, detail. No lifecycle transitions, no notes, no queue |
| D10 Evidence + Maker-Checker (8) | **0%** |
| D11 AI Narrative (6) | **0%** |
| D12 Regulator Reporting (6) | **0%** |
| D13 Rule/Model Monitoring (6) | **0%** |
| D14 Dashboards (6) | **~3%** — `/overview` is count tiles, not FR-1011 dashboards |
| D15 Governance/RBAC/Audit (10) | **~40%** — auth strong, audit search/export done. Missing: user management, config versioning, SoD reporting |

### Against the 16-week backlog (FRD §14.3)

The Group B side is around **Week 6–7** (alert queue, lifecycle, case creation).
The Group A side is around **Week 3–4** (ingestion done; no entity resolution,
no risk scoring, no rules engine). **GATE 2** (Week 7 — "first end-to-end alert
appears in the queue from a real detection run") is already demonstrable.
**GATE 3** (Week 10 — full chain through to a *decision*) is not: maker-checker
and evidence are at zero.

---

## 4. Stack and layout

- **Backend**: FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, Python 3.11
- **Frontend**: React + TypeScript + Vite, TanStack Query, react-router
- **Database**: PostgreSQL 16
- **Auth**: JWT access token + rotating refresh token, argon2 hashing
- **Packaging**: Docker Compose
- **Testing**: pytest

```
backend/
  app/
    core/          security.py, rbac.py
    models/        12 SQLAlchemy models
    routers/       alerts, audit, auth, cases, detection, ingestion, overview, transactions
    schemas/       Pydantic request/response models
    services/      ingestion.py, audit.py, detection/p02_structuring.py
    scripts/       seed.py, run_detection.py, generate_bulk_transactions.py,
                   generate_raw_dataset.py, raw_contract.py, load_raw_dataset.py
  alembic/versions/  0001 … 0004
  tests/             14 test modules, 226 passing
frontend/src/
  api/           client.ts (token refresh), alerts, cases, data, audit, auth
  pages/         Login, Overview, AlertQueue, AlertDetail, CaseDetail, Audit
  components/    Layout, ProtectedRoute, StatusBadge
docs/            FRD + TRD PDFs, screenshots/, PROJECT_CONTEXT.md
```

### Deployment deviation (needs ratification)

TRD §5.1 specifies **11 services**: nginx, web, api-detection, api-investigation,
api-iam, worker (RQ), scheduler, postgres, redis, minio, backup job.
`docker-compose.yml` has **3**: db, backend, frontend.

Consequences: without MinIO, FR-811/813 (content-addressed evidence storage)
cannot be built as designed; without worker/scheduler, detection runs are
permanently manual (FR-306). This is a reasonable solo-build decision but it is
**not yet recorded as an ADR**.

---

## 5. What is actually built

### Auth and RBAC (FR-1101, FR-1103 partial)
- argon2id password hashing; JWT access token, 15 min, **in memory only** — never `localStorage`
- Refresh token is an `HttpOnly; Secure; SameSite=Strict; Path=/auth` cookie named `fcip_refresh_token`, rotated on every use, backed by a server-side `sessions` table (hash only) so it can be revoked
- Idle timeout 30 min, absolute session 8h, lockout after 5 failed logins for 15 min
- `require_role(...)` FastAPI dependency per endpoint
- 4 of 9 roles exist: `ROLE_DATA_OPS`, `ROLE_ANALYST`, `ROLE_TRIAGE`, `ROLE_INVESTIGATOR`. Missing: `ROLE_ADMIN`, `ROLE_SENIOR_INVESTIGATOR`, `ROLE_MLRO`, `ROLE_AUDITOR`, `ROLE_MANAGEMENT`
- No central `authorize(principal, action, object)` function, no deny-by-default startup assertion

### Ingestion (FR-101, FR-102, FR-105)
- `POST /ingestion/transactions` (ROLE_DATA_OPS) registers an **ingestion batch** first: `BAT-000001` from a sequence, SHA-256 checksum, `source_system`, `business_date`, optional `expected_records`
- The same file cannot be registered twice for the same **(source system, business date)** → `409`. Same file on a different business date is allowed — that is a genuine re-send, and its rows quarantine individually as "already exists"
- Batch status: `REGISTERED` → `COMPLETED` / `NEEDS_REVIEW` (reconciliation mismatch, TRD C-02) / `FAILED` (structural failure, zero rows loaded, TRD C-01)
- The batch row is committed **before** parsing, so a structurally failed batch stays visible as `FAILED`
- `processing_log` records each stage in order, with an identity `seq` column because Postgres `now()` is transaction-start time and would otherwise make ordering arbitrary
- Valid rows → `transaction`; invalid rows → `quarantine_item` with original values and reason. Never silently dropped
- `GET /ingestion/batches`, `GET /ingestion/batches/{id}` (with full processing log)

### Detection: P02 Structuring (FR-307, FR-308)
`backend/app/services/detection/p02_structuring.py`. Parameters in `P02Parameters`:

| Parameter | Value |
|---|---|
| `REPORTING_THRESHOLD` | IDR 500,000,000 |
| `BAND_LOWER` | 70% → IDR 350,000,000 (inclusive) |
| `MIN_COUNT` | 3 in-band transactions |
| `AGGREGATE_MULTIPLE` | 1.0× → window total ≥ IDR 500,000,000 |
| Window | 7 days rolling, non-overlapping, end exclusive |

Aggregated per entity across **all** accounts and channels, both directions.
IDR only (no FX conversion). Each alert stores the FRD §8.2 factors in
`detection_details` plus exact `window_start`/`window_end` and the applied
parameters, for FR-307 reproducibility. Re-running is idempotent.

### Alerts and cases (FR-601, FR-602, FR-604, FR-605, FR-608, FR-801)
- `GET /alerts?status=`, `GET /alerts/{id}`, `POST /alerts/{id}/disposition` (ROLE_TRIAGE)
- Disposition reason is mandatory, minimum 20 characters after trimming, else `422`. Only `OPEN` alerts can be dispositioned; a second attempt is `409`
- `POST /cases` (ROLE_TRIAGE, ROLE_INVESTIGATOR) — alert must be `ESCALATED` and unlinked. Case numbers `CASE-000001` from a sequence
- Alert states: only `OPEN` / `DISPOSED` / `ESCALATED` (TRD §17.2 specifies 10). Case states: only `OPEN` / `IN_PROGRESS` / `CLOSED` (spec has 7), and only `OPEN` is reachable

### Audit (FR-1104, FR-1105)
Every material action writes `audit_log` **inside the same transaction** as the
change, so a failed audit write rolls the change back (TRD ADR-004).

| `object_type` | Transitions |
|---|---|
| `BATCH` | `NULL→REGISTERED`, `REGISTERED→COMPLETED\|NEEDS_REVIEW\|FAILED` |
| `DETECTION_RUN` | `NULL→COMPLETED` on every run — "ran and found nothing" stays distinguishable from "never ran" |
| `ALERT` | `NULL→OPEN`, `OPEN→DISPOSED\|ESCALATED` |
| `CASE` | `NULL→OPEN` |
| `AUDIT_EXPORT` | `NULL→EXPORTED`, recording the filters used |

`GET /audit` filters by `correlation_id`, `object_type`, `object_id`,
`actor_email`, `date_from`, `date_to`. Ordered oldest-first, so a
`correlation_id` search reads as the chain in order (NFR-13).
`GET /audit/export` returns CSV and is itself audited (FRD §4.1.10).

### Synthetic raw dataset (DEP-01 — TRD §6.1, §11)
`app/scripts/generate_raw_dataset.py` generates the **full banking source
schema**: 8 CSVs + manifest, not the narrow CSV the skeleton ingests.

Files: `customers.csv` (20 cols), `business_customers.csv` (16), `beneficial_owners.csv`,
`accounts.csv`, `merchants.csv` (MCC, expected bands), `devices.csv`,
`transactions.csv` (16 cols), `watchlist.csv`, plus `manifest.json`
(per-file SHA-256, `synthetic_declaration: true`), `labels.csv`,
`data_dictionary.md`, `scenario_catalogue.md`, `generation_report.json`, `seeds.json`.

Profiles: `tiny` 1% (CI), `small` 5% (default), `full` 100% (~408k transactions in 6s).
All volumes land inside TRD §11.1 targets.

Key properties:
- **Deterministic** — same seed reproduces every file byte-for-byte (T-GEN-01)
- **Provably synthetic but structurally correct** — NIK-shaped IDs encoding gender the real way (female birth day +40), on province prefix `99` which is never issued; `+62899` phone block; RFC 5737 IP ranges
- **Labelled** — all 12 patterns get injected positives, behavioural look-alikes and exact-threshold boundary cases, each writing to `labels.csv`
- **Deliberately defective** — 12 defect types at controlled rates (TRD §11.4)
- **ER population** — 5 constructions per TRD §11.5, each stating what resolution must do

Measured on `small`: 20,173 rows read, 19,751 accepted, 422 quarantined across
every defect type. P02 then catches **2/2** injected positives, fires on **0/2**
labelled look-alikes, and raises **nothing** on the 42-entity control cohort
(FRD §8.14).

`app/scripts/load_raw_dataset.py` bridges it into the skeleton and **prints
which columns it could not carry across** (merchant, device, IP, source status,
business date, transaction type) — that gap is the distance to the full pipeline.

### Frontend
`/login`, `/` (Overview: tiles, transaction table with search/filter/paging,
quarantine list, ingestion batch list, upload + run-detection controls),
`/alerts`, `/alerts/:id`, `/cases/:id`, `/audit`.

Role-aware: the disposition form renders only for ROLE_TRIAGE on `OPEN` alerts;
"Open case" only for ROLE_TRIAGE/ROLE_INVESTIGATOR on `ESCALATED` alerts. The
backend enforces the same rules — the UI just doesn't offer what would be
rejected. Correlation IDs on alert and case detail deep-link into the audit
trail, which makes UAT-12 one click.

---

## 6. NFRs proven by measurement

| NFR | Requirement | Status |
|---|---|---|
| NFR-05 | Ingest 100,000 records within 10 minutes including validation | **4.4 seconds** end-to-end through the real endpoint — ~130× inside budget. Upload cap was raised 10 MB → 50 MB because a realistic 100k file is ~10.5 MB |
| NFR-14 | Every alert explainable from alert detail alone | Satisfied for P02 |
| NFR-15 | No real personal data, lists or credentials anywhere | Satisfied |

The other 15 are untested.

---

## 7. Known simplifications (deliberate, all documented in README)

1. **`customer_id` stands in for `entity_id`.** FRD §8.2 aggregates per *entity*; entity resolution isn't built. Each customer is its own entity. One grouping key to swap later
2. **Rules are code, not data.** TRD §9 / ADR-006 require versioned SQL templates with an immutable `rule_version` table. P02 is hardcoded Python
3. **No scheduler.** Detection is triggered manually
4. **No CSRF token on `/auth/*`.** `SameSite=Strict` already blocks cross-site carriage of the refresh cookie
5. **No frontend tests in the repo**
6. **CSV ingestion covers transactions only.** Customers and accounts are seeded reference data
7. **Naive timestamps read as UTC**
8. **Batch and alert are separate audit chains.** UAT-12 asks for one correlation ID from batch to report, but an alert's evidence window can span several batches, so a single chain isn't well defined. Linked via `transaction.ingestion_batch_id`. **Needs a decision**
9. **All roles can read the audit trail** because `ROLE_AUDITOR` doesn't exist yet

---

## 8. Open questions — the most important section

### FRD §1.7 — MQ-01 to MQ-10, all still unanswered

These were due **Week 3** (DEP-11). The FRD cannot be frozen to v1.0 without
them, and the TRD sits under FRD v1.0. Each has a proposed default in the
document; several are **already implemented as their default without mentor
ratification**.

| ID | Question | Proposed default | Status in code |
|---|---|---|---|
| MQ-01 | Large-transaction thresholds: IDR 100M individual / 500M merchant? | adopt, make configurable | not built |
| MQ-02 | Structuring: fixed reporting threshold or pure behavioural clustering? | fixed threshold + behavioural band | **implemented as the default** |
| MQ-03 | Customer/merchant-facing views in scope? | out of scope | not built |
| MQ-04 | Two-eyes or four-eyes for `DRAFT_REPORT_RECOMMENDED`? | two-eyes, MLRO mandatory checker | not built |
| MQ-05 | Is AI narrative required or stretch? | required but degradable to template | not built |
| MQ-06 | Native graph database or relational projection? | relational first | not built |
| MQ-07 | Audit retention posture? | keep for project, document 5-year production stance | partial |
| MQ-08 | Specific Indonesian report template (LTKM-like) or generic? | generic fictional, clearly labelled | not built |
| MQ-09 | Is automatic alert closure ever allowed? | exact-duplicate suppression only, fully audited | **affects FR-310 design** |
| MQ-10 | A↔B integration: in-process calls or internal HTTP API? | internal HTTP with versioned contract | monolith built instead |

**MQ-02 is the most urgent.** If the mentor picks the other option, the P02
detector is redesigned.

### TRD §1.5 — conflicts still open

- **CF-01** (tied to MQ-09): boundary between forbidden autonomous closure and permitted exact-duplicate suppression
- **CF-05**: anomaly score must not raise an alert alone, but there is no anomaly rule among the twelve patterns. Proposed: anomaly stays a supporting signal

### Spec inconsistency found while building

TRD **§11.1** says "~600 duplicate source records" for entity resolution; the
**§11.5** table sums to **820** (200+150+180+40+250). The generator follows
§11.5 as the more specific. Flagged, not silently resolved.

### The structural question the documents don't cover

The mentor's Week-3 instruction was that **Group A builds the anomaly model and
Group B builds the dashboard** deciding what happens to an anomalous
transaction. **That split never happened — each group builds both engine and
UI.** This means FRD §6's A/B "Pemilik" column no longer describes reality, and
with it:

- FRD §13 and TRD §17 (the Week-4 integration contract, 13 sign-off items)
- 11 interfaces I-01…I-11 and bidirectional contract tests
- GATE 1, and dependencies DEP-03 through DEP-10
- the "Pemilik" column of the traceability matrix (FRD §14.2)

all have no counterparty. **This needs a mentor ruling, and it is due now** —
GATE 1 falls in Week 4.

Related: FRD §14.6 item **C2** asks the mentor to tick "the Must set is
achievable". It has never been filled in. 84 Must requirements were scoped
across *two* groups; one group doing all 15 domains doubles the load.

---

## 9. Confirmed rules (do not re-derive these)

- **P02 parameters** — as in §5 above. Confirmed by the user 2026-09-18, **not yet ratified by the mentor** (MQ-02)
- **Refresh token transport** — MUST be an HttpOnly cookie. An earlier draft used `localStorage` and was rejected as a TRD §12.2 violation
- **`audit_log.correlation_id`** — one shared ID chains detection run → alert → disposition → case
- **Disposition reason** — mandatory, ≥ 20 characters
- **Permission matrix** — verified against FRD §4.1: ROLE_DATA_OPS ingestion/detection; ROLE_TRIAGE disposition; ROLE_TRIAGE + ROLE_INVESTIGATOR case creation; all four roles read
- **Ingestion scope** — transactions only; customer/account are seeded master data

---

## 10. Running it

```bash
cp .env.example .env
docker compose up --build
# backend :8000 (/docs, /health) · frontend :5173 · postgres :5432
```

The backend container runs `alembic upgrade head` before starting.

```bash
# dev users, one per role — password DevPassword123! (dev only)
docker compose exec backend python -m app.scripts.seed
#   dataops@ / analyst@ / triage@ / investigator@fcip.internal

docker compose exec backend pytest              # 226 tests
docker compose exec backend pytest -m slow      # NFR-05 + full-profile volume

# generate the full banking source dataset
docker compose exec backend python -m app.scripts.generate_raw_dataset --profile small
docker compose exec backend python -m app.scripts.load_raw_dataset --profile small
```

Tests run against a separate `<db>_test` database, truncated before every test.
The suite refuses to run if the target database name doesn't end in `_test`.

**Note:** when editing a migration that has already been applied, drop the test
database so conftest rebuilds it: `docker compose exec db psql -U fcip -d
postgres -c 'DROP DATABASE IF EXISTS fcip_test;'`

### Demo path (GATE 2, ~5 minutes)

Sign in as `dataops@` → upload `sample_data/transactions_sample.csv` from
Overview → **Run detection** → switch to `triage@` → open the CUST-006 alert →
escalate with a reason → **Open case from this alert** → click the correlation
ID to see the full chain in the audit trail.

Expected on the sample file: 29 rows, 22 accepted, 7 quarantined, then exactly
2 alerts.

---

## 11. What to build next

Ordered by what unblocks the most, and by what does *not* depend on unanswered
questions.

**Safe to build now** (independent of MQ-01…MQ-10):
1. **Rule as data** — `rule` / `rule_version` tables, versioned parameters, simulation (FR-301…FR-303). The largest pending refactor, and the remaining 11 patterns will inherit whatever shape is chosen. Doing this before adding patterns is much cheaper than after
2. **Case lifecycle** (FR-804) and case queue (FR-808) — columns exist, endpoints don't
3. **Alert assignment** (FR-603) and SLA tracking (FR-607)
4. **DQ metrics** (FR-108) — the generator already produces the defects to measure

**Blocked on a mentor decision:**
- Maker-checker (needs MQ-04, plus 5 unbuilt roles) — prerequisite for GATE 3 and UAT-03/09/10/11
- Patterns P01, P03–P12 (P01 needs MQ-01; the rest need the rule-as-data decision)
- AI narrative (MQ-05), graph (MQ-06), screening review flow

**Structural debt to record, not necessarily fix:**
- ADR for the 3-service topology vs TRD §5.1's 11
- Alert/case state vocabularies are far narrower than TRD §17.2

---

## 12. Working conventions

- Reference the FRD/TRD **by section number**. If a number isn't verified from the PDF, say so explicitly rather than defaulting silently — this has burned the project before
- Staged review gates: each build stage is confirmed before the next begins
- **Never `git push`** — commits are local; Jevon pushes to GitHub himself
- A requirement without a test is not done (FRD §6 convention)
- Close each working session with a paste-ready Indonesian summary for the capstone report
