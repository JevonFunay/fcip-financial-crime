# FCIP — Progress Status

**As of 7 October 2026 · Week 5 of 16 · ~17% of MVP scope** (percentage last recounted 29 Sep; table and endpoint counts updated 6 Oct; mockup adds no implemented FR)

Supersedes any earlier progress figure. Scope definitions, architecture and open
questions are unchanged — see `PROJECT_CONTEXT.md`.

---

## Where we are

| Dimension | Built | % |
|---|---|---|
| Functional requirements (full-equivalent) | ~16 of 120 | 13% |
| FR priority **Must** only | ~15 of 84 | 18% |
| Detection patterns | 1 of 12 (P02) | 8% |
| Database tables | 19 of ~90 | 21% |
| API endpoints | 20 of 80 | 25% |
| RBAC roles | 4 of 9 | 44% |
| NFRs proven by measurement | 3 of 18 | 17% |
| TRD components | ~6 full + ~5 partial of 30 | ~25% |

**411 regular backend tests pass** (410 in Docker plus its documentation check
on the host). The 5 slow tests passed on the unchanged baseline earlier in
this session. Frontend typecheck/build passed on that baseline; production
login and empty-demo role controls were also verified in the browser.

Pure FR counting gives ~10%; ~17% weights the foundation work already done.
Neither document assigns effort weights, so the percentage is an estimate — the
counts underneath it are not.

---

## Completed requirements

| FR | What works |
|---|---|
| FR-101 | Ingestion batch registration — `BAT-000001` sequence, SHA-256 checksum, source system, business date, expected records. Duplicate file for the same (source, business date) → `409` |
| FR-102 | Record validation against schema and business rules |
| FR-105 | Per-batch processing log with end-of-batch reconciliation |
| FR-307 | Correct entity-level aggregation window, reproducible from stored bounds |
| FR-308 | Reason code + 8 contributing factors on every alert |
| FR-601 | Filterable, paged alert queue |
| FR-602 | Full alert detail with explanation payload |
| FR-604 | Disposition with mandatory reason (≥ 20 chars) |
| FR-605 | Escalate alert for investigation |
| FR-608 | Link alert to a case |
| FR-801 | Create case from an escalated alert |
| FR-1101 | Authentication — argon2id, JWT, rotating refresh in HttpOnly cookie, idle/absolute timeout, lockout |
| FR-1104 | Immutable audit events for material actions, written in the business transaction |
| FR-1105 | Audit trail search and CSV export, with the export itself audited |

Partial: FR-306, FR-309, FR-310, FR-1103, FR-808, FR-612.

---

## Built since the last progress report

### 7 October — clean demo and fresh Full files for manual upload

Jevon requested zero operational data and newly generated hundreds of
thousands of rows using the existing tested template. The app now uses the
separate `fcip_demo_s20261007` database; original `fcip` remains intact and
has a private dump/config backup. Transactions, quarantine, batches, alerts,
cases and ML scores start at zero. Master data is ready: 11,200 customers,
13,455 accounts, 1,500 merchants and 11,932 devices. Existing users/passwords
were preserved, and both trained models registered with unchanged thresholds
and the already documented approval bypass. P02 ACTIVE; `ML_SCORE` DRAFT.

Fresh Full seed 20261007 was generated on 7 October, after model fit, with
unchanged generator 1.4.0 and reference date 2026-09-30: **421,746 rows**.
`app.scripts.prepare_upload_csv` reuses the manifest check and bridge
projection, preserving row order, defects and duplicates. It publishes two
verified CSV parts (313,720 / 108,026 rows), each below the existing 50 MiB
upload cap, plus a provenance/count/hash manifest. Ground truth is excluded.
Upload both parts before detection: existing scores are immutable.

**Verification:** on a separate database, both actual upload endpoints
completed: 412,364 accepted, 9,382 quarantined (3,190 invalid, 4,182 duplicate,
2,010 unknown-account rows). Every accepted transaction and all master
attributes matched the offline dataset. Detection raised 58 P02 alerts;
AML scored 147,931 entity-weeks / flagged 1,933, Fraud scored 412,364
transactions / flagged 4,346. Triage false-positive/escalate passed;
Data Ops disposition was denied (403). The full technical check took
113.56 seconds, without populating the live demo. All four actual logins
and browser checks passed. Five new adapter tests cover byte boundaries,
Unicode/quoted newlines, cross-part duplicates, defects, safe refusal and
complete Small ingestion parity; 411 regular tests pass. Existing dependency
warnings remain. Practical steps and rollback: `docs/DEMO_FRESH_DATA.md`.

**Post-fit quality evaluation:** the unchanged models were evaluated against
the fresh seed's offline labels, without `--train` or threshold tuning.
Owned scenario recall: AML 517/517 (100%), Fraud 153/155 (98.71%; P05 misses
2/40). Unit precision/recall: AML 98.50%/98.42%, Fraud 98.79%/98.44%.
Both fresh control cohorts have 0/830 entities flagged; look-alike errors
remain, and AML flags 396/401 excluded P11 sender participants. All ten
model/reference checksums stayed unchanged. Offline flags exactly match the
upload run. Same-generator synthetic testing does not establish real-world
performance or erase the older holdout's errors. Report:
`docs/ml/evaluation_manual_s20261007.md`.

**Decisions taken independently:** use a separate reversible demo database;
preserve master data and existing logins; use a new seed with the same Full
preset; split by complete CSV records under the current cap; verify on an
isolated database. No generator, shared Tiny/Small, threshold or CF-05 change.
Jevon receives the exact commit commands and executes commit/push himself.

**Capstone summary (Indonesian, ready to paste):**
Pada 7 Oktober 2026, dataset sintetis baru berisi 421.746 transaksi dibuat
setelah training menggunakan generator 1.4.0 dan format yang telah diuji.
Database demo dipisahkan agar pengujian manual dimulai dari nol tanpa
menghapus data lama. Dua CSV siap unggah diverifikasi melalui endpoint:
412.364 transaksi diterima, 9.382 dikarantina, dan input fitur sama dengan
loader offline. Deteksi menghasilkan 58 alert P02 serta 1.933 sinyal
entity-week AML dan 4.346 sinyal transaksi Fraud. Keputusan alert tetap oleh
Triage; sinyal model belum menjadi alert sendiri selama CF-05 belum disetujui.
Evaluasi label baru menghasilkan recall skenario AML 100% dan Fraud 98,71%
pada threshold tetap, dengan kesalahan pada look-alike dan dua skenario P05.
411 tes reguler lulus; hasil sintetis bukan bukti kinerja pada transaksi nyata.

### 7 October — upload-to-results mockup for mentor review

Jevon approved the mockup stage. `docs/mockups/fcip-upload-results.html`
contains the interactive design; `fcip-upload-results-preview.html` is the
self-contained browser preview. The journey covers simulated package upload,
validation/scoring progress, dashboard, a rule-alert queue, model-only
signals, alert explanation, triage decisions and case creation. Scores,
charts and detail fixtures are labelled illustrative; the Small ingestion
counts use the earlier measured run. See `docs/mockups/README.md` for sources
and review steps. This is a design proposal, not production implementation.

**Decisions taken independently:** keep rule alerts separate from DRAFT
model-only signals; apply business-domain filters only to known rule patterns;
show the distribution of stored signals rather than imply all scores were
stored; preserve different AML/Fraud units; simulate the combined processing
journey for review; retain role restrictions, permanent captions and human
decisions. CF-05, model thresholds, generator and shared datasets are unchanged.

**Verification:** browser interaction and responsive-layout checks passed;
the standalone preview works offline and delayed saved-state echoes do not
override current navigation. `docs/mockups/build_preview.py` rebuilds the
preview from its source using only Python's standard library;
the production baseline passed 406 regular backend tests, all 5 slow tests,
and frontend typecheck/build before the mockup. Numerical comparator and
dependency warnings were emitted. At mockup completion, direct browser
verification of the production ML UI was still outstanding; the mockup check
does not replace the later clean-demo and endpoint checks above.

**ML volume rechecked against local artefacts:** training 421,282 source /
412,015 accepted transactions; separate test holdout 421,456 / 412,160.
Fraud fit uses 314,013 transactions; AML fit 113,340 entity-week units.
Both datasets were generated before fit, with the test holdout evaluated
afterwards; 20261001 is a seed, not a generation date. A requirement for a
test dataset generated strictly after fit would need an additional run.
No new training, evaluation or dataset generation was performed in this stage.

**Capstone summary (Indonesian, ready to paste):**
Pada 7 Oktober 2026, prototipe interaktif alur unggah data, dashboard,
antrean alert, dan tinjauan analis disiapkan untuk pembahasan dengan mentor.
Prototipe membedakan alert berbasis rule dari sinyal model, mempertahankan
keputusan oleh manusia, serta memberi label data sintetis dan angka ilustratif.
Artefak ML diverifikasi memakai 421.282 transaksi sumber untuk training dan
421.456 transaksi untuk pengujian terpisah. Dataset pengujian dibuat sebelum
fit tetapi baru dievaluasi setelah training; hasilnya belum membuktikan kinerja
pada transaksi nyata. Prototipe belum menambah kebutuhan fungsional yang
diimplementasikan. Jevon menerima perintah commit dan menjalankan commit/push.

### 6 October — ML stage 4: the models inside the app

`POST /detection/run` (the Overview button) runs every ACTIVE rule and then
every ACTIVE model version; scores show as a *Model score* column in the alert
queue, a *Model signals* section on each alert (with FR-406's fixed notice and
the strongest named features), and a new *Model flags* page. Underneath:
the transaction contract carries type, country, merchant, device and source
status, merchants and devices are master data (migration 0007); model
versions are stored immutable with the TRD §10.5 envelope and scores
insert-only (migration 0008); the `ML_SCORE` rule (RUL-0002) is seeded DRAFT,
so no alert comes from a score until the mentor confirms CF-05 (b) in
writing. Full test seed loaded by script (`--ingest`): 412,160 rows, one run
in 44 s, and the app flags exactly the units the models flag offline (1,975
entity-weeks, 4,339 transactions). Models registered ACTIVE without
maker-checker, a documented bypass like RUL-0001's.

### 30 September — ML stage 3: evaluation on the unseen test seed

`app/ml/evaluate.py`, report in `docs/ml/evaluation_v1.md`. LightGBM recall
100% (AML, 529 labels) and 99% (Fraud, 155), but the FRD rules reach the same
recall on this generator; the model's gain is precision (7 vs 231 look-alike
labels flagged for AML, background 0.2 vs 28.8 entity-weeks a week). Left out
of training, distinctive patterns are not found (P02, P06, P10, P04, P05
0–13%): the models learn the typologies they are given. The Fraud model flags
one control-clean entity on the test seed (fails T-DET-CONTROL-01); the AML
model flags 389/390 P11 senders; the TRD's unsupervised anomaly score detects
almost nothing (AP 0.04). Stage 4 waits for Jevon and the mentor.

### 30 September — ML stage 2: labels and training

Labels (`app/ml/labels.py`), two LightGBM models (AML per entity-week, Fraud
per transaction) with logistic regression, the FRD default rules and the TRD
§10.2 anomaly score as comparators, a scenario-level validation split, a
threshold that flags no control-clean entity, a ten-shuffle canary, and model
cards with the TRD §10.5 envelope. Validation AP 0.998 (AML) and 0.997
(Fraud), against 0.44/0.56 for the rules and 0.04/0.14 for the anomaly score.
Near-perfect on synthetic data is a warning, tested in stage 3. LightGBM and
scikit-learn added; `libgomp1` in the Dockerfile (tests train inside the
container). 391 passed on the host, 390 + 1 skipped (docs outside the mount)
in Docker.

### 30 September — feature set fs_v2 (before training)

P10's ticket test now compares a merchant's average ticket with the median
ticket of its MCC over the training data (`TICKET_MULTIPLE_R30D`, assumption
AS-04), stored as the versioned artefact `mcc_ticket_ref_v1` and used as it is
for the test set and live scoring. fs_v1 divided by the declared band's
midpoint, so high-ticket categories looked 3x over band while behaving
normally. On the training seed: ordinary business-weeks over 3x 5.9% → 0.0%,
background P10 fires 9 → 0, positives 38/45 and look-alikes 21/25 unchanged.

### 30 September — generator 1.4.0: the population as TRD §11.2 describes it

Every behavioural statement in TRD §11.2 was measured on the training seed and
fixed where it deviated, so 1.4.0 is the last generator change before
training (table with before/after figures in PROJECT_CONTEXT §8). Activity was
flat (every entity ~40 rows; the busiest 10% held 16%) and is now heavy-tailed
within the same TRD §11.1 total (retail median 16, p99 179, busiest 10% hold
40%; merchants 3.6x retail). Top-ups are incoming and round, bills outgoing,
each to the customer's own one or two accounts and one to three billers.
Merchants settle weekly (none before), are paid within their declared hours
at tickets around their MCC's (24% of business-weeks were 3x off their band,
now 6%, all of it fs_v1's band midpoint), by a payer base that exists from day
one. P10's minimum of 30 transactions in 30 days is met in 14% of
business-months (0.4% before). Every look-alike now resembles its pattern
(school fees, payroll, seasonal trader, family handset, B2B supplier and the
rest; seven were generic transfers), and boundary cases sit on each pattern's
own threshold (they were three IDR 100M deposits everywhere). Also fixed: late
arrivals backdated before the period, dormancy scenarios with no prior
activity. **0 audit flags on five seeds**, Tiny and Small refreshed; ML datasets
regenerated: training 421,282 transactions, test 421,456, 754 positive
entities each. Small through the real API: 20,916 read, 20,458 accepted, 458
quarantined; P02 2/2 positives, 0/2 look-alikes, 0/42 control. Rows without a
baseline: Fraud 64.5% → 42.9%, AML 70–76% → 67–72% (reported as a limitation
in stage 3). Pending at the stage-2 gate: fs_v2 with an MCC category reference.

### 30 September — ML feature library fs_v1 (stage 1b)

The inputs of both models, in `backend/app/ml/`. **AML**: one row per entity
and calendar week, 57 features (volume, P02 band incl. rolling 7-day clusters
that straddle two weeks, pass-through, velocity, dormancy, round amounts, value
spikes, geography, shared devices, merchant tickets, funnels, FR-401 baseline
deviations, novelty, data availability). **Fraud**: one row per transaction,
33 features (amount against the entity's own past, including a count-based
baseline that survives a dormancy; velocity in 1h and 24h; first use of a
device, counterparty or merchant; device tenure; dormancy gap). Parameters are
locked with their FRD source. Tested: no ground-truth file is read, no
identifier or smuggled column changes a value, no feature reads the future,
one entity alone equals its batch rows, and the rows are exactly what
ingestion accepts. Short history leaves baseline and novelty features empty
rather than misleading. Full training profile: 197,903 + 396,788 rows in ~46 s.

### 30 September — generator 1.3.0: counterparties as TRD §11.2 describes them

1.2.0 still drew a fresh random counterparty for every row, against TRD §11.2
("transfer ke sekumpulan counterparty kecil yang stabil" for retail, "basis
pembayar yang luas" for business), so every payment went to someone new and
the takeover signal "a recipient never paid before" meant nothing. Now retail
customers pay 3–8 regulars and 4–10 usual merchants, businesses have a broad
payer base, and a first-time counterparty stays ordinary: 25% of individual
and 30% of business payments, against 100% for ATO. The audit gained that
view and found three more recipe traces on the way (P05's dormancy erasing its
own history, P09/P11 borrowing a bystander's device, P07 repeating one amount
all week), all fixed. **0 flags on five seeds**; 1.1.0 has 38. ML datasets
regenerated: training 405,945 transactions, test 407,088, 754 positive
entities each. Small through the real API: 20,799 read, 20,344 accepted, 455
quarantined; P02 2/2 positives, 0/2 look-alikes, 0/42 control.

### 30 September — generator 1.2.0: scenarios without recipe traces, ATO, ML datasets

**The problem.** Compared column by column with ordinary traffic, 1.1.0's
scenarios carried traces of *how* they were generated: rows without a device
in 33 of 38 scenario groups (59.2% of all positive rows vs 5.0% in
background), P04's merchant payments without a merchant, P03's credits all at
09:xx. A model trained on that data learns the recipe, not the behaviour.

**The check is automatic now.** `audit_scenario_artefacts.py` compares every
raw transaction column between each scenario group and background, and flags
a difference beyond sampling noise that is not part of the pattern's
definition. 1.1.0: 36 flags. 1.2.0: **0**, on the training seed, the test
seed and three more. The audit is itself tested both ways (it catches rows
stripped of their device; it passes a random background sample).

**What changed.** Scenario rows carry the entity's own device and the
non-quarantining defects; every party has its own device and ~120 are shared,
as TRD §11.1 says (1.1.0 shared thousands at random); 15% of customers change
phones; scenario dates and hours follow background; three look-alikes that
contradicted their own notes now match them. New: the **ATO** scenario (60 at
full) with 30 look-alikes that carry at most two of its three signals, and
`label_transactions.csv`, per-row ground truth for the Fraud model.

**ML datasets.** Training: full profile, seed 20260923, 403,557 transactions,
749 positive entities. Test: full profile, seed 20261001, 407,812
transactions, 744 positive entities. Not committed; regenerated in ~6 s.
Shared Tiny and Small refreshed to 1.2.0. Small through the real API: 20,541
read, 20,086 accepted, 455 quarantined; P02 catches 2/2 positives, 0/2
look-alikes, 0/42 control.

### 29 September — rules as data, stages 1–2 (FR-301 … FR-303, partial)

**Schema (migration 0005).** `rule`, `rule_version` and `simulation_result`.
The database itself guarantees what FR-302 and BR-302.1 require: a version's
content never changes in any state (only `state` does, and an ACTIVE version
can only be suspended or retired), no version is ever deleted, an ACTIVE reason
code is unique, and reason code, severity and description are never blank.
All seven rule states exist, so FR-304/305 need no migration. Severity is the
FRD §8.0 scale (CRITICAL/HIGH/MEDIUM/LOW).

**P02 is now data (migration 0006).** Its parameters left the Python code and
became RUL-0001 v1 — exactly the former constants, and reason code
`RC-STRUCT-01` / severity `HIGH` as FRD §8.2 specifies. The detector reads the
ACTIVE version from the database; every alert records the version that raised
it; the alert detail page shows the rule's description verbatim (FR-301 AC3,
BR-301.2). The seed is written straight as ACTIVE, **bypassing maker-checker**
until FR-304 and `ROLE_MLRO` exist, and says so in the version itself and in
an audit event. All 250 earlier tests still pass with unchanged expectations.

**Stages 3–4 (rule endpoints, simulation) deferred** by the mentor's ML
directive — see `PROJECT_CONTEXT.md` §11.

### 29 September — shared datasets refreshed, Full no longer in git

`backend/sample_data/Tiny` and `Small` regenerated with generator 1.1.0; `Full`
removed from the repository (reproducible from the seed in ~6 s, and ~85 MB of
history per change). The bridge's `--shared` now covers Tiny and Small only.
A new test requires the shared copies to match the current generator byte for
byte, so changing the generator without refreshing them fails — it fails
against the old PR #1 data, as intended. Through the real API the refreshed
`Small` gives 20,509 read · 20,058 accepted · 451 quarantined, 2 P02 alerts
(2/2 positives, 0/2 look-alikes, 0/42 control), no entity with two labels.

### 28 September — shared datasets, generator 1.1.0, custom scale

**Shared datasets (PR #1).** A teammate committed `backend/sample_data/Tiny`,
`Small` and `Full`. They are byte-for-byte generator 1.0.0 output at seed
20260923 (12/12 files identical per profile when that version is re-run) —
produced before the §11.2 population fix, so they still carry the business
cohort at ~27% and, on `Full`, 20 entities labelled both positive and
look-alike. No schema change was needed; the app now loads them directly:

- `load_raw_dataset.py --shared tiny|small|full`
- **Manifest checks before parsing** (TRD §6.1): refused unless declared
  synthetic (FR-501) and every file matches its SHA-256 — the guard for data
  anyone can edit in git
- Provenance printed; a warning when another generator version produced it
- The bridge's projection is gitignored, so loading never dirties the repo

Through the real API, `Small` gives 20,182 read · 19,751 accepted · 431
quarantined, and 3 P02 alerts: 2/2 positives, 0/2 look-alikes, 0/42 control,
plus one cross-pattern hit on a P08 positive.

**Generator 1.1.0.** The population fix changed the output for the same seed
without bumping the version, so two different datasets both claimed "1.0.0".
TRD §11.7 makes the version what tells datasets apart; it is now 1.1.0.

**Custom scale — `--target-transactions N`.** For the office's request of a
100,000-transaction dataset. Every volume derives from N by the full profile's
ratio; the row count is calibrated to land exactly on N using unlabelled
parties only; each pattern is floored at 5 scenarios; minimum target 20,000.
The presets are byte-for-byte unaffected (14/14 files per profile checked).

Measured at 100,000: **generated in 1.4 s**, exactly 100,000 transactions
(96,781 generated + 3,219 calibrated), 2,381 customers, 286 business customers,
3,202 accounts, 357 merchants, 2,381 devices, 595 watchlist, 688 labels. Only
the 12 boundary cases needed the floor (1 → 5). Through the real API: 97,691
accepted · 2,309 quarantined · 16 P02 alerts — 11/11 positives, 0/11
look-alikes, 0/198 control, 5 cross-pattern hits on P07/P08 positives.

---

### Ingestion batch + processing log (FR-101, FR-105)
Every upload now runs inside a registered batch. Batch status reflects what
actually happened: `COMPLETED`, `NEEDS_REVIEW` (reconciliation mismatch, TRD
C-02), or `FAILED` (structural failure, zero rows loaded, TRD C-01). The batch
row is committed **before** parsing so a failed batch stays visible rather than
disappearing with the rollback. `processing_log` carries an identity `seq`
column because Postgres `now()` is transaction-start time and would otherwise
leave the log in arbitrary order.

New endpoints: `GET /ingestion/batches`, `GET /ingestion/batches/{id}`.

### Audit trail (FR-1104, FR-1105)
Audit coverage went from 2 object types to 5 — added `BATCH`, `DETECTION_RUN`
and `AUDIT_EXPORT`. Every detection run is now audited even when it raises
nothing, so "ran and found nothing" stays distinguishable from "never ran".

New endpoints: `GET /audit` (filters: correlation_id, object_type, object_id,
actor_email, date range), `GET /audit/export` (CSV). Results are ordered
oldest-first so a correlation_id search reads as the chain in order (NFR-13).

**UAT-12 is now demonstrable in one click** — correlation IDs on alert and case
detail deep-link into the filtered audit trail. New `/audit` page in the UI.

### Synthetic raw dataset — DEP-01 (TRD §6.1, §11)
The full banking source schema: **8 CSV files + manifest**, not the narrow
transaction CSV the skeleton ingests.

`customers.csv` (20 cols) · `business_customers.csv` (16) ·
`beneficial_owners.csv` · `accounts.csv` · `merchants.csv` (MCC, expected
bands) · `devices.csv` · `transactions.csv` (16) · `watchlist.csv` ·
`manifest.json` (per-file SHA-256, `synthetic_declaration: true`) ·
`labels.csv` · `data_dictionary.md` · `scenario_catalogue.md` ·
`generation_report.json` · `seeds.json`

Three profiles: `tiny` 1% (CI), `small` 5% (default), `full` 100%.
Full profile: **407,019 transactions in 6 seconds**, every volume inside the
TRD §11.1 target bands.

Five properties that make it usable as evidence rather than just volume:

1. **Deterministic** — same seed reproduces every file byte-for-byte (T-GEN-01). Without it no detection metric is reproducible
2. **Provably synthetic but structurally correct** — NIK-shaped IDs encoding gender the real way (female birth day +40), on province prefix `99` which is never issued; `+62899` phone block; RFC 5737 IP ranges
3. **Labelled** — all 12 patterns get injected positives, behavioural look-alikes and exact-threshold boundary cases, each writing a row to `labels.csv` with entity, window and expected reason code. Each entity carries at most one label, and the control cohort is never touched
4. **Population mix held exactly** (TRD §11.2) — retail 62 / business 18 / control 8 / edge 8 / injected 4, as exact counts at every scale
5. **Deliberately defective** — 12 defect types at controlled rates (TRD §11.4), so the quality pipeline has real work to do

Plus the entity-resolution population per TRD §11.5: five constructions, each
stating what resolution must do with it.

`load_raw_dataset.py` bridges it into the skeleton and **prints which columns it
could not carry across** (merchant, device, IP, source status, business date,
transaction type) — that gap is the distance to the full pipeline, reported
rather than hidden.

**Verified end-to-end through the real API on the `small` profile:**
20,509 rows read · 20,058 accepted · 451 quarantined across every defect type.
P02 then raises exactly 2 alerts: it catches **2/2** injected positives, fires
on **0/2** labelled look-alikes, and raises **nothing** on the 42-entity
control cohort (FRD §8.14).

On the `full` profile, checked offline against the generated files: 45/45
positives, 0/30 look-alikes, 0/830 control entities — plus 17 cross-pattern
hits on entities labelled P07 or P08, whose values step into P02's band. Those
are genuinely suspicious entities, not false positives on clean ones.

---

## Per domain

| Domain | % | Note |
|---|---|---|
| D1 Ingestion | **~40%** | was ~15%. Batch, checksum, processing log, reconciliation added |
| D2 Entity Resolution | 0% | `customer_id` still stands in for `entity_id` |
| D3 Risk Scoring | 0% | |
| D4 Rules Engine + Detection | ~26% | rules are data (P02 = RUL-0001 v1); endpoints + simulation deferred |
| D5 Anomaly + Graph | 0% | |
| D6 Screening | 0% | |
| D7 Alert & Triage | ~35% | strongest domain |
| D8 Entity 360 / Timeline / Graph | ~2% | |
| D9 Case Management | ~14% | create + detail only |
| D10 Evidence + Maker-Checker | 0% | |
| D11 AI Narrative | 0% | |
| D12 Regulator Reporting | 0% | |
| D13 Rule/Model Monitoring | 0% | |
| D14 Dashboards | ~3% | |
| D15 Governance/RBAC/Audit | **~40%** | was ~23%. Audit search/export added |

---

## Against the 16-week backlog (FRD §14.3)

- **Group B side: ~Week 6–7** — alert queue and detail (W4), lifecycle and disposition (W5), case creation and detail (W6)
- **Group A side: ~Week 3–4** — ingestion complete; entity resolution (W4), risk scoring (W5) and rules engine (W6) not started
- **GATE 2** (Week 7, "first end-to-end alert in the queue from a real detection run") — **demonstrable now**
- **GATE 3** (Week 10, full chain through to a *decision*) — not reachable; maker-checker and evidence are at zero

Calendar position ≈ Week 4–5 of 16 (~28%), ahead of scope completion (~17%)
because Weeks 8–14 carry the heaviest work: 11 more patterns, graph, screening,
AI, maker-checker, dashboards.

---

## Next

**Safe to build now** — independent of the unanswered MQ-01…MQ-10:

0. **ML pipeline** — stages 1–4 done (first version of 4); next for it: FR-405's approval flow, live mode, and activating `ML_SCORE` once CF-05 is confirmed
1. **Rule as data, stages 3–4** (rule endpoints FR-301/302, simulation FR-303) — deferred for the ML pipeline; schema already in place
2. **Case lifecycle** (FR-804) and case queue (FR-808) — columns exist, endpoints don't
3. **Alert assignment** (FR-603) and SLA tracking (FR-607)
4. **Data quality metrics** (FR-108) — the generator already produces the defects to measure

**Blocked on a mentor decision:** maker-checker (MQ-04 + 5 unbuilt roles,
prerequisite for GATE 3 and UAT-03/09/10/11), patterns P01 and P03–P12, AI
narrative (MQ-05), graph (MQ-06), screening review.

---

## Still open

- **MQ-01…MQ-10** (FRD §1.7) unanswered past their Week-3 deadline (DEP-11). **MQ-02 is the urgent one** — its proposed default is already implemented in P02, so the other choice means redesigning the detector
- **CF-01 and CF-05** (TRD §1.5) still marked open
- **The A/B split never happened.** Each group builds both engine and UI, so FRD §6's ownership column, FRD §13, TRD §17, the 11 interfaces and GATE 1 have no counterparty. Due now — GATE 1 falls in Week 4
- **FRD §14.6 item C2** ("the Must set is achievable") has never been ticked. 84 Must requirements were scoped across two groups
- **New spec inconsistency found:** TRD §11.1 says "~600 duplicate source records" for entity resolution; the §11.5 table sums to 820. The generator follows §11.5 as the more specific. Flagged, not silently resolved
- **Undecided design point:** UAT-12 asks for one correlation ID from batch to report, but an alert's evidence window can span several batches, so a single chain isn't well defined. Batch and alert currently chain separately, linked via `transaction.ingestion_batch_id`
