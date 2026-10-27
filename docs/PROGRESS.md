# FCIP — Progress Status

**As of 27 October 2026 · roughly 18% of MVP scope (estimate)**

Supersedes any earlier progress figure. Scope definitions, architecture and open
questions are unchanged — see `PROJECT_CONTEXT.md`.

> **26 October, branch `lab-thin-slice` only (fork, not `main`):** a model-lab
> prototype of the mentors' "framework" direction — upload a labelled dataset,
> map columns, choose feature groups and models (Logistic Regression, LightGBM,
> Isolation Forest), compare on a chronological test period, pick a threshold,
> save as a DRAFT `model_version`. No scoring is activated. 698 backend tests
> pass (684 + 14 new); frontend build passes. Details, demo script, decisions
> and open questions: [LAB_THIN_SLICE.md](LAB_THIN_SLICE.md). The figures in
> this file are not recounted for it.
**How to read the numbers.** Counts below are *full-equivalent* (a partial
requirement counts half). "Done" means the requirement's observable behaviour
exists in code; it does **not** mean every FRD acceptance criterion has its own
passing test. A requirement is only listed as Done if the FRD's own meaning of
that FR ID matches what was built — several IDs in the previous revision of this
file were attached to different functionality and have been corrected.
The percentage is an estimate, not a measurement; neither the FRD nor the TRD
assigns effort weights.

---

## Where we are

| Dimension | Built | % |
|---|---|---|
| Functional requirements (full-equivalent) | ~22 of 120 (16 done + 12 partial at half credit) | ~18% |
| Detection patterns implemented as app rules | 1 of 12 (P02) | 8% |
| Database tables | ~20 of ~90 | ~22% |
| API route handlers (all routers, incl. auth/health) | ~34, against ~80 in the TRD catalogue | not comparable 1:1 |
| RBAC roles defined | 9 of 9 | 100% (definition only — see D15) |
| NFRs proven by measurement | 3 of 18 | 17% |
| Demo-relevant UAT scenarios runnable end to end | 3 of 15 (UAT-01, 04, 05 are plausible; others blocked) | ~20% |

**Test status (be precise about what was verified):**
- Core application suites (alerts, cases, evidence, rules, auth, RBAC, audit, P02
  detection, rule schema): **203 passed, 0 failed**, run 27 Oct against PostgreSQL 16.
- The earlier claim of "735 passed, 8 skipped, 5 deselected" (`pytest -m "not slow"`
  in Docker) has **not been independently reproduced**; the ML suites are slow and
  were not completed in the verification run. Re-run in Docker and record the
  command and output date before quoting that number.
- Frontend `npm run build` was not re-run in the verification pass.
- Passing tests establish technical correctness only; they do not establish
  detection quality. The ML quality gate is still unmet and every model is DRAFT.

---

## Requirement status

### Done (behaviour matches the FRD meaning of the ID)

| FR | What works |
|---|---|
| FR-101 | Ingestion batch registration — `BAT-` sequence, SHA-256 checksum, duplicate file for same (source, business date) → `409` |
| FR-102 | Record validation against schema and business rules, quarantine |
| FR-105 | Per-batch processing log with end-of-batch reconciliation |
| FR-307 | Entity-level aggregation window reproducible from stored bounds |
| FR-308 | Reason code and contributing factors on every alert |
| FR-601 | Filterable, paged alert queue |
| FR-602 | Full alert detail with explanation payload |
| FR-603 | Alert assignment with audit event |
| FR-604 | Disposition with mandatory reason (≥ 20 chars) |
| FR-605 | Escalate alert for investigation |
| FR-608 | Link alert to a case |
| FR-801 | Create case from an escalated alert |
| FR-813 | Evidence immutability enforced by database trigger, SHA-256 hash |
| FR-1101 | Authentication — argon2id, JWT, rotating refresh cookie, idle/absolute timeout, lockout |
| FR-1104 | Immutable audit events written in the business transaction |
| FR-1105 | Audit search and CSV export, export itself audited |

### Partial (exists, but a named part of the FR is missing)

| FR | Built | Missing |
|---|---|---|
| FR-301 | Rule list and detail (read) | Creating a draft rule, reason-code uniqueness, plain-language description guard |
| FR-302 | Immutable versions viewable | New version created on change; `RULE_VERSION_IMMUTABLE` error; version diff |
| FR-303 | Simulation endpoint | Not verified against ACs: 7-day freshness guard, ≤ 6-month period, overlap report, sample of 20 |
| FR-306 / 309 / 310 | Detection run for P02 (+ ML rule DRAFT); structural no-write role | Only one pattern runs; dedupe/suppression and priority scoring incomplete |
| FR-612 | Related-alert context partly shown | Case history and false-positive-in-30-days context |
| FR-804 | Case transitions `OPEN → IN_PROGRESS → PENDING_CLOSURE → CLOSED` | FRD vocabulary (`IN_INVESTIGATION`, `PENDING_DECISION`, `PENDING_APPROVAL`, `RETURNED_FOR_REWORK`, `APPROVED_CLOSED`); reopen flow |
| FR-808 | Case queue and assignment | Case detail completeness, SLA status, unauthorised-URL behaviour |
| FR-811 | Upload of evidence with hash and size check | MIME/magic-byte allow-list verification, mandatory description, synthetic-content confirmation, MinIO storage (currently an internal `evidence://` reference) |
| FR-814 | Maker submits closure with a reason | Decision type, reasons that reference evidence items, completeness check (FR-805) |
| FR-815 | Checker approve/reject; maker ≠ checker checked in application code | `403 SEGREGATION_OF_DUTIES_VIOLATION`, DENIED audit event, database CHECK, routing of `DRAFT_REPORT_RECOMMENDED` to MLRO only |
| FR-1103 | Nine roles defined, `require_role` on endpoints | Deny-by-default startup assertion, generated role × endpoint × state matrix test, object-level access |

### Previously listed as complete but not built (corrected 27 Oct)

| Previous claim | Reality |
|---|---|
| FR-708 "Evidence attachment" | FR-708 is capture of an investigation view (timeline/graph/Entity 360) as evidence. Only file upload (FR-811) exists |
| FR-816 "Checker approval or rejection" | FR-816 is the approval queue and workload view. Approve/reject is FR-815; the queue is not built |
| "GATE 3 unblocked" | Gate 3 requires the full chain to a decision with SM-06 green. Screening, decision types, narrative and several guards are absent |

---

## Built since the last progress report

### 27 October — Investigation chain skeleton (partial; GATE 3 not reached)

- **Roles:** `ROLE_SENIOR_INVESTIGATOR`, `ROLE_MLRO`, `ROLE_AUDITOR`, `ROLE_MANAGEMENT`,
  `ROLE_ADMIN` added (enum, migration 0010, frontend types, seed logins). The roles
  exist; the FRD §4.2 permission matrix is not yet fully enforced or tested.
- **Alert assignment (FR-603):** `POST /alerts/{id}/assign` with audit event.
- **Case queue and transitions (FR-808 partial, FR-804 partial):** `GET /cases`,
  `POST /cases/{id}/start`, `/assign`, `/submit-closure`, `/approve-closure`, `/reject-closure`.
- **Evidence (FR-811 partial, FR-813):** insert-only table with trigger, SHA-256 hash,
  upload and list endpoints; mutation of closed cases rejected.
- **Maker-checker on case closure (FR-814/815 partial):** closing requires a second user;
  self-approval is refused in application code with HTTP 409.
- **Rules API (FR-301/302 read-only, FR-303 simulation):** `GET /rules`, `GET /rules/{ref}`,
  version read, `POST /rules/{ref}/versions/{v}/simulate`. No create, submit, approve or activate.

**Known divergences from the FRD/TRD introduced or left open here:**
1. State vocabulary: alerts use `OPEN/DISPOSED/ESCALATED` (contract has 10 states);
   cases use `OPEN/IN_PROGRESS/PENDING_CLOSURE/CLOSED/REOPENED/ESCALATED/REFERRED`
   (contract has 7 different states). The `CaseStatus` docstring says "all seven FRD
   states", which is not accurate. Evidence types also differ from FRD §5.10.
2. Closure is not guarded by the completeness check (evidence present, alerts
   dispositioned, screening resolved), so a case with no evidence can be submitted.
3. Segregation of duties is enforced in application code only, returns 409, and does
   not write a DENIED audit event.
4. Minimum reason length for closure is 10 characters (own decision); the FRD universal
   rule is 20.

**Own decisions:**
1. Evidence URI scheme `evidence://{case_id}/{sha256}` until MinIO is wired.
2. Closure/rejection reason minimum 10 characters (conflicts with FRD §5 universal rule — to be aligned).
3. Alert assignment restricted to roles with analytical/investigative scope.

**Ringkasan capstone (Indonesia):**
Pada 27 Oktober 2026, kerangka rantai investigasi dibangun: sembilan role didefinisikan,
antrean dan transisi case, penugasan alert, unggah evidence dengan hash SHA-256 dan
trigger imutabilitas, serta persetujuan penutupan case oleh orang kedua. Pekerjaan ini
masih parsial: kosakata state belum sesuai kontrak FRD/TRD, pemeriksaan kelengkapan case,
jenis keputusan dan penegakan pemisahan tugas di level database belum ada, sehingga
GATE 3 belum tercapai. Pattern deteksi baru 1 dari 12. Tes inti aplikasi (203) lulus;
angka suite penuh perlu diverifikasi ulang.

## Per domain

Percentages are rough, based on the status tables above.

| Domain | % | Note |
|---|---|---|
| D1 Ingestion | ~40% | Batch, checksum, log, reconciliation done. DQ metrics (FR-108), late-arrival (FR-106), reprocessing (FR-107) not built |
| D2 Entity Resolution | 0% | `customer_id` still stands in for `entity_id` |
| D3 Risk Scoring | 0% | |
| D4 Rules Engine + Detection | ~25% | P02 as data; rule read + simulation; no create/submit/approve; 11 patterns missing |
| D5 Anomaly + Graph | ~5% | ML signal shown on alerts (supervised, DRAFT); graph and FR-401 baseline not in the app |
| D6 Screening | 0% | Generator produces watchlist; no engine, no review flow |
| D7 Alert & Triage | ~45% | Strongest domain; state vocabulary still narrower than contract; no SLA, bulk, reopen |
| D8 Entity 360 / Timeline / Graph | ~2% | |
| D9 Case Management | ~30% | Create, queue, assign, transitions; notes, completeness check, merge, reopen missing |
| D10 Evidence + Maker-Checker | ~25% | Upload, hash, immutability; capture of views, decision types, SoD error/audit missing |
| D11 AI Narrative | 0% | |
| D12 Regulator Reporting | 0% | |
| D13 Rule/Model Monitoring | 0% | |
| D14 Dashboards | ~3% | Overview only |
| D15 Governance/RBAC/Audit | ~45% | Roles, audit, auth done; deny-by-default assertion, role matrix tests, config governance missing |

---

## Against the 16-week backlog (FRD §14.3)

Calendar position is about **week 8 of 16** (handoff dated 7 October = week 5; verify the
project start date). Feature freeze (Gate 5) is week 14, so roughly six weeks remain.

| Gate | Status |
|---|---|
| GATE 1 (week 4) — FRD v1.0 frozen, integration contract signed | **Not formally closed.** FRD still v0.9, MQ-01…MQ-10 unanswered, A/B split never happened |
| GATE 2 (week 7) — first real alert in the queue | Reached (P02) |
| GATE 3 (week 10) — chain ingestion → alert → case → decision, SM-06 green | **Not reached.** Missing: decision types, completeness check, screening, narrative |
| GATE 4+ | Not started |

---

## Next (priority order)

0. **Mentor, this week (no code):** written answers to MQ-01, MQ-02, MQ-04, MQ-05, CF-01, CF-05;
   a one-page parameter sheet for the 12 patterns (default value, keep/change column); ruling on the
   A/B split and a cut list for the 84 Must requirements (FRD §14.6 C2).
1. **Align state vocabulary** to TRD §17.2 (alert, case, evidence, decision types) with migration and tests.
2. **Complete the decision workflow:** decision type, evidence-referencing rationale, completeness
   check returning all unmet conditions at once, `403 SEGREGATION_OF_DUTIES_VIOLATION` + DENIED audit,
   database CHECK `checker_id <> maker_id`.
3. **Patterns P01, P03–P10 as rule-as-data** (logic exists offline in `app/ml/comparators.py`), each with
   positive / negative / boundary / explanation / sufficiency tests and the control-cohort test.
4. **Rule maker-checker** (FR-301 create, FR-304, FR-305), MLRO as checker.
5. **Entity 360 and timeline** with evidence capture (FR-701, 702, 708).
6. **Screening engine + minimal entity resolution** (P12, SM-06 database guarantees).
7. **AI narrative, template provider first**, then grounding check and usage log.
8. **Draft report and watermarked export** (FR-911…914).
9. **DQ metrics, rule metrics, basic dashboards** (FR-108, 1001, 1011–1013).
10. Risk scoring (minimal), P11/graph — cut first if time runs out.

**Freeze, do not extend:** further ML research, live monitoring, and any `ML_SCORE` activation
until CF-05 is confirmed in writing.

---

## Still open

- **MQ-01…MQ-10** unanswered past their week-3 deadline (DEP-11). MQ-02's default is already
  implemented in P02; the other answer means redesigning the detector.
- **CF-01 and CF-05** (TRD §1.5) open.
- **The A/B split never happened.** FRD §13, TRD §17, interfaces I-01…I-11 and Gate 1 have no counterparty.
- **FRD §14.6 C2** ("the Must set is achievable") has never been ticked.
- **ML quality gate unmet:** best AML account-window precision 0.677%; all models DRAFT.
- **State vocabulary divergence** (see 27 October entry) — needs a decision: align to contract, or
  record a documented ADR and get mentor sign-off.
- **TRD §11.1 vs §11.5:** "~600" vs 820 entity-resolution duplicates; generator follows §11.5.
- **UAT-12:** one correlation ID from batch to report is not well defined because an alert's window can
  span several batches; batch and alert currently chain separately.
- **Unreproduced test count:** the full-suite figure needs a recorded Docker run.

---

## Earlier progress entries

### 20 October — full-network benchmarks and human-review readiness

AML retains all 885,744 edges and evaluates 27 complete scenarios, but account-window
precision remains 0.677%. A separate public FRAUD benchmark raises matched final
precision 1.09%→2.02% while missing one extra positive (66→65); strict coverage
fails, including low amounts. Six forests fit without targets; no final retuning.
A blinded offline page and import workflow prepare 300 cases; all are PENDING.
**254 focused checks pass**, 95 new; historical 52/35/17 protected hashes match.
**Own decisions:** AS-11 records public-source/peer/chronology choices, strict
guards, disclosed integrity corrections and unknown human/crime outcomes.
**Capstone:** Evaluasi dan alat review siap; peningkatan precision belum memenuhi
syarat recall dan semua model tetap DRAFT. [Evidence](ml/precision_20261020.md).

### 16 October — source-cadence repair and recall-preserving follow-up

All five authorised steps are complete: validation audit, new source-cadence
generator, grouped selection, six target-free fits and fresh frozen final,
plus a separate published sender–receiver AML benchmark. Generated 1M/200k/200k
rows; no alternative passes the coverage/workload guard, so both retain the
reference. Final simulated precision/recall: AML **0.96%/67.36%**, FRAUD
**3.01%/57.69%**; quality remains inadequate. **159 focused checks pass**;
35 new and 52 prior frozen hashes match. No app/DB/promotion changes.
**Own decisions:** AS-10 records the new generator, relative-view experiment,
three parent groups, strict fallback and disclosed external graph exclusions.
**Capstone:** Evaluasi lebih ketat, tetapi belum ada model lebih baik; data
tanpa fakta pembeda tidak bisa memastikan fraud. [Full evidence](ml/robustness_20261016.md).

### 16 October — controlled retraining and fresh final simulation

Jevon approved the proposed source-only/mixed training comparison, comparable
validation, feature repair, frozen selection, new final test and report. The
new offline runner fits **16 models** and evaluates **64 model/threshold
options** across AML and FRAUD. The original full mixed models reproduce
15 October's model text byte-for-byte. Original source-only models mark almost
all generated validation rows as outliers; simply removing dummy data does
not resolve the gap between observed-source and simulator populations.

The separate research representation uses a supported minimum-30-observation
reference with complete timestamp-boundary ties, calendar-day exposure and
the 30-observation/30-observed-day gate. It excludes explicit clock/history-age
risk inputs. Equal-origin sampling is a separate fit arm performed after
complete histories are built. Existing source, generator, datasets and models
are unchanged. No target enters fitting/preprocessing; synthetic validation
labels select candidates under the predeclared F1/coverage/review-load rule
(AS-09). This is label-informed selection of unsupervised estimators.

After frozen selection/code/model/input hashes, **200,000 fresh final rows**
are generated with seed `2026101601`. Both reference and selected candidates
are scored on that same draw. AML precision **3.52% → 9.38%**, recall
**63.79% → 38.79%**; FRAUD precision **3.28% → 6.71%**, recall
**22.56% → 22.98%**. AML retains only 45/74 reference detections, so the 80%
validation-coverage safeguard fails to generalize. FRAUD's point recall change
is inconclusive under paired source-parent intervals. These are synthetic
scenario metrics; the source has no verified fraud targets. Known source
parents, repeated validation and remaining generator shortcuts limit claims.

**Outcome: partial research improvement, no promotion.** Every new model stays
offline DRAFT; research cards cannot silently replace the app serving contract.
No registry, database, UI, P02 or CF-05 change occurs. No configuration is tuned
after the new final draw. [Report, exact counts and limitations](ml/retraining_20261016.md),
compact JSON evidence and a CSV of all validation options are under `docs/ml/`.

**Verification:** 20 feature, 14 workflow and 8 integrity checks pass, plus
62 related existing pure ML tests. Initial/manifest/frozen hashes verify before
and after selection/final evaluation. Tests cover strict target separation,
ancestry, source metrics staying null, guarded candidate selection, symmetric
same-time history, before-fit/during-fit tamper refusal and fresh-test ordering.
The pre-existing full app test results above remain historical; app modules
and frontend were not changed in this stage.

**Decisions taken independently for Jevon to check:** preserve generator and
old evidence; separate portable observation-reference features; explicit origin
balancing and threshold grid; research-only 80% reference coverage constraint;
parent-cluster uncertainty; preserve negative final findings and keep DRAFT.
These defaults are not FRD thresholds, model approval or mentor ratification.
Commit/push remain with Jevon.

**Ringkasan capstone (Indonesia):** Eksperimen membandingkan training tanpa
label menggunakan sumber saja, campuran sumber-simulasi, dan sampling asal
yang seimbang. Sebanyak 16 model dilatih dan dipilih menggunakan validasi
sintetis dengan aturan yang ditetapkan sebelumnya. Pengujian baru 200 ribu
transaksi setelah pembekuan model menunjukkan presisi meningkat, tetapi
cakupan AML menurun dan kemampuan deteksi fraud nyata belum dapat dibuktikan.
Seluruh kandidat dipertahankan sebagai DRAFT serta tidak diaktifkan di aplikasi.

### 14–15 October — source-based data replacement and reusable unsupervised flow

Jevon authorised replacing all old dummy corpora with varied source-conditioned
simulation and a reusable structure/normalization engine, then confirmed USD
and unsupervised learning. This supersedes the old supervised/data-freeze
instruction for this work; CF-05 remains open. Details, specification references
and assumptions AS-07/08 are in `PROJECT_CONTEXT.md` §14. The supplied source
has 50,000 unlabeled observations; publisher/augmentation lineage is unverified,
87.5% are date-only, and the source timezone is unknown.

Implemented canonical profiles, validation/quarantine/provenance, available-history
features, source-account partitions before augmentation, label-free Isolation
Forest fitting, immutable JSON models/cards and matching app scoring. Generated
**1,000,000 train + 200,000 validation + 200,000 final-test transactions**; final
test is created after both model/card hashes freeze. Fit also includes 35,662
original source-training observations. Evaluation-only stress sidecars and raw
identifiers never become fitting targets/features. Complete-record web CSV
splitting stays below the existing 50 MiB cap. Observed context survives upload;
USD is never converted to IDR. This is reusable configured infrastructure,
not automatic understanding of arbitrary source semantics or full AutoML.

**Quality result:** final synthetic precision/recall is AML **3.13% / 62.28%**,
Fraud **2.25% / 18.64%**, small-amount Fraud **2.48% / 32.76%**. Source holdout
flag loads are AML 42.22% and Fraud 92.16%; no source fraud precision is
measurable without verified labels. Three further validation-only prototypes
also failed to establish an adequate precision/recall tradeoff and were not
promoted or tested on the final holdout. The exact USD83.44 small-slice boundary
reporting fix is documented; it changes no model, data, scores or thresholds.
[Evaluation and evidence](ml/behavioral_evaluation_20261015.md).

Removed 154 old generated files (780,744,721 bytes) and cleared dataset tables
in ten explicitly named local demo/test DBs, preserving users/sessions, previous
audit, rules, models and simulation evidence, with appended reset audit events.
Source CSV/archive, private backups and Git history remain. Regression corpora
are generated temporarily by tests; the 29-row legacy contract fixture is
test-only. The live demo now has **0 transactions / 0 quarantine / 0 alerts**,
800 new account proxies and four existing users. Old supervised models are
audited **RETIRED**; new `aml_if_v1`/`fraud_if_v1` remain **DRAFT**. No model is
ACTIVE. `ML_SCORE` remains DRAFT and P02 remains IDR-only. The UI states these
lifecycle/currency limits and shows separate synthetic validation diagnostics.

**Verification:** a real isolated endpoint upload accepted 200,000 rows,
quarantined 0, preserved observed metadata and matched offline flags exactly
(2,270 AML / 16,336 Fraud) in 62.48 seconds locally. Its new-data QA database is
retained; it did not upload into the live demo. Full/focused/host regular checks,
all five slow checks, production frontend build and whitespace checks pass.
Guarded browser checks pass eight Data Ops/Triage × Overview/Model signals ×
1440/390 px combinations: lifecycle, validation values, role controls and no
overflow; no operational mutations or browser exceptions.
An additional 63 focused API checks pass after propagating observed timestamp
precision/timezone knowledge to transaction/evidence lists and the latest
summary; date-only data no longer appears with an invented midnight/local offset.
[Manual upload guide](DEMO_FRESH_DATA.md) and [engine guide](ML_DATA_ENGINE.md)
identify the ready 38,293,500-byte CSV and the DRAFT scoring limitation.

**Decisions taken independently for Jevon to check:** explicit canonical
profiles; anonymous account proxies; validated JSON forest storage;
median-replacement score explanations; bounded complete-record upload files;
portable regression fixtures and preservation-checked cleanup; keep poor models
DRAFT and retire withdrawn legacy scoring. Recipe/default numbers are recorded
experimental assumptions, not specification thresholds or approved prevalence.
No new approval, external crime label, FX assumption, CF-05 decision or automatic
commit/push was inferred. Engineering/data replacement is complete; reliable
high-precision financial-crime detection is **not achieved**.

**Ringkasan capstone (Indonesia):** Pengembangan menambahkan engine standardisasi
data yang dapat digunakan ulang, fitur perilaku berdasarkan riwayat yang tersedia,
dan training Isolation Forest tanpa label. Sebanyak 1,4 juta transaksi simulasi
dibagi untuk training, validasi, dan pengujian baru setelah model dibekukan.
Pengujian upload 200 ribu transaksi berhasil serta konsisten dengan skor offline.
Namun presisi skenario sintetis masih rendah dan dataset sumber tidak memiliki
label fraud terverifikasi. Model baru dipertahankan sebagai DRAFT; hasil ini belum
membuktikan kemampuan deteksi fraud atau pencucian uang pada data nyata.

### 7 October — production UI makeover

Jevon authorised "make over ui nya". The existing login, sidebar workspace,
Overview, alert/case review, model flags and audit are redesigned for desktop
and mobile. CSV selection/drop/preflight, actual batch feedback, manual
detection, paged alert results, searchable transactions, model explanations
and audit filters/export use the existing APIs. AML entity-weeks and Fraud
transactions stay separate; FR-406/synthetic notices and role guards remain.
No backend, generator, shared dataset, model, threshold or CF-05 change.

**Verification:** `npm run build` (TypeScript + Vite) and `git diff --check`
passed. Scripted Playwright checks passed: 64 mocked-empty role/route/width
combinations and 24 populated route/width combinations, at 320/390/768/1440
px, with no whole-page overflow. Real alert pages 25/25/6, status reset,
model tabs/search/keyboard contributions, CSV multipart/preflight and pending
locks, filter/loading consistency, login deep links and audit offset clamping
passed. On the isolated clone, trimmed 19/20-character reason checks, Triage
escalate/false-positive, Investigator case creation, audit chain and CSV export
passed. Upload/detection feedback checks use mocked responses; actual Full
endpoint/scoring parity is the preceding stage's verification, not a new run.
No permanent frontend test suite was added and backend tests were not rerun.

**Decisions taken independently for Jevon to check:** light/navy design and
sidebar; SVG icons without added dependencies; 25-row transaction/alert and
50-row model/audit pages; keyboard focus and responsive scrolling; matching
CSV preflight and applied-filter behaviour (AS-06, presentation choices,
not specification/model values). Commit and push remain with Jevon.

**QA incident:** a failed interception sent `qa-transactions.csv` to the live
demo at 15:15:37 WIB, creating `BAT-000002` with **0 accepted / 3 quarantine**.
It remains recorded, and Jevon was informed. His existing part-1 upload and
37-alert detection run were retained; the demo was not reset. All subsequent
mutation checks use isolated frontend :5174/API :8001 and a QA clone, with
live API requests blocked. Exact batch/correlation IDs and the partial-history
scoring limitation are recorded in `PROJECT_CONTEXT.md` §13 and
`DEMO_FRESH_DATA.md`.

Visual evidence: [login](screenshots/ui-makeover-login.png),
[populated Overview](screenshots/ui-makeover-overview.png), and
[mobile alert queue](screenshots/ui-makeover-alert-mobile.png). Populated
screenshots show the isolated Full-dataset QA clone, not the live demo.

**Capstone summary (Indonesian, ready to paste):**
Pada 7 Oktober 2026, antarmuka FCIP diperbarui dengan workspace berbasis
sidebar, dashboard menggunakan angka aktual, upload CSV dengan hasil
validasi batch, serta halaman alert, case, sinyal model dan audit yang
responsif. Unit entity-week AML dan transaksi Fraud tetap dipisahkan.
Keputusan alert tetap oleh Triage, dan sinyal model tetap mendukung
investigasi selama CF-05 belum disetujui. Generator, data bersama, model dan
threshold tidak berubah. Insiden tes berupa tiga baris quarantine tanpa
transaksi diterima dicatat; tes operasi selanjutnya memakai lingkungan QA
terpisah. Perubahan tampilan tidak menambah jumlah kebutuhan fungsional
atau menjadi bukti kinerja model pada data nyata.

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