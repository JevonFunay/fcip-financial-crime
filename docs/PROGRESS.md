# FCIP — Progress Status

**As of 28 September 2026 · Week 4 of 16 · ~17% of MVP scope**

Supersedes any earlier progress figure. Scope definitions, architecture and open
questions are unchanged — see `PROJECT_CONTEXT.md`.

---

## Where we are

| Dimension | Built | % |
|---|---|---|
| Functional requirements (full-equivalent) | ~16 of 120 | 13% |
| FR priority **Must** only | ~15 of 84 | 18% |
| Detection patterns | 1 of 12 (P02) | 8% |
| Database tables | 12 of ~90 | 13% |
| API endpoints | 17 of 80 | 21% |
| RBAC roles | 4 of 9 | 44% |
| NFRs proven by measurement | 3 of 18 | 17% |
| TRD components | ~6 full + ~5 partial of 30 | ~25% |

**247 backend tests pass** (5 more marked `slow`, all passing). Frontend typechecks and builds.

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
| D4 Rules Engine + Detection | ~22% | rules still hardcoded Python |
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

1. **Rule as data** — `rule` / `rule_version` tables, versioned parameters, simulation (FR-301…FR-303). Largest pending refactor; the remaining 11 patterns inherit whatever shape is chosen, so doing it before adding patterns is far cheaper
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
- **Team decision — refresh the shared datasets?** `backend/sample_data/Tiny|Small|Full` are generator 1.0.0 output. Regenerating them with 1.1.0 fixes the composition and label issues, but `Full` adds ~85 MB to the repository history on every change. Options: refresh all three; refresh Tiny/Small and stop tracking Full (it is reproducible from the seed in 6 s); or keep them as a frozen 1.0.0 reference
- **Undecided design point:** UAT-12 asks for one correlation ID from batch to report, but an alert's evidence window can span several batches, so a single chain isn't well defined. Batch and alert currently chain separately, linked via `transaction.ingestion_batch_id`
