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
| `tiny` | 1% | CI | ~4,570 |
| `small` | 5% | local development (default) | ~20,900 |
| `full` | 100% | integration, final demo, ML training and test | ~421,000 (TRD §11.1 target 420,000) |

### Custom scale

For any other size, pass a transaction count instead of a profile:

```bash
docker compose exec backend python -m app.scripts.generate_raw_dataset --target-transactions 100000
```

Every other volume is derived from it by the same ratio as the full profile
(`target / 420,000` against TRD §11.1's targets), so no new numbers are
hardcoded. Three things differ from the presets, and all three are recorded in
the `scale` block of `generation_report.json`:

- **Exact row count.** Background generation lands within a few percent of
  any target (at the 20,000 minimum the per-pattern floor makes it overshoot);
  a final calibration adds (or removes) rows on unlabelled parties only, with
  defects and duplicates at their declared rates, so the file holds exactly the
  requested number. Labelled entities are never touched, so every label stays
  true, and weekly settlement sweeps are never removed.
- **Per-pattern floor.** Each pattern's positives, look-alikes and boundary
  cases are floored at 5 (`--min-per-pattern`), so every pattern keeps enough
  samples to test. Where the floor overrode pure proportion is listed.
- **Minimum target of 20,000.** One label per entity means the floors need
  distinct parties; measured below ~16,000 some scenarios cannot be placed and
  calibration can no longer land exactly.

Cases are not generated at any scale: TRD §11.6 seeds them through the service
layer so their audit trail is authentic, and they are not source data. The
report records the proportional target (43 at 100,000) for a later seeding
script. The preset profiles are byte-for-byte unaffected by this option.

**Measured at 100,000 (generator 1.1.0):** generated in 1.4 s — 100,000 transactions (96,781
generated, +3,219 calibrated), 2,381 customers, 286 business customers, 626
beneficial owners, 3,202 accounts, 357 merchants, 2,381 devices, 595 watchlist
records, 688 labels. Only the boundary cases needed the floor (1 → 5 for each
of the 12 patterns). Through the real API: 97,691 accepted, 2,309 quarantined;
P02 raises 16 alerts — 11/11 injected positives, 0/11 look-alikes, 0/198
control entities, plus 5 cross-pattern hits on P07/P08 positives.

Output lands in `backend/sample_data/raw/<profile>/` (or `raw/custom-<target>/`):

| File | Contents |
|---|---|
| `customers.csv` | 20 columns: identity, KYC status, address, contact, onboarding |
| `business_customers.csv` | 16 columns: legal name, registration, industry code, turnover band |
| `beneficial_owners.csv` | ownership percentage and control type per business |
| `accounts.csv` | wallet / virtual account / settlement, status, balance snapshot |
| `merchants.csv` | MCC, declared volume and ticket bands, settlement account, outlets |
| `devices.csv` | device type, OS, app version, emulator/rooted flags. One per transacting party, ~120 shared by design (TRD §11.1), plus devices that come into use during the period |
| `transactions.csv` | 16 columns: amount, currency, channel, transaction type, counterparty, merchant, device, IP, source status |
| `watchlist.csv` | synthetic PEP / sanctions / internal list records |
| `manifest.json` | `source_system_code`, business date, contract version, per-file SHA-256 and record counts, `synthetic_declaration: true` |
| `labels.csv` | ground truth for every injected scenario |
| `label_transactions.csv` | ground truth per row: which transactions each scenario and look-alike emitted (for per-transaction evaluation). Like `labels.csv`, evaluation only: never loaded, never a feature |
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

**Shaped like Indonesian wallet activity (TRD §11.0.3, §11.2).** Payday
clustering around the 25th, a month-end tail, quieter weekends, arisan
collection, agent kiosks, school fees, payroll and remittance corridors. Since
1.4.0 activity is heavy-tailed (a few very active entities, most quiet),
merchants settle weekly, and top-ups, bills and business tickets behave as
their type and category say (see "Generator 1.4.0" below).

**Population mix held exactly (TRD §11.2).** Retail 62%, business 18%,
control-clean 8%, edge/ambiguous 8%, injected 4% — assigned as exact counts, so
the shares hold at every scale including the CI profile. §11.1's volumes put
real businesses at only ~11% of parties, so individual sole traders (UMKM
wallet merchants) make up the rest of the business cohort and transact like
merchants.

**Labelled (TRD §11.3).** All twelve patterns P01–P12 get injected positives,
behavioural look-alikes, and exact-threshold boundary cases; since 1.2.0 so
does **ATO** (account takeover: dormancy, a device the customer never used,
then an outgoing burst), the Fraud use case, with look-alikes that carry one
or two of those signals but never all three. Every one writes a
row to `labels.csv` with its entity, window and expected reason code, so recall
is measured rather than eyeballed. **Each entity carries at most one label**:
positives come from the injected cohort and look-alikes from the edge cohort
(spilling to ordinary parties only when those run out), and the control cohort
is never touched. A party holding both a positive and a look-alike label would
make the look-alike label a lie.

**Deliberately imperfect (TRD §11.4).** Twelve defect types at controlled
rates: malformed dates, invalid currencies, zero/negative amounts, unresolvable
accounts, exact duplicates, idempotency conflicts, late arrivals, missing
counterparties and devices, missing beneficial owners, placeholder addresses,
truncated names. The quality pipeline has real work to do. Defect counts in
`generation_report.json` are taken from the rows actually written, not tallied
as they are injected.

**Entity resolution population (TRD §11.5).** Five constructions, each stating
what resolution must do with it — must auto-merge, must not auto-merge, must
force `PENDING_REVIEW` with `IDENTIFIER_CONFLICT`, or must land in the
0.75–0.95 manual review band.

### Generator 1.2.0 to 1.4.0: scenarios without recipe traces

A scenario should differ from ordinary traffic **only in the behaviour it
represents**. Anything else is a trace of how the generator built it, and a
model learns that trace instead. `app/scripts/audit_scenario_artefacts.py`
checks this automatically: for every raw transaction column (hour, weekday,
payday, channel, type, missing counterparty, missing device, last digits of the
amount, device type, device sharing, IP range, …) it compares each scenario
group with background using total variation distance, and flags a column that
differs beyond sampling noise without being part of that pattern's definition.

```bash
python -m app.scripts.audit_scenario_artefacts sample_data/Small          # exit 1 if anything is flagged
python -m app.scripts.audit_scenario_artefacts <dir> --markdown audit.md --json audit.json
```

| Full profile | Flags | Main findings |
|---|---|---|
| **1.1.0**, seed 20260923 | **38** | Scenario rows without a device in most groups (all positives 59.2% vs 5.0% in background; P04 93.5 points above background). P04's merchant payments had no merchant (TVD 1.00). P03's credits all at 09:xx (43.5% vs 5.4%) |
| **1.3.0**, seeds 20260923 and 20261001 (training, test) | **0** | also 0 on seeds 1, 2 and 3, and on the shared Tiny and Small |
| **1.4.0**, seeds 20260923 and 20261001 | **0** | also 0 on seeds 1, 2 and 3 and the shared Tiny and Small; the 1.4.0 audit still finds 37 on 1.1.0 |

What 1.2.0 changed:

- Scenario rows use the entity's own device, and the defects that load normally
  (missing device 5%, missing counterparty 3%) apply to them at the declared
  rates. The quarantining ones still never do: a scenario must stay detectable
- Every transacting party has a device of its own and ~120 are shared by design,
  as TRD §11.1 states. 1.1.0 handed devices out at random, so thousands were
  shared by unrelated parties. The device total now follows the population,
  inside TRD §11.1's 8,000–12,000 band at full (11,902 in 1.3.0)
- 15% of individuals change phones during the period, so a never-seen device is
  ordinary, not a scenario signature
- Scenario dates follow the background's day weights (paydays busier, weekends
  quieter), bursts keep background hours, and background has a thin night tail
- Look-alikes now match their own notes: P06's are round, P08's are
  cross-border inbound, P04's payroll lands on the 25th
- P04's merchant payments carry a merchant; P03's credits arrive at any hour

What 1.3.0 changed, after the audit gained a "counterparty new for this
entity" view:

- **Counterparties as TRD §11.2 describes them.** Retail customers pay a small
  stable set ("sekumpulan counterparty kecil yang stabil": 3–8 regulars, 4–10
  usual merchants), businesses have a broad payer base. 1.2.0 drew a fresh
  random counterparty for every row, so every payment went to someone new and a
  new recipient, a key takeover signal, meant nothing
- A first-time counterparty is still **ordinary**: 25% of individual and 30%
  of business payments after a 60-day burn-in (early rows are "new" only for
  lack of history). ATO pays recipients its victim never paid: **100%**
- Each party's background is generated in date order, so a payer is new the
  first time it appears in time, and a row only uses counterparties already in
  use on its date
- P05's dormancy is 90+ days (FRD §8.5) with at least 30 days of history left
  in view; P09 and P11 rings share a device of their own, not a bystander's;
  P07's weekly amounts vary around the week's level instead of repeating

Two views are declared part of a definition rather than flagged, with the
reason in the code: for P05 and the dormant ATO look-alikes a counterparty
looks new more often (38–44% and 27–37% vs 25%), because after 90+ quiet days
in a six-month window little history is left to show it. The same happens to a
real takeover, so it does not tell the two apart.

The audit itself is tested both ways: it catches scenario rows stripped of their
device, and it does not flag a random sample of background dressed up as a
scenario. It compares each group with background of the same kind of owner
(business or individual) **and the same activity level** (under 20, 20–49,
50–119, 120+ background rows), and judges clustered rows per device or
scenario, not per row. Two refinements came with 1.4.0's heavy-tailed volumes:
the "counterparty new" view is compared within the same transaction type (a
top-up comes from the customer's own account, a transfer goes to a wider
circle), and it is **one-sided** — it flags a scenario that pays first-time
counterparties *more* often than ordinary life, the 1.2.0 artefact it was built
for, while *fewer* (a burst or a monthly cycle paying the same people within
days) is density and is reported, not flagged. Roundness views skip wallet
top-ups, which are round by nature (FRD §8.6).

#### Generator 1.4.0: the population as TRD §11.2 describes it

Every behavioural statement in TRD §11.2 was measured on the 1.3.0 training
seed and fixed where it deviated; 1.4.0 is the last generator change before
model training. The full table, with measurements before and after, is in
[PROJECT_CONTEXT §8](docs/PROJECT_CONTEXT.md). In short:

- **Activity is heavy-tailed, within the same TRD §11.1 total.** 1.3.0 gave
  every entity ~37–40 rows in six months (CV 0.35, the busiest 10% held 16% of
  rows). Each entity's share is now lognormal (σ = 1.0), merchants 3× as
  active as retail: retail median 16, p99 179, the busiest 10% hold 40%. The
  file lands on 420,000 (±0.3%) because the background leaves room for the
  scenarios and counts the settlements it produces
- **Retail rows mean what their type says**: 40% merchant payments (5%
  refunds), 25% P2P (40% received), 20% top-ups (always in, round to IDR
  50,000, from one or two accounts of the customer's own), 15% bills (always
  out, to the same one to three billers). 1.3.0 had 25% of each and 30% of
  every type incoming
- **Merchants settle weekly** to one bank account of their own (none in
  1.3.0), are paid while they are open, at tickets around their MCC's typical
  ticket (1.3.0: a quarter of business weeks more than 3× off their band), by
  a broad payer base that exists from day one; a business's outlets share one
  MCC; a sole trader without a merchant record is paid by transfer. P10's
  30-transactions-in-30-days minimum is now met in 14% of business-months
  (0.4% in 1.3.0), so the rule can actually be evaluated
- **Every look-alike resembles its pattern** (FRD §8 expected false positives,
  TRD §11.2 examples): a bonus or car down payment, arisan, salary passed to
  bills, payroll on the company's payday, a return from working abroad, agent
  float top-ups, a seasonal trader, a student abroad, a family on one handset at
  one address, a B2B supplier, school fees, a shared given name. Seven of the
  twelve were six generic transfers in 1.3.0. Several now fire the default rule,
  as the FRD expects of them: that false-positive burden is what a model should
  lower
- **Boundary cases sit on their own pattern's threshold**, on the side that
  must not fire (exactly IDR 100M on a thin history, 79% pass-through, 14 in a
  day, 89 quiet days, 4 round amounts, 3 entities on a device, 7 senders), with
  the party's own background carved away where it could tip them over. 1.3.0
  put three IDR 100M deposits at every pattern's "boundary", which sits on none
  of their thresholds and fires P07. P07, P08, P10 and P12 have none (no single
  clean threshold)
- Dormancy scenarios (P05, ATO and their look-alikes) always follow real
  activity on the dormant account; late arrivals are backdated inside the
  six-month period, never before it (0.4% of 1.3.0's rows sat up to 100 days
  earlier and gave entities months of empty "history")

### Datasets for the ML pipeline

Both are the **full** profile and are **not committed** (`backend/ml_data/` is
ignored); anyone on the team regenerates them exactly from the seed in ~6 s:

```bash
cd backend
python -m app.scripts.generate_raw_dataset --profile full --out ml_data/train_full_s20260923
python -m app.scripts.generate_raw_dataset --profile full --seed 20261001 --out ml_data/test_full_s20261001
```

| Generator 1.4.0 | Training | Test |
|---|---|---|
| Seed | `20260923` (default) | `20261001` |
| Period | 1 Apr – 30 Sep 2026 | 1 Apr – 30 Sep 2026 |
| Transactions | 421,282 | 421,456 |
| Individual / business customers | 10,000 / 1,200 | 10,000 / 1,200 |
| Accounts | 13,383 | 13,327 |
| Merchants / devices / watchlist | 1,500 / 11,932 / 2,500 | 1,500 / 11,932 / 2,500 |
| Positive entities | **754** | **754** |
| Look-alike / boundary labels · control entities | 392 / 50 · 830 | 392 / 50 · 830 |

Positives per pattern (training / test): P01 60/60 · P02 45/45 · P03 50/50 ·
P04 55/55 · P05 40/40 · P06 35/35 · P07 40/40 · P08 45/45 · P09 174/174 ·
P10 45/45 · P11 35/35 · P12 70/70 · ATO 60/60 (585 / 588 ATO transactions).
(1.3.0: 405,945 / 407,088 transactions; look-alike and boundary labels 390.)
The test set is the full size too, because 100,000 transactions left ~15 ATO
and 8–14 positives per pattern, too few to trust. A different seed means new
entities, amounts and timings from the **same** generator: the test measures
generalisation to unseen data, not to unseen scenario recipes.

### ML feature library (fs_v1)

`backend/app/ml/` turns a dataset into the two models' inputs (TRD §9.2, C-04):

```bash
cd backend
python -m app.ml.build_features ml_data/train_full_s20260923    # ~45 s at full
python -m app.ml.build_features ml_data/test_full_s20261001
```

It writes `features_fs_v1/aml.parquet` (one row per entity and calendar week
the entity transacted), `fraud.parquet` (one row per accepted transaction, as
of that transaction) and `build.json` (provenance) next to the dataset, ignored
by git like the dataset. Every feature, its window, its parameters and their
source are in [`docs/ml/feature_set_fs_v1.md`](docs/ml/feature_set_fs_v1.md),
rendered from `feature_set.py`. The parameters are locked in the feature set
and not read from the rules: a rule change must not change a model's inputs
silently.

What the tests guarantee:

- **The answers cannot reach a feature.** The loader reads only allow-listed
  files and columns; deleting every ground-truth file, adding a scenario column
  to the transactions, or renaming every identifier leaves every value
  unchanged
- **No feature reads the future**, and one entity scored alone gets exactly
  what the batch gave it: `aml_features(history, as_of, context)` and
  `fraud_features(history, i, context)` are the functions live monitoring will
  call
- **Same rows as the app**: the loader applies ingestion's own validation, so
  the features describe exactly what the upload endpoint stores
- **Short history is explicit.** A feature comparing with the entity's own past
  is NaN (not zero) below the threshold the FRD sets for it, and novelty
  features (a device, counterparty or merchant never seen before) are NaN under
  30 days of history. A package of a few weeks therefore does not make every
  payment look new

### Loading it into the skeleton

The skeleton models customers, accounts and transactions, and its ingestion
endpoint takes a narrower CSV, so a bridge script loads the master data and
projects the transactions onto that shape:

```bash
docker compose exec backend python -m app.scripts.load_raw_dataset --profile small            # generated locally
docker compose exec backend python -m app.scripts.load_raw_dataset --profile custom-100000
docker compose exec backend python -m app.scripts.load_raw_dataset --shared small             # committed to the repo
```

Before reading anything it checks the manifest the way TRD §6.1 requires of any
source drop: the dataset must declare `synthetic_declaration: true` (FR-501) and
every file must match its recorded SHA-256, otherwise it is refused. It prints
the dataset's provenance and warns when a different generator version produced
it.

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

**Measured on the shared `Small` (generator 1.4.0, through the real API in a
throwaway database):** 20,916 rows read, 20,458 accepted, 458 quarantined across
every defect type (203 in-file duplicates, 100 unresolvable accounts, 80
malformed dates, 41 zero/negative or malformed amounts, 34 invalid currencies).
P02 detection then raises exactly 2 alerts: it catches **2 of 2** injected
positives, fires on **0 of 2** labelled P02 look-alikes, and raises **nothing**
on the 42-entity control cohort (FRD §8.14).

On the `full` profile (1.4.0) the same check, run offline against the generated
files, catches 45 of 45 P02 positives, fires on 0 of 30 look-alikes and 0 of
830 control entities on both the training and the test seed, and additionally
fires on entities labelled for *other* patterns: P07 (weekly values stepping
into the band) and P08 (remittances inside the band), 14 on the training seed
and 18 on the test seed (1.3.0: 15 and 18). Those are cross-pattern hits on genuinely suspicious
entities, not false positives on clean ones; no unlabelled entity fires.

### Shared datasets in the repository

`backend/sample_data/Tiny` and `Small` are committed so the whole team loads
the same data without running the generator:

```bash
docker compose exec backend python -m app.scripts.load_raw_dataset --shared small
```

They must always be exactly what the current generator produces, and
`tests/test_load_raw_dataset.py` enforces it byte for byte. **If you change the
generator, regenerate them in the same commit** — the failing test prints the
command:

```bash
docker compose exec backend python -m app.scripts.generate_raw_dataset --profile tiny  --out sample_data/Tiny
docker compose exec backend python -m app.scripts.generate_raw_dataset --profile small --out sample_data/Small
```

The full profile is **not** committed: at ~85 MB it would add that much to the
history on every change, and it is reproducible byte for byte from the seed in
~6 s. Generate it on demand and load it with `--profile full`.

(History: the first copies, committed in PR #1, were generator 1.0.0 output
made from a checkout that did not yet have the §11.2 population fix. They were
replaced with 1.1.0 output, and the byte-for-byte test was added so a stale
shared copy cannot slip in again.)

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

The logic is in `backend/app/services/detection/p02_structuring.py`. The
parameters (FRD §8.2) are **data**: the ACTIVE version of rule `RUL-0001`,
seeded by migration `0006_seed_p02_rule` from what used to be the
`P02Parameters` constants (see [Rules as data](#rules-as-data-fr-301--fr-303)).
Version 1 holds:

| Parameter | Value | Meaning |
|---|---|---|
| REPORTING_THRESHOLD | IDR 500,000,000 | the reporting threshold being evaded |
| BAND_LOWER | 70% → IDR 350,000,000 | a transaction is "in band" when `350,000,000 <= amount < 500,000,000` |
| MIN_COUNT | 3 | at least this many in-band transactions in one window |
| AGGREGATE_MULTIPLE | 1.0× → IDR 500,000,000 | the window's in-band total must reach this |
| Window | 7 days rolling | opens at an in-band transaction, end is exclusive |

A run evaluates **every ACTIVE P02 rule version** and says which ones
(`rules_evaluated`); with none, it evaluates nothing and the audit entry says
so. Every alert records the exact version that raised it
(`alert.rule_version_id`).

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

## Rules as data (FR-301 … FR-303) — in progress

A pattern's logic is code; a **rule** is data: one typed parameter set bound to
that code (TRD ADR-006, §7.3). Migration `0005_rule_as_data` adds the tables;
`0006_seed_p02_rule` makes P02 the first rule. The endpoints and simulation
come in the next stages.

| Table | Holds |
|---|---|
| `rule` | identity: `rule_ref` (`RUL-0001`, …), `pattern_code`, a `correlation_id` shared by every audit event of the rule |
| `rule_version` | one row per version: `parameters` (JSONB), `window_type`/`window_length`, `entity_scope`, `severity`, `reason_code`, `description`, `state`, lineage (`parent_version`), `change_summary`, `changed_by`/`changed_at` |
| `simulation_result` | FR-303 output attached to a rule version: period, counts, daily distribution, overlap with other active rules, top-20 sample |

Guarantees enforced **in the database**, not just in the API:

- **A version's content never changes, in any state.** Every change is a new
  version (FR-302); a trigger lets only `state` change. An `ACTIVE` version
  may only move to `SUSPENDED` or `RETIRED` (TRD §7.3).
- **No version is ever deleted** (BR-302.1). `rule` and `simulation_result`
  rows are append-only too.
- **An ACTIVE reason code is unique**, case-insensitively (FR-301 AC2), and a
  rule has at most one ACTIVE version.
- Reason code, severity and description can never be blank (FR-301 AC1).
- All seven TRD §8.2 states exist in the enum, so FR-304 (submit/approve) and
  FR-305 (suspend/retire) need no schema change. Until they are built, only
  `DRAFT → IN_SIMULATION` is reachable.

`audit_log` gains two nullable columns: `action` (TRD §8.8, e.g.
`RULE_VERSION_CREATED`) and `details` (JSONB, e.g. a parameter-level diff).

Severity is the FRD §8.0 scale, `CRITICAL` / `HIGH` / `MEDIUM` / `LOW`
("skala tingkat keparahan"), which drives priority scoring (FR-311) and SLA
targets (FR-607). It is stored low-to-high so severities compare in order.

### P02 is rule RUL-0001 v1

- **Parameters** are exactly the former `P02Parameters` defaults, which never
  changed since the baseline commit. `P02Parameters` is now only the typed
  form of a version (no default values), built with
  `P02Parameters.from_rule_version(...)`. No threshold is left in the code.
- **Every alert names its rule version** (`alert.rule_version_id`, FR-302
  flow 3, BR-302.2). Alerts raised before this change were backfilled to v1.
  `GET /alerts/{id}` returns a `rule` block (reference, version, state, reason
  code, severity, parameters, window, and the description **verbatim**, FR-301
  AC3), and the alert detail page shows it to the triage analyst (BR-301.2).
  An alert keeps showing its own version after the rule moves on (FR-302 AC2).
- **Reason code `RC-STRUCT-01` and severity `HIGH` are FRD §8.2's own** for
  P02. The description is the sentence the TRD §8.6 alert example shows for
  this rule, an English rendering of FRD §8.2's defensive logic statement.
- A new version of the same rule that finds the same evidence does not raise
  it again: re-run idempotence is keyed by rule, not by version.

> **Temporary bypass of maker-checker.** A rule should only become ACTIVE
> through FR-304 (an analyst submits, an MLRO approves). Neither FR-304 nor
> `ROLE_MLRO` exists yet, so migration `0006_seed_p02_rule` writes RUL-0001 v1
> **straight as ACTIVE, with no approver**. This is stated in the version's
> own `change_summary` and in an audit event (`action = RULE_SEEDED_ACTIVE`,
> `details.maker_checker = BYPASSED`), and must be replaced by a real
> approval once FR-304 is built.


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
  are non-overlapping. These are logic, not parameters, so a rule version
  cannot change them: each is a small change in `evaluate` / `find_clusters`
  plus a `TEMPLATE_VERSION` bump if the FRD says otherwise.
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
