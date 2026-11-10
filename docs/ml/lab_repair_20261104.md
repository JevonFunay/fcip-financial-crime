# Lab evaluation, training coverage and monitoring repair — 4 November 2026

## Outcome

The authorised repair is implemented and the three-model retraining completed
once. **913 distinct backend tests pass**, including five slow tests, and the
frontend production build passes. No live application DB or registry is changed.

A separate synthetic source has **250,000 rows and 150,000 actual TRAIN rows**,
with positive, benign and low-value ATO/P04/P05 support in every partition.
The validation-selected LightGBM scores a newly generated **100,000-row final**
at **68.49% precision / 97.39% recall**. The original pinned LightGBM on those
same rows scores **36.99% / 13.91%**. Low-value recall increases from 2/1,077
to 1,054/1,077. This repairs the missing-pattern coverage found in the baseline.

**The precision/workload gate still fails:** false positives rise 499→944;
104/108 benign lookalike episodes receive at least one flag. These synthetic
intervention labels are not confirmed fraud or money laundering. All new models
remain **offline DRAFT**, with no registration, submission or activation.

[Full results and original hash expectations](lab_repair_20261104_results.json),
[technical checks](lab_repair_20261104_checks.json) and
[independent verification](lab_repair_20261104_verification.json) accompany this
report. The [preceding baseline](current_quality_20261104.md) remains intact.

## Approved scope and retained historical evidence

Jevon approved the proposed evaluation lock, TRAIN-coverage repair, retraining
and monitoring alignment with “gas”. AS-17–19 record the choices. The separate
AML account-week **Lab** remains a later stage; historical AML research is not
converted into a current serving model by this work.

The new generator is `lab_behavioral_coverage_v1`, separate from enterprise
generator **1.4.0**. The latter, `raw_contract.py`, shared Tiny/Small, raw
sources (including the downloaded unlabelled USD dataset), old generated data
and old model artifacts are unchanged. This run trains on **new simulated Lab
data**, not Kaggle observations labelled as crime. Amounts are explicitly
synthetic IDR in the existing Lab upload layout, with no USD conversion.

The original baseline report SHA remains
`7f78a400abae76561b653bf233a7eba68d2a16a6e86d1b56924a699eb00394d1`.
Before runtime edits, `git archive 0382821` was retained additively at
`backend/ml_data/lab_quality_s20261104/source_snapshot_0382821/`.
Its tar SHA is `bcd5924db75d7683cf7957ce1a92244fa35023eab147a18f2b6287bb320a98af`.
All 11 original protected application-code files reproduce their original
hashes in that archive; the pinned native model, contract and preprocessing
also match the original report. The current trainer deliberately supersedes
that archived source, so old reproduction belongs to the archive/0382821.

## Immutable mapping and one-time final evaluation

- Migration `0014_lab_evaluation_lock` adds a source-wide protocol keyed by exact
  uploaded-file SHA. First training freezes mapping, date interpretation and
  TRAIN share; duplicate uploads adopt that protocol. Run snapshots and model
  candidates are immutable, with source/code/mapping/split/artifact hashes.
- Chronological boundaries keep whole availability-time groups together.
  Day-only observations, including dates without supplied time, become available
  at day end. Supplied minute precision keeps its timestamp group.
  Supervised fits refuse validation-positive categories absent from TRAIN.
- Comparisons, category curves and the threshold slider expose **validation
  only**. TEST prevalence, positive counts, rankings and candidate curves are
  withheld. The slider counts transaction flags, not grouped application alerts.
- Saving locks one run/model/threshold under a database lock **before**
  calculating that choice's TEST metrics. Concurrent alternative saves/refits
  and duplicate-upload bypasses are refused; an exact repeat save is idempotent.
  Choice, final result and draft commit together in the serialized transaction.
  The UI retains the selected final result after reload.
- Legacy exposed results/artifacts remain stored unchanged, are marked
  `LEGACY_EXPOSED`, and have their TEST comparison metrics redacted. Existing
  saved versions are not relabelled as new blind evaluations.

This is an application evaluation guard. The source owner can inspect raw
labels, validation describes source-level data quality, and re-encoded copies
are not recognised as duplicates. It is not a claim of globally unseen labels.
No numerical production precision/recall acceptance target is invented.

## Chronological coverage dataset

Fixed source: **250,000 rows**, seed `2026110403`, simulated local calendar
2024-01-01 through 2025-12-20 (+07:00), 720 days. All rows normalise; no quarantine,
missing labels or currency exclusions. Exact second timestamps are simulated,
not reconstructed from an incomplete public source. File size **30,826,536 bytes**.

| Source partition | Rows | ATO positive / small positive | P04 positive / small positive | P05 positive / small positive |
|---|---:|---:|---:|---:|
| TRAIN | 150,000 | 1,098 / 555 | 1,088 / 561 | 1,101 / 550 |
| Validation | 50,000 | 245 / 127 | 238 / 119 | 251 / 130 |
| Internal TEST, label-support metadata only | 50,000 | 85 / 45 | 86 / 44 | 79 / 39 |

Small means ≤**source TRAIN q25 IDR61,504.655**, fixed for every final arm.
Each partition/pattern also contains benign and small benign event rows:

| Partition | ATO benign / small benign | P04 benign / small benign | P05 benign / small benign |
|---|---:|---:|---:|
| TRAIN | 613 / 301 | 544 / 277 | 373 / 187 |
| Validation | 131 / 65 | 142 / 74 | 64 / 29 |
| Internal TEST | 27 / 19 | 41 / 7 | 48 / 25 |

The fixed generator calendar provides monthly positive/benign episodes rather
than selecting partitions by labels. Missing coverage fails without retry,
repartitioning or target changes. Source internal TEST **label-support metadata
is inspected and reported**; no source internal TEST prediction metric, ranking
or curve is computed in this offline benchmark.

Per-account histories vary from 32–64 records with irregular spacing; background
activity is heavy-tailed. Positive and benign events share channels, amounts,
device novelty and initial observable prefixes. About 25% of paired scenarios
are exact observable counterfactuals (source 53/216 pairs, final 26/108): intent
differs while current model inputs match. IDs are seed-namespaced random tokens;
channel support is label-independent and entirely known to the old reference.

P05 has 100–120 days of dormancy and 16–24 event transactions. Its low-ticket
episodes satisfy FRD §8.5's **≥90 days and ≥5 transactions within 7 days** branch,
not the IDR25M branch. Their per-row label is the assumed intervention episode,
not a per-row rule output. A prior-90-day amount baseline can be unavailable
after ≥100 days of dormancy despite older account history; this is disclosed.

Whole timestamps are isolated, but **96 source scenarios share history across
partitions**; none has event rows in multiple source partitions in this draw.
This is chronological future-observation evaluation, not independent scenarios
or customers. New sources still share simulation recipes.

## Validation selection and fresh final results

The current eight feature groups, TRAIN-only vocabulary and model seed
`20261026` are retained. All three candidates fit on 150,000 TRAIN rows. Selection
is native highest validation AP; each candidate's native validation max-F1 fixes
its threshold. Amount-only and pinned old LightGBM are diagnostic arms, excluded
from model selection. No final result changes the winner or threshold.

| Candidate | Validation AP | Validation precision / recall at fixed default | Fixed threshold |
|---|---:|---:|---:|
| Logistic Regression | 0.753871 | 70.29% / 89.92% | 0.9569533766 |
| **LightGBM, selected** | **0.789724** | **69.18% / 97.55%** | **0.9003599144** |
| Isolation Forest | 0.215876 | 23.72% / 45.91% | 0.5868645269 |

Thirty-eight original expected hashes plus the frozen manifest's own SHA seal
source files, code, mapping, vocabulary, model texts/contracts and selection.
Freeze: **2026-11-04 07:12:19.162155 UTC**; final generation begins at
**07:12:19.182099 UTC**. Final has seed `2026110404`, 100,000 clean rows,
**12,330,766 bytes**, 2,107 positive / 97,893 negative, no unresolved targets.
Observed dates are **2027-01-03 10:45:24 UTC–2028-12-20 16:50:04 UTC**, strictly
after the entire new source and old reference source (ending October 2026).
Nine final-artifact creation hashes are checked before/after evaluation.

| Same fresh synthetic final, fixed arms | Precision | Recall | TP / FP / FN / TN | Flags / 10k |
|---|---:|---:|---|---:|
| **LightGBM, validation-selected** | **68.49%** | **97.39%** | **2052 / 944 / 55 / 96949** | **299.6** |
| Logistic Regression | 70.02% | 88.23% | 1859 / 796 / 248 / 97097 | 265.5 |
| Isolation Forest | 31.22% | 42.86% | 903 / 1989 / 1204 / 95904 | 289.2 |
| Pinned previous LightGBM | 36.99% | 13.91% | 293 / 499 / 1814 / 97394 | 79.2 |
| Validation-fixed amount-only diagnostic | 2.11% | 99.91% | 2105 / 97889 / 2 / 4 | 9999.4 |

The repaired LightGBM gains **31.50 percentage points of precision and 1,759 TP**
against the old model on identical rows. Total, small-value and all pattern TP
guards pass, but **FP increases by 445**, so the strict matched coverage/workload
guard **fails**. Logistic Regression's higher final precision does not replace
the pre-final winner. The old 98.56% baseline used another final population;
subtracting it from 68.49% would not measure model improvement or deterioration.

| Selected LightGBM pattern | Positive rows caught / total | Recall | Small positive rows caught / total | Small recall |
|---|---:|---:|---:|---:|
| ATO | 673 / 697 | 96.56% | 348 / 357 | 97.48% |
| P04 | 684 / 715 | 95.66% | 350 / 364 | 96.15% |
| P05 | 695 / 695 | 100.00% | 356 / 356 | 100.00% |

Small slice: 24,606 rows, 1,077 positives, **TP1054 / FP493 / FN23 / TN23036**,
precision **68.13%**, recall **97.86%**. Pinned old model catches **2/1,077**,
with one FP. This now assesses all three low-value patterns, unlike the old
P04-only low-value denominator. It does not establish unseen-typology robustness.

All 108 positive episodes have some event flagged (36 per pattern). Median first
flag delay is 90 s ATO, 90 s P04, 0 s P05; delay is conditional on detection.
Only 12/36 ATO, 5/36 P04 and 36/36 P05 episodes have **every** declared event row
flagged. **104/108 benign episodes also have a flag** (34/36, 34/36, 36/36).
Any-hit episode recall is therefore not evidence of precise discrimination.

Of 944 FP, **498/511 exact benign event rows** and **443/485 other benign event
rows** are flagged; ordinary background contributes 3/86,621 and history
0/10,276. The model separates scripted episodes from background well, but
poorly distinguishes positive intent from close legitimate controls. Its top
native feature importance is the prior-observation gap (~78.5%); the behavioral
signal is not verified criminal intent. Exact counterfactuals alone imply a
conservative **80.48% precision ceiling at 100% recall** in this final, ignoring
other ambiguous prefixes. That bound applies to full recall only, not every
threshold. More dummy volume cannot recover information absent from inputs.

## Monitoring alignment

Lab uploads and `run_detection` CLI now call the same P01–P10 registry used by
the regular endpoint, followed by active-model scoring unless `--rules-only`.
Rule summary counts sum all executed patterns. Historical flagged scores from
RETIRED/SUSPENDED/DRAFT model versions cannot create new model alerts; historical
scores and existing alerts are retained.

Lab monitoring loads and verifies the **approved version's mapping snapshot**,
not a mutable dataset mapping. Incompatible dataset references or legacy cards
without full mapping/date/currency provenance return 409 without changing their
model state. A new reviewed version is required for that workflow. Existing
canonical DB scoring is retained. No live model/rule switch is changed here.

## Verification and limitations

Full regular run: **887 PASS** (132.64 s); all five slow checks **PASS**
(111.82 s). One subsequently added generator compatibility check and 20 new
repair-audit checks bring distinct coverage to **913**. Independent final
generator/audit preflight: **35 PASS** (9.46 s), overlapping checks counted once.
Private PostgreSQL **16.15**, UNIX socket only, peer authentication, disposable
`fcip_test`; servers stop cleanly. An initial setup-only attempt hit macOS's
socket-path length limit before test bodies; a short `/tmp` cluster resolves it.
No application DB or live Compose migration is accessed.

Frontend `npm run build` passes TypeScript and Vite. No current browser visual QA
or 250k UI request-latency measurement is claimed. Actual offline protocol-to-
report duration is **42.68 s**; it is not production throughput. Python3.11.18,
NumPy2.1.3, pandas2.2.3, PyArrow18.1.0, scikit-learn1.5.2, LightGBM4.5.0.

The retained actual log contains Logistic Regression NumPy matrix warnings
(divide by zero/overflow/invalid). All final score columns and stored LR
parameters are finite. Independent review reproduces the same warnings on
**all-zero bounded matrix inputs** with correct output in local Apple Accelerate
BLAS. A non-BLAS sum/expit calculation matches final probabilities within
8.88e-16 and every fixed-threshold flag. Saved inputs/coefficients and gradients
are bounded; every transient optimizer iterate cannot be reconstructed. This
supports a local floating-status warning artifact, not a claim that warnings
can always be ignored. Logs are retained and no post-final refit occurs.

Independent review rebuilds all source/final features with frozen vocabularies,
replays every saved model's final scores exactly, and recomputes **65 slices**,
pattern/cohort/episode counts. It verifies all 38 new frozen, nine final-creation,
11 archived original-code and 30 unchanged original data/model file expectations.
All five arms score identical final references; all 511 exact counterfactual
event pairs receive identical scores in each arm. Intentional unknown-feature
NaNs remain unknown; there are no infinities or nonfinite final scores.

Main limitations: synthetic prevalence and intent labels; recurring recipes;
account-history overlap; exact/partial benign ambiguity; no adjudicated public
fraud or AML target; uncalibrated class-weighted supervised scores; no live
ACTIVE artifact or grouped alert-volume benchmark; no automated promotion.

## Reproduction, decisions and capstone summary

Local ignored output: `backend/ml_data/lab_repair_s20261104/`. Upload-ready files:

- `source/transactions.csv` — 250,000 labelled rows for Lab step 1.
- `final/transactions.csv` — 100,000 rows; targets are ignored during monitoring.
  This final is now exposed and can demonstrate the workflow, not a new blind test.

Do not upload audit sidecars. Current logins remain `analyst@fcip.internal`,
`mlro@fcip.internal`, `dataops@fcip.internal`, `triage@fcip.internal`, seeded dev
password `DevPassword123!`; Analyst selects/submits, a different MLRO approves,
Data Ops monitors, and Triage reviews alert dispositions. Ordinary review does
not turn the synthetic scenario labels into real crime ground truth.

The completed one-shot command, from `backend/`, was:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m app.scripts.audit_lab_repair \
  --out ml_data/lab_repair_s20261104
```

It refuses an existing destination. Reproduction needs the retained original
baseline report/model/preprocessing/archive at the paths above; the report's
hash expectations are not rebased to today's code. In the same checkout use a
fresh output path and preserve the declared constants. The original report has
absolute-path provenance: moving to another machine/checkout needs an explicit
audited relocation procedure, not merely copying files or replacing hashes.
Public results render repository-relative paths for reading while retaining the
unchanged local report's original checksum. Recreating the exposed seeds is
reproducibility, not another unseen result. For normal
app deployment `docker compose up --build` applies migration0014; this stage
does not run it on the live DB. Large generated/model files remain ignored.

**Decisions I took myself (for Jevon to check):** AS-17 exact-byte lock and
availability ties; AS-18 separate generator, fixed calendar/history/low-ticket
support and overlapping controls; AS-19 fixed native selection, future final,
matched old reference, hash sealing, private DB verification and monitoring
alignment. All numeric research choices are assumptions, not FRD acceptance
thresholds. FRD §8.5 supports the P05 count/dormancy design; FR-405/BR-401.3
support versioned provenance and interpretable advisory scores.

**Next evidence needed:** mentor review of legitimate lookalikes and intended
intervention labels, a declared precision/recall/workload target, and additional
available context that distinguishes those cases. A further precision study
must select on new development/validation data and seal another final first.
AML account-week Lab remains a separate stage; no AML-quality claim follows
from these transaction labels.

**Ringkasan capstone:** Evaluasi Lab diperbaiki dengan penguncian pemetaan dan
pembagian waktu, pemilihan model pada validasi, serta pembukaan satu hasil uji
final setelah pilihan dikunci. Dataset sintetis baru berisi 250.000 transaksi
dengan 150.000 data TRAIN, mencakup ATO/P04/P05 bernilai kecil dan kontrol sah.
Pada 100.000 transaksi final yang sama, precision/recall LightGBM meningkat
dari 36,99%/13,91% menjadi 68,49%/97,39%; recall nominal kecil 97,86%.
Namun false positive naik dari 499 menjadi 944 dan 104 dari 108 episode sah
ikut tertandai, sehingga guard ketat belum terpenuhi. Model tetap offline DRAFT;
hasil ini menunjukkan cakupan simulasi, bukan bukti kejahatan atau akurasi AML.
