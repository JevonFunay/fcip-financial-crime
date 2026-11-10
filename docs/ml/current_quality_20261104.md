# Current FCIP quality verification — 4 November 2026

Historical baseline, preserved at commit `0382821`. The subsequent
[Lab repair and retraining](lab_repair_20261104.md) supersedes its live-code
description without rewriting this evidence. Reproduce this baseline from the
archived commit/source, not the repaired runtime: its supervised coverage guard
now rejects the old ATO/P05-missing TRAIN and its time split preserves ties.
The additive archive is `backend/ml_data/lab_quality_s20261104/source_snapshot_0382821/`.
Old model/data/report hashes remain unchanged. Compare models on the same new
final in the repair report, not percentages from these different populations.

## Outcome

The current application passes its existing **843 backend checks** and frontend
build; 18 new audit guard checks also pass. On a fresh synthetic monitoring
draw, the unchanged Lab's validation-selected **LightGBM has precision 98.56%
and recall 89.38%**, with 25 reference-negative transaction flags.

**This is not broad fraud coverage:** P04 recall is 98.67%, ATO 33.33%, and P05
0%. A post-final diagnostic finds **no ATO or P05 positive examples in the
actual TRAIN partition**. The high aggregate result is dominated by P04 and
does not establish real-world fraud or money-laundering performance. No model
has been promoted or registered by this audit.

[Compact results](current_quality_20261104_summary.json),
[full benchmark and hashes](current_quality_20261104.json),
[independent verification](current_quality_20261104_verification.json),
[post-final diagnosis](current_quality_20261104_diagnostics.json) and
[technical checks](current_quality_20261104_checks.json) preserve the findings.

## Scope and fixed evaluation protocol

Jevon requested a current quality test after the collaborative changes on
`main` at commit `e4e3eb6`. This stage checks the existing application and
measures the current model-lab algorithms offline. It does not repair or tune
production code, change the generator, register/approve a model, activate
`ML_SCORE`, or ingest into the live application database.

The lab supports supervised Logistic Regression and LightGBM, plus label-free
Isolation Forest fitting, as recorded in the 27 October framework direction.
The existing generator 1.4.0 and `make_lab_sample` provide a **synthetic FRAUD
task**: own in-window rows of ATO/P04/P05 injected scenarios are positive;
everything else, including other patterns' injected rows, is assigned zero by
the current single-label sample policy. Zero here is a benchmark assumption,
not verified legitimacy. This is not a laundering label set.

The predeclared source has 100,000 generated rows, seed `2026110401`, reference
date `2026-09-30`, and the current sample's minimum of 25 scenarios per pattern.
The unchanged lab uses all eight feature groups, its default 60/20/20
chronological row split and existing model seed `20261026`. Supervised fitting
uses TRAIN labels; vocabulary and preprocessing use TRAIN observations.
Validation AP recommends the model and validation max-F1 fixes each default
threshold. No parameters are chosen using a fresh final result.

The lab's internal test is already exposed in its comparison response, so its
metrics are reported as development diagnostics. This audit adds a separately
frozen check: model texts, features, TRAIN vocabulary, mapping, input/code
hashes and chosen thresholds are sealed **before** generating another
100,000-row draw, seed `2026110402`, reference date `2027-03-31`. Both periods
and all observations are simulated; no actual future banking data is implied.
Final features use only that new file's available history with the frozen
TRAIN vocabulary, as the current first monitoring upload does. The training
dataset is separate from the monitored transaction table.

All three fixed models are evaluated on the same accepted final observations.
A predeclared **amount-only** reference uses a nominal cutoff selected by
validation max-F1 and frozen alongside them; it is a diagnostic, not an extra
lab candidate or a deployable rule. It tests whether the models contribute
beyond large nominal values. Low amounts mean the TRAIN amount q25. A slice
without positives has null recall and cannot prove small-value effectiveness.

Fresh random seeds do not create independent real customers or new fraud
mechanisms. The two draws share generator definitions and identifier templates.
No real/public unlabelled source is relabelled, and no old model or dataset is
overwritten.

## Technical checks

Docker was initially unavailable. Backend verification instead uses a new
temporary PostgreSQL **16.15** cluster, matching Compose's major version, on a
private UNIX socket with peer authentication. The explicit test URL names
`fcip_test`; the existing fixtures create/drop that disposable database only.
The live app database and model registry are not accessed. The temporary server
is stopped after verification.

An initial environment setup attempt stopped before any test body: Alembic's
ConfigParser rejected percent-encoded socket slashes in the URL. Supplying the
private socket through `PGHOST`/`PGPORT` with a simple test URL resolves the
harness configuration without modifying application code. Initial and corrected
logs are retained separately under `/tmp`.

Frontend `npm run build` passes (TypeScript plus Vite production build, 164
modules). This verifies compilation/bundling, not a browser interaction test.
An initial focused set of 18 lab/tabular unit checks also passes; it overlaps
the later full suite and must not be added to that suite's distinct test count.

The existing regular backend suite completes with **838 passed, 5 slow tests
deselected**, in 130.10 seconds, on the isolated PostgreSQL 16.15 cluster.
There are 263 warnings, including numerical warnings in existing Logistic
Regression fixtures and dependency/future warnings. Passing functional tests
does not establish detection precision or remove the known issues below.

All **5 slow tests also pass** in 119.05 seconds: 100k-row ingestion, Full
feature construction, volume targets, 100k generator reproducibility and a
two-seed Full scenario-artifact check. Both temporary PostgreSQL servers are
confirmed stopped. In total **843 existing checks pass**, with no failed or
skipped test bodies in the corrected environment.

The new offline audit adds **18 passing guard tests**, independently repeated:
validation-only selection/native defaults, target-free final features/scores,
null unsupported positive slices, label/currency reconciliation, immutable
outputs, pre-freeze source/model/contract hashes, frozen-manifest integrity and
creation-time final-input hashes. The stage therefore has **861 distinct
passing checks** (843 existing + 18 new), not additional counts for repeated
runs. [Exact check evidence](current_quality_20261104_checks.json) records
commands, runtimes, log hashes, warnings and environment limits.

After Jevon started Docker Desktop, the daemon became available, but
`docker compose ps --all` listed no project services. The live model registry
was therefore not inspected. Model quality below concerns an offline copy of
the current lab pipeline, not an identified ACTIVE deployment artifact.

## Current lab and fresh synthetic final

The 100,000-row development source yields **98,318 usable observations** and
1,682 quarantined rows. The default split actually fits on **58,991** TRAIN
rows, validates on 19,664, and exposes 19,663 internal-test rows. The whole
100,000-row source is therefore not the estimator's training-row count.

All three native candidates complete. Model texts, native validation defaults,
mapping, features, TRAIN vocabulary and 41 protected files freeze at
**2026-11-04 05:07:44.006430 UTC**. Fresh final generation starts at
**05:07:44.020132 UTC**. The new draw has 100,000 rows: **98,346 accepted and
scored, 1,654 quarantined**, with no foreign-currency exclusions or unresolved
accepted labels. Its 1,911 positive rows use the existing synthetic label rule.
No threshold or feature is changed after final evaluation.

| Fixed candidate on the same 98,346 final rows | Precision | Recall | F1 | TP | FP | FN | TN |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **LightGBM — validation-selected** | **98.56%** | **89.38%** | **93.74%** | 1,708 | 25 | 203 | 96,410 |
| Logistic Regression | 93.22% | 84.20% | 88.48% | 1,609 | 117 | 302 | 96,318 |
| Isolation Forest | 18.97% | 41.60% | 26.06% | 795 | 3,396 | 1,116 | 93,039 |
| Amount-only diagnostic | 5.46% | 82.68% | 10.25% | 1,580 | 27,337 | 331 | 69,098 |

LightGBM average precision is 0.96797; its fixed score threshold is
0.6927640305. Class-weighted outputs are not calibrated fraud probabilities.
It flags 1,733 transactions (1.76%); these are **transaction flags**, not an
observed count of grouped account/day application alerts. No final draw is
uploaded to the live application.

The amount-only cutoff is IDR 551,556, chosen from validation. Relative to this
diagnostic, LightGBM adds 128 overall TP and reduces reference FP by 27,312.
However, it loses ATO coverage (231→78) and P05 coverage (25→0). Thus this
comparison is **not** a precision improvement that preserves each pattern's
coverage. The diagnostic never becomes a selectable Lab winner.

The two fresh draws share 2,861 account-identifier tokens from the same
templates. They are not joined as a customer's history and do not establish
independent real identities. All final scores are finite. Native Logistic
Regression numerical warnings remain in the log; its stored parameters are
finite and fixed scores are independently verified.

## Small amounts, patterns and false positives

| Synthetic positive pattern, transaction rows | LightGBM detected / positives | Recall |
|---|---:|---:|
| P04 high velocity | 1,630 / 1,652 | 98.67% |
| ATO account takeover | 78 / 234 | 33.33% |
| P05 dormant reactivation | 0 / 25 | 0% |

P04 supplies **86.45% of final positive rows**. Global recall therefore hides
the two weak pattern slices. The quantity here is row recall, not the fraction
of scenario entities/cases caught.

For amounts **≤TRAIN q25 = IDR78,189**, there are 26,345 accepted final rows:
TP17 / FP5 / FN1 / TN26,322, precision **77.27%**, recall **94.44%**. All **18
positives are P04**, so this supports small-value P04 behaviour only, with a
small denominator. It does not assess low-value ATO or P05. The amount-only
diagnostic catches 0/18 in this slice; the selected model has learned something
beyond the large-value cutoff on these particular synthetic cases.

Of LightGBM's 25 reference FP, nine are on Fraud-pattern lookalike/boundary
rows and sixteen on other background/unlabelled rows. It flags none of the
2,547 clean-control transaction rows. The other-pattern injected rows are
separately reported as **reference negatives**, not asserted to be crime-free.
All amount-quartile/history slices and negative-cohort denominators are in the
full JSON; no empty slice is assigned perfect recall.

**Post-final explanation, not a retuning step:** all requested scenario
placements (minimum 25) were fulfilled in both complete draws, but the current
chronological split produces the following actual learning coverage:

| Partition | Rows | P04 positive rows | ATO positive rows | P05 positive rows |
|---|---:|---:|---:|---:|
| TRAIN | 58,991 | 581 | **0** | **0** |
| Validation | 19,664 | 410 | 88 | 3 |
| Exposed development test | 19,663 | 561 | 150 | 22 |

A minimum scenario count in a whole source file does not guarantee that each
pattern appears in its earliest TRAIN period. Validation examples can choose
a threshold, but do not teach these supervised fits missing positive patterns.
This diagnosis was made **after** the final evaluation and pinned to the
unchanged original report hash; it did not select a new model or threshold.

The actual accepted dates also need qualification. TRAIN ends
26 July 2026 06:48:42 UTC and validation ends 27 August 02:55:52 UTC. The new
final spans 29 September 2026 17:05:51 UTC to 3 April 2027 14:20:36 UTC, so
every final row is later than TRAIN and validation. Generated valid date
defects extend the development test to 6 October 2026; **3,846 final rows**
fall within that exposed test's calendar range. The two complete files are
therefore not strictly calendar-disjoint. No after-the-fact filtering is done.

## Limits and unresolved integration issues

- The current Lab UI/response reveals candidate test outcomes before a final
  choice, and multiple threshold saves can reveal more test points. Validation
  still determines the automatic recommendation, but the claimed one-time
  independent final gate is not enforced by the application. This audit's fresh
  post-freeze draw is separate from that UI workflow.
- Lab dataset mapping is mutable after training. Runs do not snapshot it;
  saving an older run and monitoring can read the dataset's current mapping.
  This audit fixes and hashes its training mapping instead of changing the app.
- The Lab monitoring path and the CLI `run_detection` call P02 plus models;
  the general `/detection/run` endpoint calls P01–P10 plus models. A successful
  monitoring upload does not establish that all ten patterns were evaluated.
- Timestamp ties can cross the lab's TRAIN/validation/test row boundaries.
  P01's relative-history branch can also depend on reference ordering of rows
  with identical timestamps. These remain implementation limitations, not
  claims repaired by this test stage.
- The Lab is transaction-level FRAUD only, with synchronous requests and a
  500,000-row / 50 MiB file limit. AML account-week Lab training is not built.
  New synthetic FRAUD metrics cannot be compared directly with the previous
  BAF application, public card or AMLSim benchmark metrics.
- Historical flagged scores are not filtered by current ACTIVE model state
  when generating model alerts; model retirement/suspension therefore needs
  a separate lifecycle fix. Evidence currently stores file metadata/hash rather
  than retrievable file bytes, and the investigation UI remains partial.

## Reproduction and evidence

From `backend/`, choose a **new, non-existing ignored output directory**:

```bash
.venv/bin/python -m app.scripts.audit_current_lab \
  --out ml_data/lab_quality_NEW_RUN

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  --noconftest -p no:cacheprovider tests/test_lab_quality_audit.py -q
```

The source/final seeds, dates and current Lab defaults are fixed in the audit
script. Repeating this command uses the same synthetic draws and is a
**reproduction**, not a new unseen final. Once these results are exposed, later
model/data repairs require development data and a separately frozen new final;
do not train on this final and continue calling it independent.

The actual local run is `backend/ml_data/lab_quality_s20261104`. Original
report SHA-256:
`7f78a400abae76561b653bf233a7eba68d2a16a6e86d1b56924a699eb00394d1`.
Independent review verifies all **41 original protected hashes**, the frozen
manifest's own checksum, and **20 creation-time final-artifact hashes**.
It recomputes 52 slice results, 12 pattern metrics and 24 negative-cohort
results from cached scores exactly. Source split counts are independently
recomputed as an explicitly post-final diagnosis.

Public JSON files contain aggregate evidence and omit machine-specific project
path prefixes. Embedded hashes identify the original local artifacts, not the
sanitized mirror bytes. Raw/row-level source, final and model artifacts remain
gitignored; old datasets, generator code and model evidence are unchanged.

Technical commands were the regular and slow backend pytest selections on an
isolated PostgreSQL 16.15 cluster, plus `npm run build` in `frontend/`.
`current_quality_20261104_checks.json` records the precise commands and log
checksums. This stage does not claim browser QA, live service testing or an
ACTIVE model artifact assessment.

## Decisions and capstone summary

**Own decisions:** use the current Lab defaults unchanged; distinguish the
exposed internal test from a fresh post-freeze draw; add a frozen amount-only
diagnostic; keep missing/foreign-currency/quarantined rows explicit; report
pattern and small-slice denominators; retain weak ATO/P05 coverage honestly;
use temporary PostgreSQL instead of the live DB. These are AS-16 measurement
choices, not a generator modification, model approval or real-crime claim.

**Priority after this test:** restore ATO/P05 positive coverage in the actual
training period using an authorised data plan while keeping chronological
evaluation; add validation coverage checks per pattern; enforce one-time
final evaluation and immutable training mappings; align monitoring with the
complete detection runner. These changes are not implemented in this stage.

**Ringkasan capstone:** Aplikasi lulus 843 tes backend dan build frontend,
ditambah 18 tes integritas audit. Pipeline Lab saat ini diuji dengan sumber
100 ribu baris dan 100 ribu baris simulasi baru setelah pembekuan. LightGBM
mencapai precision 98,56% dan recall 89,38% pada 98.346 baris uji valid, tetapi
recall ATO hanya 33,33% dan P05 0%. Bagian TRAIN tidak memiliki contoh positif
kedua pola tersebut. Hasil menunjukkan kemampuan pada simulasi P04, belum
membuktikan cakupan fraud menyeluruh, deteksi pencucian uang, atau kualitas
produksi; model hasil audit tetap offline DRAFT.
