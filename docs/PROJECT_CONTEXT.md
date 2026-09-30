# FCIP — Project Context

**Read this first.** It is the complete working context for the Financial Crime
Intelligence Platform capstone: what the project is, what the spec demands, what
is actually built, what is deliberately not built, and what is still undecided.

Last updated: **29 September 2026** · Week 4 of 16 · ~17% of MVP scope

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
| Database tables | 15 of ~90 | 17% |
| API endpoints | 17 of 80 | 21% |
| RBAC roles | 4 of 9 | 44% |
| NFRs proven by measurement | 3 of 18 | 17% |
| TRD components | ~6 full + ~5 partial of 30 | ~25% |

**347 backend tests pass** (5 more marked `slow`). Frontend has no automated
tests in the repo; it was verified with a scripted Playwright click-through.

### Per domain

| Domain | State |
|---|---|
| D1 Ingestion (10 FR) | **~40%** — batch registration, checksum, validation, quarantine, processing log, reconciliation all work. Missing: DQ metrics, reprocessing, late-arrival handling, source configuration |
| D2 Entity Resolution (6) | **0%** — `customer_id` stands in for `entity_id` |
| D3 Risk Scoring (7) | **0%** |
| D4 Rules Engine + Detection (12) | **~26%** — FR-307/308 solid. Rules are data: `rule`/`rule_version`/`simulation_result` tables with DB-enforced immutability, P02 is RUL-0001 v1 and every alert names its version (FR-301/302 partial). No rule endpoints, no simulation yet (deferred, §11), no maker-checker, no priority scoring |
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
    models/        15 SQLAlchemy models
    routers/       alerts, audit, auth, cases, detection, ingestion, overview, transactions
    schemas/       Pydantic request/response models
    ml/            feature_set.py, loader.py, features.py, build_features.py, reference.py (fs_v2)
    reference/     geo_list_v1.csv (the organisation's configured list)
    services/      ingestion.py, audit.py, detection/p02_structuring.py,
                   detection/active_rules.py
    scripts/       seed.py, run_detection.py, generate_bulk_transactions.py,
                   generate_raw_dataset.py, raw_contract.py, load_raw_dataset.py,
                   audit_scenario_artefacts.py
  alembic/versions/  0001 … 0006
  tests/             18 test modules, 347 passing
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
Logic in `backend/app/services/detection/p02_structuring.py`. Parameters are
**data** since 29 Sep: the ACTIVE version of rule `RUL-0001`, seeded by
migration 0006 from the former `P02Parameters` constants (v1 holds exactly
these values). Every alert references its rule version. The seed was written
straight as ACTIVE, **bypassing maker-checker** until FR-304 and `ROLE_MLRO`
exist; the version's `change_summary` and an audit event
(`RULE_SEEDED_ACTIVE`) say so.

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
`label_transactions.csv` (per-row ground truth, since 1.2.0),
`data_dictionary.md`, `scenario_catalogue.md`, `generation_report.json`, `seeds.json`.

Profiles: `tiny` 1% (CI), `small` 5% (default), `full` 100% (~421k transactions in 6s,
TRD §11.1 target 420k). All volumes land inside TRD §11.1 targets. Generator version
**1.4.0** (TRD §11.7: bumped whenever the same seed would produce different bytes).

**1.2.0 (30 Sep) removed the scenario artefacts** (§8): an automatic audit
(`audit_scenario_artefacts.py`) compares every raw column between scenario and
background rows. It also added the **ATO** scenario (60 + 30 look-alikes at
full), ordinary phone changes, a device of its own for every party with ~120
shared (TRD §11.1), and `label_transactions.csv`. **1.3.0 (30 Sep)** gave every
party the counterparties TRD §11.2 describes (a small stable set for retail, a
broad payer base for business) instead of a random one per row: a first-time
counterparty is 25% of individual and 30% of business payments, 100% of ATO's.
**1.4.0 (30 Sep)** makes the population behave as TRD §11.2 describes it, every
statement measured before and after (table in §8): heavy-tailed activity within
the same total, coherent top-ups and bills, weekly merchant settlement, tickets
consistent with the MCC, look-alikes that resemble their pattern, and boundary
cases on each pattern's own threshold. It is the last generator change before
training. Audit: 1.1.0 has 37–38 flags, 1.3.0 and 1.4.0 have 0 on five seeds
(the audit now also stratifies by entity activity and judges the
counterparty-new view within transaction type and one-sided, see README). ML
datasets: training = full, seed 20260923 (421,282 transactions, 754 positive
entities); test = full, seed 20261001 (421,456, 754); not committed, commands in
the README.

**Custom scale:** `--target-transactions N` (min 20,000) derives every volume
from N by the full profile's ratio, calibrates the row count to exactly N using
unlabelled parties only, and floors each pattern at 5 scenarios, recording all
three in the report's `scale` block. At 100,000: 1.4 s, exactly 100,000 rows,
only the 12 boundary cases floored (1 → 5). Presets are byte-for-byte unaffected.

Key properties:
- **Deterministic** — same seed reproduces every file byte-for-byte (T-GEN-01)
- **Provably synthetic but structurally correct** — NIK-shaped IDs encoding gender the real way (female birth day +40), on province prefix `99` which is never issued; `+62899` phone block; RFC 5737 IP ranges
- **Population mix held exactly** (TRD §11.2) — retail 62 / business 18 / control 8 / edge 8 / injected 4, as exact counts at every scale; individual sole traders fill the business cohort beyond the ~11% that §11.1's real businesses supply
- **Labelled** — all 12 patterns and ATO get injected positives and behavioural look-alikes (8 patterns also boundary cases on their own threshold), each writing to `labels.csv`, and every row they emit to `label_transactions.csv`. Each entity carries at most one label, and the control cohort is never touched
- **Deliberately defective** — 12 defect types at controlled rates (TRD §11.4)
- **ER population** — 5 constructions per TRD §11.5, each stating what resolution must do

Measured on the shared `Small` (1.4.0) through the real API: 20,916 rows read,
20,458 accepted, 458 quarantined across every defect type. P02 raises exactly
2 alerts: **2/2** injected positives, **0/2** labelled look-alikes, **nothing**
on the 42-entity control cohort (FRD §8.14). On `full` (1.4.0), checked offline
on both ML seeds: 45/45 positives, 0/30 look-alikes, 0/830 control, plus 14/18
cross-pattern hits on entities labelled P07/P08, whose values fall inside P02's
band, and no unlabelled hit.

### ML feature library, fs_v2 (stage 1b and fs_v2, 30 Sep)
`backend/app/ml/`: `feature_set.py` (registry, locked parameters with their
source, rendered to `docs/ml/feature_set_fs_v2.md`), `loader.py`, `features.py`,
`build_features.py`, `reference.py`. **fs_v2** differs from fs_v1 in one
feature: P10's ticket is divided by its MCC's reference ticket
(`TICKET_MULTIPLE_R30D`, FRD §8.10's factor name), the training data's median
per MCC (AS-04), instead of the declared band's midpoint. On the training seed
it removes every ordinary business-week over 3x (5.9% under fs_v1) and every
background P10 fire (9), with P10 positives and look-alikes unchanged. Two units: **AML** one row per (entity, calendar week the
entity transacted), **Fraud** one row per transaction, as of that transaction.
`aml_features(history, as_of, context)` and `fraud_features(history, i,
context)` work for a single entity, which is what live monitoring will call.

- **No leakage, tested**: the loader reads only allow-listed files and
  columns; deleting every ground-truth file, smuggling a scenario column into
  the transactions, or renaming every identifier leaves every feature value
  unchanged; no feature reads the future (and the test is shown to catch a
  feature that does)
- **Parity**: the loader applies ingestion's own validation, so the features
  describe exactly the rows the upload endpoint stores (20,458 on Small, tested
  against the real ingestion service); one entity built alone gets exactly its
  batch features
- **Short history is explicit**: a feature comparing with the entity's own past
  is NaN below the FRD threshold that owns it (P01 20 transactions, P04 30
  days, P07 6 weeks, FR-401 30 transactions over 30 days); novelty features
  (new device, counterparty, merchant) are NaN under 30 days of history
  (`min_history_days`)
- Full training profile (1.4.0): 149,115 AML rows and 412,015 Fraud rows in ~43 s
  (1.3.0: 197,903 and 396,788; fewer entity-weeks because quiet entities now
  skip most weeks)

`app/scripts/load_raw_dataset.py` bridges it into the skeleton and **prints
which columns it could not carry across** (merchant, device, IP, source status,
business date, transaction type) — that gap is the distance to the full pipeline.
It checks the manifest first (TRD §6.1): refused unless declared synthetic
(FR-501) and every file matches its SHA-256.

**Shared datasets:** `backend/sample_data/Tiny` and `Small` are committed so
the team loads the same data without running the generator (`--shared small`).
A test requires them to match the current generator byte for byte — **change
the generator, regenerate them in the same commit**. `Full` is not committed
(~85 MB of history per change); generate it on demand with `--profile full`.

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

### Our own assumptions — not from the FRD/TRD, awaiting ratification

Where the spec is silent, the build still had to pick something. These are
**our choices, not spec values**, tracked here the same way as MQ-01…MQ-10
until the mentor ratifies or replaces them. The `AS-` IDs are ours, not the
FRD's.

| ID | Where the spec is silent | Our assumption | Status in code |
|---|---|---|---|
| AS-01 | ~~Rule severity vocabulary~~ | **Retracted 29 Sep, not an assumption.** FRD §8.0 defines the scale: "Skala tingkat keparahan: CRITICAL, HIGH, MEDIUM, LOW". The first search looked for "severity"; the FRD says "tingkat keparahan" | the `rule_severity` enum matches FRD §8.0 exactly; a test pins it |
| AS-02 | The **floor on the standard deviation** in FR-401's z-score: TRD §10.2 writes `max(sd, floor)` without a value | one transaction (or counterparty) for count z-scores; 10% of the baseline mean for value z-scores | **implemented** in feature set fs_v1 (`z_sd_floor_*`) |
| AS-03 | **`LOW_CONFIDENCE_BASELINE` threshold** (FR-401 E2: "batas atas yang dikonfigurasi", no value) | none invented: `BASELINE_CV` is exposed as a feature and the model uses it; the label threshold stays **open** | not a threshold in code |
| AS-04 | **The category band in P10's ticket test** (FRD §8.10: "3× titik tengah pita kategori", no bands given) | the **median incoming ticket per MCC over the training dataset** (a peer-group baseline), stored as the versioned artefact `mcc_ticket_ref_v1` and used as it is at test, inference and live time, never recomputed from the data being scored. Not taken from the generator's configuration, which would let the feature read the recipe. Decided by Jevon, 30 Sep | **implemented** in feature set fs_v2 (`TICKET_MULTIPLE_R30D`, `app/reference/mcc_ticket_ref_v1.json`, `app/ml/reference.py`) |

### TRD §1.5 — conflicts still open

- **CF-01** (tied to MQ-09): boundary between forbidden autonomous closure and permitted exact-duplicate suppression
- **CF-05**: anomaly score must not raise an alert alone, but there is no anomaly rule among the twelve patterns. Proposed: anomaly stays a supporting signal. **The ML pipeline is built so both answers work** (see "Mentor directives" below): (a) the score is a supporting factor on rule alerts, or (b) a score above a threshold raises its own alert — as a rule with pattern `ML_SCORE`, seeded DRAFT, which matches FRD BR-401.2 ("unless an anomaly rule is explicitly configured and approved via §5.3"). Choosing (b) is a state change on that rule, not a code change. **Direction from the mentor (30 Sep), awaiting written confirmation:** option **(b)**. The mentor's instruction to "categorise real and anomalous transactions and alert the admin" is read as a score raising its own alert, through the `ML_SCORE` rule configured and approved via §5.3 (BR-401.2), seeded with the same documented maker-checker bypass as RUL-0001. Option (a) stays as the explanation on the alert detail (the score and its top contributing features)

### Mentor directives, 29 September — ML pipeline, Fraud and AML

Two directives changed the plan:

1. *"Dataset harus dalam bentuk enterprise, jadi butuh ribuan data. Data masuk
   model, lalu di-training. Kalau sudah, dites pakai dataset baru, lalu
   dikembangkan ke live monitoring."*
2. *"Scope Financial Crime ada 2 dan harus dikerjakan semua: Fraud dan AML.
   Use case boleh memilih, tapi project harus tetap ada Fraud dan AML."*

**Conflict with the TRD, decided by the team, awaiting mentor ratification.**
The TRD's model is *unsupervised*: a per-entity 90-day baseline with
standardized deviations (FR-401, ADR-007, TRD §10.2), with the generator's
labels as evaluation ground truth only (TRD §10.6). Directive 1 asks for a
model trained on labels. Decision (29 Sep): build the supervised model, feed
it the FR-401 baseline deviations as features, and report an FR-401-style
unsupervised score next to it in every evaluation. If the unsupervised score
does nearly as well, that is a finding, not a failure. FRD §3.2 keeps it in
scope: "satu proses fit offline yang dapat direproduksi dengan artefak
berversi"; automated retraining and MLOps promotion stay out of scope.

**Domain map.** The FRD does not split the twelve patterns into AML and
Fraud; it only names one role for both ("AML/Fraud Analyst", FRD §4.1.3).
This map is ours, proposed by the team and corrected against each pattern's
FRD §8 logic. Every evaluation metric is reported separately per domain.

| Domain | Patterns | Why |
|---|---|---|
| AML | P01, P02, P06, P07, P08, P12 | threshold, structuring, round-amount, value-step, geography and watchlist typologies |
| Fraud | P04, P10, **ATO** (new) | velocity bursts, merchant misuse (P10 also has a transaction-laundering, i.e. AML, angle), account takeover |
| Overlap (mule networks) | P03, P05, P09, P11 | pass-through, dormant accounts receiving funds, shared devices, funnels |

Two moves from the team's first proposal, **confirmed 30 Sep**: **P03**
(funds in and out within 24 h is the defining mule behaviour) and **P05**
(generator 1.1.0 builds it as a dormant account receiving one large *credit*,
which is a sleeper/mule shape; the fraud side of dormancy is covered by ATO).

**Domain is for reporting; model ownership is separate.** The domain column
above decides how results are grouped in every report. Which model *detects*
a pattern depends on how fast the behaviour happens, not on its domain. Two
models (decided 30 Sep):

| Model | Unit | Owns | Why |
|---|---|---|---|
| Fraud model | one row per **transaction**, scored when it arrives | ATO, P04, **P05** | things that happen in a moment: a takeover, a velocity burst, a dormant account coming back to life |
| AML model | one row per **(entity, week)** | P01, P02, P03, P06, P07, P08, P09, P10, P11 | patterns that build up over days or weeks |

So P05 is reported under Overlap but detected by the Fraud model, and P10 is
reported under Fraud but detected by the AML model (a merchant's profile is a
30-day picture). P12 is watchlist screening, outside both models' reach
(excluded from training, recall still reported). Every report also shows each
model's recall on the patterns it does not own, so cross-coverage is visible.

**Datasets.** Training: the Full profile, seed `20260923`. Test: **also the
Full profile**, seed `20261001` (decided 30 Sep; the 100k size gave ~15 ATO
and 8–14 positives per pattern, too few to trust). Both regenerate in ~6 s and
are not committed.

**ATO (account takeover) is a project extension, not one of the FRD's twelve
patterns.** Dormancy, then a device never seen for that entity, then a burst of
outgoing transactions. It is the Fraud use case, scored **per transaction**.
Its label code and any reason code need ratification.

**Fraud detection stops at an alert (NG-01).** A fraud score or a fraud alert
never blocks, holds, reverses or declines a transaction; the platform has no
write path to any payment system. "Live" fraud monitoring means scoring a
transaction as it arrives and raising an alert for a human, nothing more.

**Open items from this work:**
- **RC-ML-01**: an alert raised by a model score (CF-05 option b) needs a
  reason code, and the FRD catalogue (one code per pattern, §8.1–8.12) has
  none for it. Proposed `RC-ML-01`. Needs ratification
- **ATO label and reason code**: not in the FRD catalogue. Needs ratification
- **`geo_list_v1`** (`backend/app/reference/geo_list_v1.csv`) is the
  organisation's configured high-risk geography list, with the same standing
  as P02's threshold: configuration, not derived from the countries any
  scenario uses. The generator builds its P08 exposure against the same
  organisational list, as it builds P02 against the threshold
- **Labels for stage 2** (from building the features): a positive row is a
  week, or a transaction, that contains the labelled entity's own scenario
  rows (`label_transactions.csv`), not any week overlapping a label window
  (P05's window spans its whole dormancy). Entities that took part in a
  scenario without carrying its label (P11's senders) are excluded from both
  training negatives and false-positive counts, and reported as a group of
  their own
- **Stage 3 reports a separate slice for customers with under 30 days of
  history** (decided 30 Sep), so that blind spot is visible, not averaged away
- Language discipline still applies to fraud output: alert text describes what
  was observed ("first use of a device never seen for this customer, after 137
  days without activity"), never "fraud" as a conclusion (BR-406.2, NFR-16)

### Generator 1.1.0 scenario artefacts (found 29 Sep, fix planned as 1.2.0)

Comparing the transactions inside each labelled positive window with ordinary
background traffic in the Full profile shows traces of *how* a scenario was
built, not of the behaviour it represents. A model would learn them:

| | background | P04 | P03 | P10 | P06 | P08 | P07 |
|---|---|---|---|---|---|---|---|
| transactions with no device | 5.5% | 98.6% | 87.8% | 86.5% | 80.1% | 73.0% | 69.2% |

Scenario rows are emitted without the entity's device (background only lacks
one 5% of the time, as a declared defect), and P03's inbound credit always
lands at 09:xx. Static customer attributes, ID numbering, amount cents and time
of day otherwise show no difference. Planned fix in generator 1.2.0: scenario
rows use the entity's own device, and the non-quarantining defects (missing
device, missing counterparty) apply to them at the declared rates.

**The check is automatic** (built 30 Sep; results in §5): every raw transaction column
(hour, weekday, channel, type, direction, missing counterparty, missing device,
last digits of the amount, …) is compared between scenario and background
transactions, and a column that differs strongly without being part of that
pattern's definition is flagged. It runs on 1.1.0 (the evidence) and on 1.2.0
(the proof it is clean); both results go into the report.

**Generator 1.2.0 scope (decided 30 Sep):** (1) the artefact fixes above;
(2) ordinary customers occasionally get a new device, so "new device" also
happens in legitimate traffic; (3) the ATO scenario, 60 in Full; (4) 30 ATO
look-alike edge cases; (5) `label_transactions.csv`, per-transaction ground
truth for the Fraud evaluation. Tiny and Small are refreshed in the same
commit (team rule).

### Generator 1.4.0 — every TRD §11.2 statement measured (30 Sep, done)

Directive: fix P10 as 1.4.0 and go through **every** behavioural statement in
TRD §11.2 at once, so 1.4.0 is the last generator change before training.
Measured on the training seed (full, 20260923) with the same script before and
after; "rule" figures use approximate FRD §8 default rules on the fs_v1
features, per entity over the whole period (generator QA, not the app's rules).

| TRD §11.2 statement | 1.3.0 | 1.4.0 | |
|---|---|---|---|
| Retail: "pembayaran bernilai kecil" | median 73k, 98.5% under 1M | median 104k, 98.3% under 1M | held |
| Retail: "yang sering" (activity per entity) | **flat**: mean 39.6, median 40, p99 72, CV 0.35; busiest 10% hold 16% | **heavy-tailed**: mean 27.6, median 16, p90 60, p99 179, CV 1.42; busiest 10% hold 40% | fixed |
| Retail: "top-up" | 25% of rows, 30% of them *outgoing*, amounts not round | 20%, all incoming, round to IDR 50,000, from 1–2 own accounts (median 1) | fixed |
| Retail: type mix and direction | 25% each type, 30% incoming for every type | 40% merchant (5% refunds), 25% P2P (40% received), 20% top-up (in), 15% bills (out, median 1 biller, p90 3) | fixed |
| Retail: "transfer ke sekumpulan counterparty kecil yang stabil" | regulars 3–8, but top-ups and bills drew from the same book | P2P out: median 5 counterparties over a median 7 transfers, 70% to repeat counterparties | fixed |
| Retail: "pengelompokan pada hari gajian dan akhir bulan" | 29.9% on paydays (uniform 16.4%), 13.6% month-end (11.3%) | 29.9%, 13.5% | held |
| Business: "pola settlement" | **none** | 35,635 weekly sweeps to one own bank account; median business sweeps in 81% of weeks | fixed |
| Business: activity | same as retail (mean 40.2) | mean 100.3 (3.6x retail), p99 538 | fixed |
| Business: "refund pada tingkat normal" | 1.0% reversed | 1.0% | held |
| Business: "basis pembayar yang luas" | 0.32 distinct payers per payment; the first payers dominated early months (P10 payer concentration on 116 ordinary businesses once evaluable) | 0.57; an established payer base from day one; 0 payer-concentration fires | fixed |
| Business: "nilai tiket yang konsisten dengan kategori" | 24.2% of business-weeks over 3x the band, 24.0% under 1/3; one business could be a hospital and a book store | 5.9% over, 1.3% under (all residual: fs_v1's band midpoint, see fs_v2 below); one MCC per business; paid within declared hours | fixed |
| P10 evaluable (30 txns in 30 days, FRD §8.10) | 0.4% of business-months | 14.0% | fixed |
| Control-clean: raises nothing (FRD §8.14) | 0 of 830 on every approximate rule | 0 of 830 | held |
| Edge: arisan (P02) | spread past the 7-day window; fires 0/30 | same | held |
| Edge: rekening uang sekolah (P11) | **generic**: 8 transfers | 30–60 payers, one fee, same names each month, spread over 12 days; fires 1/30 (FRD TD-P11-NEG-01) | fixed |
| Edge: penyalur payroll (P04) | on the 25th, but 6 generic transfers | 15–40 staff within 3 hours on the company's payday (23rd–28th, Friday before a weekend), two months; fires 24/30 | fixed |
| Edge: kios agen (P06) | round float top-ups; fires 25/30 | same, as top-ups | held |
| Edge: pedagang musiman (P07) | **missing** (generic transfers) | three weeks of 20–35 sales of IDR 2–6M from many buyers; fires 19/25 | fixed |
| Edge: pelajar lintas negara (P08) | family support, normal corridor; fires 0/25 | same | held |
| Edge: keluarga satu HP (P09) | **missing** (generic transfers) | 3–4 members at one address on one handset; the 4-member families fire (48/102 member entities) | fixed |
| Edge, FRD §8 expected FPs for the rest | P01, P03, P05, P10, P12 generic (6 transfers) | bonus/car down payment; salary IDR 20–80M passed to bills (fires 20/30); back from abroad (17/30); B2B supplier (21/25); shared given name (no rows) | fixed |
| Boundary cases | three IDR 100M deposits for every pattern but P02: on none of their thresholds, and P07 fires on them | on each pattern's own threshold, non-firing side: P01 exactly 100M on a thin history, P02 3x500M, P03 79%, P04 14 in a day, P05 89 days, P06 4 round, P09 3 entities, P11 7 senders; none for P07, P08, P10, P12 | fixed |
| TRD §11.1 total (target 420,000) | 405,945 (−3.3%) | 421,282 (+0.3%); test seed 421,456 | held |

Also fixed because the measurement exposed them: late arrivals were backdated
up to 100 days *before* the period (0.4% of rows), giving entities months of
empty "history" and false P07 spikes; dormancy scenarios on quiet customers
could follow no activity at all (P05 positives caught 39/40 → 40/40).

**Activity distribution (the specific check requested).** 1.3.0 was flat, as
above. 1.4.0 draws each entity's share of the same total from a lognormal
(σ = 1.0, mean 1, merchants x3). Effect on rows with a baseline (fs_v1,
training seed, NaN = gate closed):

| Rows without … | 1.3.0 | 1.4.0 |
|---|---|---|
| AML entity-weeks (total) | 197,903 | 149,115 |
| AML: own p95 (P01 relative, 20 txns in 90 days) | 70.1% | 67.1% |
| AML: FR-401 z-scores (30 txns over 30 days) | 76.0% | 72.0% |
| AML: P07 weekly baseline (6 weeks) | 25.3% | 26.5% |
| Fraud transactions: own p95 | 64.5% | 42.9% |
| Fraud transactions: p95 of the last 50 | 50.5% | 38.3% |
| Fraud transactions: device novelty (30 days) | 20.8% | 18.4% |

Per transaction, coverage improves a lot (transactions concentrate on active
entities with long histories); per entity-week only a little (most
entity-weeks belong to quiet entities). **Stage 3 still reports the rows
without a baseline as a limitation** (AML 67–72%, Fraud 38–43%), as directed.

**What still fires on unlabelled background (approximate rules):** P01 17
(all weekly settlement sweeps of IDR 100M+ by sole traders: FRD §8.1 lists
"konsolidasi batch settlement merchant"), P05 10 (quiet customers' natural
dormancy: "wallet musiman atau sekunder"), P07 12 ("pertumbuhan bisnis"),
P10 9 (all the ticket sub-condition, the fs_v1 band midpoint), P11 2, and P09
397 — every one a P11 participant (a sender sharing the funnel's device),
which stage 2 already treats as its own group. Approximate (entity, pattern)
pairs in total: 2,129 (1.3.0: 1,959) against TRD §11.1's 500–1,500 expected
alerts; the P11 participants are the bulk of the excess.

**Decision for the stage-2 gate — fs_v2 (decided 30 Sep: built as AS-04, the training data's median per MCC, not a configured band).** fs_v1's
TICKET_OVER_BAND divides by the *declared* band's midpoint (GT_1M → IDR 2M), so
a merchant whose MCC's ordinary ticket is IDR 4.5–9.5M (computers, precious
metals) looks 3x over band while behaving normally: the whole residual 5.9%
and all 9 background P10 fires. FRD §8.10 says "3x titik tengah **pita
kategori**", the category's band, not the declared one. Proposal: fs_v2 reads
an MCC category reference (typical ticket per MCC) as organisation
configuration, like `geo_list_v1`, and computes the ticket features against
it. The FRD gives no values for the category bands, so the reference would be
**our assumption**, flagged as such. Deciding before training avoids
retraining.

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
The test database is **dropped and rebuilt from the migrations at the start of
every run**, so an edited migration can never leave a stale schema behind.
Reference data a migration seeds (the P02 rule) is restored after every
truncate.

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

**Current priority (mentor directive, 29 Sep): the ML pipeline.** "Dataset
harus dalam bentuk enterprise, jadi butuh ribuan data. Data masuk model, lalu
di-training. Kalau sudah, dites pakai dataset baru, lalu dikembangkan ke live
monitoring." Plus a second directive: Fraud **and** AML must both be covered
(see §8, "Mentor directives"). Plan approved 30 Sep, stages: 1a generator
1.2.0 and 1.3.0 (**done 30 Sep**) · 1b feature library + leakage tests (**done 30 Sep**) ·
generator 1.4.0, TRD §11.2 in full (**done 30 Sep**; fs_v2 decision pending) · 2 labels +
training (two models) · 3 evaluation per domain · 4 integration, whose target
is **§13 "Konsep produk"**.

**Must not be missed in the ML integration stage:** the app's `transaction`
table does not carry `device_id`, `counterparty_country`, `transaction_type`
or `merchant_id` (the bridge drops them). The Fraud side cannot score in the
app without `device_id`, so extending the ingestion contract and the table is
**mandatory** there, not optional. Adding LightGBM to the backend image also
needs `libgomp1` in the Dockerfile (`python:3.11-slim` lacks it).

**Rule as data — stages 1–2 done, stages 3–4 DEFERRED (29 Sep).** Done: the
`rule` / `rule_version` / `simulation_result` tables with DB-enforced
immutability (migration 0005), and P02 moved to RUL-0001 v1 with every alert
naming its version (migration 0006). **Deferred by the mentor's directive
above**, not because of a blocker: stage 3 (rule CRUD + versioning endpoints,
FR-301/302) and stage 4 (simulation, FR-303). The schema for both already
exists, so resuming needs no migration. Decisions waiting for when it resumes:

- **Rule reference for P02.** TRD §18.8 (rule inventory) and the §8.6 example
  call P02's rule `RUL-0007`, with `RUL-0001` being P01. The seed uses
  `RUL-0001`; recommendation is to keep it (the inventory numbers read as a
  delivery-time history with gaps, not reserved numbers). Unconfirmed
- **Reason codes must be "unik dan terdaftar"** (FRD §5.3, guard on draft
  creation). FRD §8.1–8.12 give exactly one reason code per pattern, so stage 3
  should accept only the registered code for the pattern, not free text. With
  FR-301 AC2 that means at most one live rule per pattern
- **Simulation period ≤ 6 months** (FRD §5.3, guard DRAFT → IN_SIMULATION).
  Stage 4 should default to the last 6 months of data and reject longer. The
  5%-of-transactions volume cap (FRD §5.3 exceptions) belongs to FR-304, but
  the simulation result should already record the rate

**Safe to build now** (independent of MQ-01…MQ-10):
1. **Rule as data, stages 3–4** — deferred, see above
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

---

## 13. Konsep produk — product concept (target for ML stage 4, not built)

Recorded 30 Sep from the team and the mentor. It is the reference for the ML
pipeline's integration stage. **Nothing in this section is built yet.**

### Upload mode (built first)

1. **Upload data.** The first screen is as simple as possible: one upload
   area, then straight to the dashboard
2. **Validation.** Broken rows go to quarantine and the count is shown
3. **Both models score the data**: Fraud per transaction, AML per
   entity-week, using the ACTIVE model versions. An upload **never retrains**
   a model (FRD §3.2: one reproducible offline fit, no automated retraining)
4. **Dashboard.** Transactions processed; flagged items split into Fraud /
   AML / Overlap; trend over time; score distribution
5. **Flagged list.** Each item opens a detail with the *reason*, i.e. the top
   contributing features (ADR-007), and the related transactions
6. **Triage decides.** False positive, or escalate into a case (the flow the
   app already has)
7. **Audit.** Every decision lands in the audit trail

### Live monitoring mode (after the app is complete; mentor directive)

- The system watches a stream of transactions for a period (e.g. 3 days),
  separates normal from flagged, and raises alerts
- For the demo: an accelerated replay, labelled as a simulation (TRD §2.2).
  Still to be confirmed with the mentor
- The database is loaded with history first: the FR-401 baseline needs up to
  90 days, and P02 a 7-day window
- The mentor's "alert to the admin" means the **Triage Analyst**, because
  ROLE_ADMIN may not act on alerts (FRD §4.1.1). A new alert needs an in-app
  notification
- The Fraud model is called per transaction as it arrives; the AML model runs
  in batch (e.g. nightly) over what has accumulated

### Principles that may not be broken

- Model output is advice. It never blocks or holds a transaction (NG-01).
  Alert text is descriptive and never concludes "fraud" (BR-406.2, NFR-16)
- Upload mode and live mode use **the same feature functions and the same
  models**

### What does "upload" upload? (design question)

Today `POST /ingestion/transactions` takes a transactions CSV only, an
unknown account sends the row to quarantine, and customers and accounts are
seeded master data (a documented simplification, §9). The models need
customers, accounts, devices and history. **TRD §6.1 already defines the unit
of ingestion as a file drop with a manifest** listing each file's name,
SHA-256 and record count, covering `customers.csv`, `accounts.csv`,
`devices.csv`, `transactions.csv` and the rest: a package.

| Option | For | Against |
|---|---|---|
| A. Transactions only, on master data already loaded (today) | Smallest change. Realistic for daily operation, where master data changes slowly. It is what live mode streams anyway | The first load needs another path (a script today). A new customer's transactions quarantine. Not a one-screen demo |
| B. A full package every time (TRD §6.1: manifest + all files) | Matches TRD §6.1 exactly. Self-contained. The bridge (`load_raw_dataset.py`) already projects it | Master data re-sent every batch needs upsert rules (changed KYC status, closed accounts). Full is ~85 MB: the upload size limit and a synchronous request (no worker, §4 deviation). Heavier validation |
| **C. One upload area that accepts either (recommended)** | A package (zip: manifest + files) for the first load or a master-data refresh; a transactions CSV or micro-batch after that. Live mode is a stream of the second kind. Keeps the one-screen front page | Two ingestion paths behind one drop zone. The package path is new work: master tables the app lacks (business customers, merchants, devices), upsert rules, per-file quarantine |

With C the package path verifies the manifest first (checksums,
`synthetic_declaration`, contract version), as the bridge does today, and
**rejects any ground-truth file** inside it (`labels.csv`,
`label_transactions.csv`, `generation_report.json`, `scenario_catalogue.md`,
`seeds.json`): the app must never see the answers, for the same reason the
model may not. A Full-size package is too large for one synchronous request;
the demo uploads a smaller one (Small, or a date slice), and the Full load
stays a command-line path until a worker exists. **Decided 30 Sep: option C.**
For the demo, Full is loaded by script; the on-screen package upload is for
Small or new data. The other conflicts below are accepted as analysed.

### Mapped onto today's app (input for stage 4)

| | Pages | Endpoints |
|---|---|---|
| **Reuse as is** | Login, CaseDetail, Audit | `/auth/*`; `POST /alerts/{id}/disposition`; `POST /cases`, `GET /cases/{id}`; `GET /audit`, `/audit/export`; `GET /ingestion/batches`, `/batches/{id}`, `/ingestion/quarantine`; `GET /transactions` |
| **Change** | Overview → role-aware home: the upload area for Data Ops, the dashboard for everyone. AlertQueue → filter by domain (Fraud / AML / Overlap) and source (rule / model), sort by score. AlertDetail → a "why flagged" block: top feature contributions, model version, FR-406's fixed caption | `POST /ingestion/transactions` → also carries `device_id`, `counterparty_country`, `transaction_type`, `merchant_id` (**mandatory** for Fraud). `GET /overview` → dashboard figures per domain, trend, score distribution, with the synthetic-data caveat as a payload field. `GET /alerts`, `/alerts/{id}` → domain, score, model version, contributions. `POST /detection/p02/run` → one detection run over rules and models (TRD A22 `POST /detection/runs`) |
| **New** | Notification bell; a live-monitoring page with a permanent simulation banner | `POST /ingestion/packages`; model registry (`GET /detection/models`, versions with the TRD §10.5 envelope); `GET /detection/scores`; tables `business_customer`, `merchant`, `device`, `model_version`, `ml_score` (+ per-feature contributions), `notification`; a replay runner for live mode |

### Where the concept meets the FRD/TRD

1. **A flagged list that triage disposes is an alert queue.** If model flags
   reach triage on their own (steps 4–6), a score raises alerts by itself.
   FRD BR-401.1 / BR-401.2 allow that only through an anomaly rule configured
   and approved via §5.3, and TRD §10.2 says the anomaly score creates no
   alert in the MVP (CF-05). Steps 4–6 as written are **CF-05 option (b)**:
   the `ML_SCORE` rule with reason code RC-ML-01 and, until FR-304 exists, the
   same documented maker-checker bypass as RUL-0001. Under option (a) the
   list is rule alerts carrying the score as a factor. **Direction (30 Sep):
   option (b), with (a) as the explanation on the alert detail**; see CF-05 in
   §8, awaiting the mentor's written confirmation
2. **One front page for everyone meets RBAC.** Uploading is ROLE_DATA_OPS only
   (FRD §4.1.2; the permission matrix in §9); triage cannot upload. So the home
   screen is role-aware
3. **"Live monitoring" is a simulation in this MVP.** FRD AS-03, NG-06, §3.5
   and L-04, TRD §2.2: ingestion is batch; near-real-time is a scheduled
   micro-batch over a replay file, labelled as a simulation. Allowed, as long
   as the label is always visible
4. **A new-alert notification goes beyond FR-610's triggers** (assignment, SLA
   breach, returned case, approval request). The mechanism is FR-610's (in-app
   only, BR-610.2; no unmasked identifiers, BR-610.1); the trigger is an
   extension, or comes for free if new alerts are auto-assigned (FR-603 →
   FR-610 AC1). **Decided 30 Sep: the FR-603 route.** New alerts are
   auto-assigned, and the assignment notification (an official FR-610 trigger)
   tells the Triage Analyst; FR-610's triggers are not extended
5. **Words on screen.** "Fraud" and "AML" are fine as the name of the
   monitoring function that raised a flag, but a flag is described by what was
   observed and carries FR-406's fixed caption that it is an observation, not
   proof of wrongdoing (BR-406.1: the caption cannot be dismissed; BR-406.2:
   no "suspicious", "fraud" or "money laundering" on a signal; NFR-16)
6. **Every metric surface carries the synthetic-data caveat** as a payload
   field (TRD §10.6, FRD L-01), the dashboard's counts and score distributions
   included
7. **The scoring model version must be ACTIVE through maker-checker
   (FR-405).** Not built: the first model version is seeded ACTIVE with the
   same documented bypass as RUL-0001
