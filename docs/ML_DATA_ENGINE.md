# Behavioral data and unsupervised ML guide

15 October 2026. The reusable normalization, generation, training and evaluation
flow is implemented. The current models remain **DRAFT** because benchmark
quality is poor; this guide does not recommend activation.

**Latest 20 October stage:** [full-network precision research](ml/precision_20261020.md)
adds separate AML temporal and OpenML FRAUD adapters, TRAIN-only peers, strict
matched comparisons, and a 300-case blinded offline review tool. Precision gains
do not retain final coverage; all candidates remain offline DRAFT. Human review
is PENDING and cannot yet establish anomaly agreement or crime precision.

**Latest 16 October follow-up:** the [source-cadence robustness report](ml/robustness_20261016.md)
documents the validation error audit, a separate repaired generator,
recall-preserving grouped validation, frozen fresh final evaluation and an
independent published sender–receiver AML benchmark. It supersedes the research
procedure below for new experiments; existing app/demo artifacts remain the
15 October versions. Research thresholds are not approved operating points.

**16 October research update:** the [controlled retraining report](ml/retraining_20261016.md)
compares source-only/mixed/equal-origin training, repaired observation-reference
features and a new frozen final draw. Precision improves partially and AML
coverage falls. Those candidates stay offline DRAFT and do not replace the
application versions or the manual-upload instructions in this guide.

## Source and meaning of the data

`backend/sample_data/raw/bank_transactions_data_2_augmented_clean_2.csv` has
50,000 rows, 15 columns and 495 account identifiers, **without fraud labels**.
Jevon confirmed USD on 14 October; amounts are retained without FX conversion.
The `kaggle_bank_v1` profile name does not verify the Kaggle publisher, original
augmentation recipe or whether the observations represent real banking activity.

43,750 timestamps (87.5%) contain only a date; 6,250 contain a minute-level time.
The source timezone is unknown for all 50,000 rows. UTC is only their storage
carrier: normalization preserves day precision and does not reconstruct missing
times. Generated exact UTC times are explicitly **simulated**.

Account/customer records created for this flow are observation proxies, not
resolved real identities. Source age/occupation vary within an account; stable
generated demographics are simulation choices. There is no actual counterparty
network, merchant ownership, MCC or inferred country. Pass-through scenarios
are alternating-direction account proxies, not evidence of linked fund flows.

## Reusable flow

1. **Normalize with an explicit profile.**
   [`standardize.py`](../backend/app/ml/behavioral/standardize.py) accepts the
   source profile, `fcip_canonical_v1`, or a versioned JSON mapping. It validates
   observations, preserves rejected originals in quarantine and keeps unknown
   currency as `XXX`. A different bank needs an explicit mapping and currency/date
   policy; this is not automatic interpretation of arbitrary banking schemas.
2. **Partition source accounts before augmentation.**
   [`generate.py`](../backend/app/ml/behavioral/generate.py) makes disjoint
   70%/15%/15% account partitions with split seed `20261014`. Its global fit
   profile uses source training accounts only; heldout generation conditions on
   actual heldout parent observations. `lineage.csv` records the parent account,
   transaction and source ordinal. Each split has its own 180-day simulated
   window. Original-source evaluation uses its original dates, not these windows.
3. **Fit unsupervised models offline.**
   [`features.py`](../backend/app/ml/behavioral/features.py) builds AML
   account-week and FRAUD transaction features with prior-history comparisons.
   Vocabulary, numeric medians and preprocessing use training observations only.
   [`model.py`](../backend/app/ml/behavioral/model.py) fits Isolation Forest
   without a target, storing a validated JSON model rather than pickle.
   Scores measure isolation, not fraud probability. The fixed review cutoff is
   `score >= 0.5`; labels do not select features, trees or the threshold.
4. **Evaluate after freezing.**
   [`pipeline.py`](../backend/app/ml/behavioral/pipeline.py) fits from 1,000,000
   generated rows plus 35,662 original source-training rows: **1,035,662 input
   observations**, giving 134,067 AML units and 1,035,662 FRAUD units. Validation
   has 200,000 generated rows. Both models and diagnostic cards are frozen and
   hashed before generating the new 200,000-row test set. The report records
   freeze/test-start timestamps and verifies unchanged hashes after evaluation.

`transactions.csv` contains canonical observations only. `source_shape.csv` is
the 15-column companion. `ground_truth.csv` contains generated stress/background/
lookalike assumptions for evaluation only; it is never a training target or web
upload. Background label 0 is an assumption, not verified legitimacy. Recipes
include low-value bursts and legitimate small-payment lookalikes; volume does
not create additional independent real observations.

## Current result and artifacts

[`backend/ml_data/behavioral_s20261015/evaluation.json`](../backend/ml_data/behavioral_s20261015/evaluation.json)
records seeds `20261015`/`20261016`/`20261017`, provenance, frozen hashes and final
metrics. Each dataset has a manifest and frozen `fit_profile.json`; immutable
model files/cards are under `backend/ml_models/behavioral_s20261015/`.

| Final generated test unit | Precision | Recall |
|---|---:|---:|
| AML account-week | 3.13% | 62.28% |
| FRAUD transaction | 2.25% | 18.64% |
| FRAUD small-amount transaction | 2.48% | 32.76% |

Small amount here means at most the training 25th percentile, **USD 83.44**;
this is descriptive, not an AML reporting threshold. These results show high
review burden and missed injected events. They measure only our synthetic
recipes and assumptions, **not real-world financial-crime accuracy**. Original
source precision/recall remain unavailable because it has no verified targets.

## Web upload and roles

The intended demo handoff has **zero loaded transactions**, 800 prepared test
account proxies, and both `aml_if_v1`/`fraud_if_v1` registered **DRAFT**. Loading
accounts alone does not ingest transactions, fit models or activate scoring.
Use existing users: Data Ops uploads; Triage makes authorized human alert
decisions. There is no implemented `ROLE_ADMIN` or MLRO approval workflow.

Run commands below from `backend/`, with the intended database configured. The
master-data command writes anonymous account proxies; `--ingest` would also load
transactions and is deliberately omitted from this web-upload workflow.

```bash
.venv/bin/python -m app.scripts.load_behavioral_dataset \
  --csv ml_data/behavioral_s20261015/test_web/transactions_001.csv \
  --profile fcip_canonical_v1 --master-data-only

.venv/bin/python -m app.scripts.register_models \
  --models-dir ml_models/behavioral_s20261015 \
  --version aml_if_v1 --version fraud_if_v1 --state DRAFT
```

As Data Ops, select
`backend/ml_data/behavioral_s20261015/test_web/transactions_001.csv` in the web
upload. It has 200,000 rows and 38,293,500 bytes (38.29 MB), under the unchanged
50 MiB upload cap. Do not select labels, lineage or manifests. Inspect accepted/
quarantined counts; upload never retrains a model. DRAFT models do not provide
active ML scoring. `ML_SCORE` remains DRAFT pending CF-05, so model scores do
not independently create alerts. P02 is an IDR-only rule and does not convert
these USD observations to IDR or apply its IDR threshold to them. A later model
activation requires a separate quality/governance decision (FR-405).

For a different input, use `normalize_csv(path, profile)` and inspect its
`report`/`quarantine` before exporting `frame[CANONICAL_COLUMNS]`. The loader can
also export accepted canonical rows with `--canonical-out PATH`; it prepares
account proxies in the configured database even without `--ingest`.

## Reproduce in new output roots

Preserve the frozen run. The exact full-run CLI is below with **new** output
roots; existing dataset/model directories are refused. The current CLI has no
model-version flag: `pipeline.MODEL_NAMES` currently fixes `aml_if_v1` and
`fraud_if_v1`. A new candidate intended for registration needs new immutable
version identifiers in a reviewed code change as well as new output roots;
never overwrite or relabel the frozen cards to reuse an existing version.
Generator or shared-data changes still follow repository approval rules.

```bash
.venv/bin/python -m app.ml.behavioral.pipeline \
  --source sample_data/raw/bank_transactions_data_2_augmented_clean_2.csv \
  --out ml_data/behavioral_NEW_RUN \
  --models-out ml_models/behavioral_NEW_RUN \
  --train-rows 1000000 --validation-rows 200000 --test-rows 200000 \
  --seed 20261015 --reference-date 2026-10-15 --estimators 100 --max-samples 256
```

This runs offline and does not register or activate models. Keep the generated
final test untouched after evaluation; future revisions need a separate run and
an honestly described evaluation boundary.

## Run the controlled research experiment

`app.ml.behavioral.experiments` reuses the immutable training/validation CSVs;
it never reads the old final test or training answers. It records a protocol
before fitting, selects with synthetic validation labels after all target-free
fits finish, freezes all code/model/card/input/selection hashes and generates
a new final draw only then. The portable representation is separately versioned
and not automatically compatible with the application registry. No DB writes,
registration, promotion or UI changes occur.

From `backend/`, reproduce only in fresh roots:

```bash
.venv/bin/python -m app.ml.behavioral.experiments \
  --source sample_data/raw/bank_transactions_data_2_augmented_clean_2.csv \
  --previous-run ml_data/behavioral_s20261015 \
  --out ml_data/retraining_NEW_RUN \
  --models-out ml_models/retraining_NEW_RUN \
  --test-rows 200000 --seed 20261015 --final-seed 2026101601 \
  --reference-date 2026-10-16 --estimators 100 --max-samples 256 2048 \
  --bootstrap-replicates 300
```

The command above reproduces the recorded stochastic draw; it does not create
another independent test. For new model selection, use an untouched evaluation
design/data and record the repeated-validation/known-parent limits. Existing
output roots and old split seeds are refused. Keep the original run and its
negative findings intact; changing the final cutoff after seeing its results
would turn that set into development data.

## Run the source-cadence robustness follow-up

The new `generate_v2` keeps each heldout source parent's actual calendar span,
empirical day-gap shape, day/minute precision and unknown timezone. It adds
ordinary shared hiatuses and matched legitimate controls, with ordinary
history and follow-up around every supported intervention. These splits are
account-disjoint and **not chronological holdouts**. Some benign/stress pairs
are observationally identical; labels alone cannot supply missing crime facts.

`robustness` fits six unsupervised models (three fixed configurations per unit)
on 1,000,000 new generated TRAIN rows plus 35,662 original TRAIN observations.
Validation uses 200,000 new rows and three heldout source-parent groups, keeping
all descendants together. Labels are used after all fits for selection, never
for fitting medians, vocabulary or estimators. A candidate must preserve the
reference TP count in every declared slice and avoid increasing synthetic FP
or source review flags; if none passes, the reference is retained. Selection
does not guarantee the same individual cases are retained or that final
coverage will generalize. The final 200,000 rows are generated after freezing.

From `backend/`, using fresh, non-existing roots:

```bash
.venv/bin/python -m app.ml.behavioral.robustness \
  --source sample_data/raw/bank_transactions_data_2_augmented_clean_2.csv \
  --out ml_data/robustness_NEW_RUN \
  --models-out ml_models/robustness_NEW_RUN \
  --train-rows 1000000 --validation-rows 200000 --test-rows 200000 \
  --seed 2026101620 --final-seed 2026101602 --estimators 100 \
  --bootstrap-replicates 300
```

These seeds reproduce the recorded draw, not another independent evaluation.
Keep every prior final result unchanged. Extra simulations from known source
parents/recipe families do not become independent real-crime evidence.

The separate published IBM AMLSim benchmark uses actual simulator sender/
receiver columns and graph-only inputs; it does not attach imagined edges to
the Kaggle source. Its entire published graph is connected, so the declared
account/time partition drops edges and reports partial pattern retention.
It uses fixed score 0.5 and TRAIN q99, with no target-based threshold selection.

```bash
.venv/bin/python -m app.ml.behavioral.aml_network_benchmark \
  --download --raw ml_data/aml_network_SOURCE/raw \
  --out ml_data/aml_network_NEW_RUN --seed 20261016 --estimators 100
```

The downloader pins the archive checksum and preserves provenance. The reference
repository is Apache-2.0; the external archive has no separately stated data
licence in its README/wiki. Raw external data stays local and ignored. Account
and 30-day graph windows are a batch benchmark, not proof of real-time warning.
All research remains DRAFT with no DB registration, approval or CF-05 change.

## Run the 20 October precision experiments

The sources and targets are distinct. AMLSim has supplied synthetic SAR patterns
and real simulator edges. OpenML 1597 has publisher-supplied historical card Class
references and opaque PCA inputs. Neither source is merged into Kaggle rows or
misrepresented as an independently adjudicated FCIP outcome. These adapters
reuse the target-free JSON Isolation Forest fitter; they are offline research,
not serving registrations. Check source licences/provenance in the report.

From `backend/`, with new non-existing output roots:

```bash
.venv/bin/python -m app.ml.behavioral.aml_temporal \
  --raw ml_data/aml_network_ibm_amlsim_v21_20261016/raw \
  --out ml_data/aml_temporal_NEW_RUN --seed 20261020 --estimators 100

.venv/bin/python -m app.ml.behavioral.aml_temporal_selection \
  --run ml_data/aml_temporal_NEW_RUN --out ml_data/aml_temporal_NEW_RUN_fp_audit

.venv/bin/python -m app.ml.behavioral.fraud_public \
  --source ml_data/fraud_public_s20261020/source/creditcard_1597_v1.parquet \
  --metadata ml_data/fraud_public_s20261020/source/openml_1597.json \
  --out ml_data/fraud_public_NEW_RUN --seed 20261020 --estimators 100
```

The additive AML FP-cap audit preserves the frozen original selector and checks
stored validation without another target read/refit. The original run already
satisfies the cap for every option; its postfreeze audit is not called a new
predeclared experiment. Reproducing the same public time splits does not create
an unseen final. The FRAUD authoritative integrity run uses the same final as
its first attempt; all models/profiles/scores are identical and the reproduction
note records the correction. Do not tune on these final outcomes.

The FRAUD Parquet is obtained from the official metadata's `parquet_url` at
`https://www.openml.org/api/v1/json/data/1597`. This run's source SHA-256 is
`b7efcb35a428bbe22347a05d2437d9177bab07ce61e51214a17bec584ad9496d`.
Its amounts are unknown source units, not inferred USD/EUR. Keep raw files local.
Only our added preprocessing is verifiably TRAIN-only; upstream PCA scope is unknown.

## Review the original unlabelled source

The prepared reviewer page is
`backend/ml_data/review_s20261020_ui/review.html`. From the project root on macOS:

```bash
open backend/ml_data/review_s20261020_ui/review.html
```

Enter a reviewer ID, inspect the case and prior evidence, choose a status and
save a substantive reason. Use INSUFFICIENT_EVIDENCE when the available fields
cannot support a judgement. **Click CSV export regularly; closing/reloading the
tab loses in-memory answers.** It has no network or browser storage. All 300 rows
are exported, including PENDING cases; crime outcome remains UNKNOWN.

Give a reviewer the HTML alone, or the visible packet/template/instructions.
Do not supply sealed_* files or model metadata until review is complete: file
separation is logical only. Never replace absent labels with normal or generate
completed reviews from a model score. The currently prepared 300 cases are all
pending, and no human/crime precision has been measured.

Import the downloaded responses from `backend/` into a NEW output directory;
replace the response path with the actual CSV you saved:

```bash
.venv/bin/python -m app.ml.behavioral.review_labels import \
  --packet ml_data/review_s20261020 \
  --responses ~/Downloads/fcip_review_responses.csv \
  --out ml_data/review_REVIEWED_NEW
```

Completed rows need reviewer ID, reason and offset-aware timestamp. PENDING
metadata stays blank; only UNKNOWN crime outcomes are accepted. The import
reports design-weighted observational-anomaly agreement, unresolved coverage
and conservative sampling uncertainty. It never confirms fraud/AML, updates
app alerts or fits an estimator. Keep reviewed validation separate from future
final evidence. To regenerate a packet/page in new roots:

```bash
.venv/bin/python -m app.ml.behavioral.review_labels export \
  --source sample_data/raw/bank_transactions_data_2_augmented_clean_2.csv \
  --models ml_models/robustness_s20261016/mixed_full_256 \
  --frozen ml_data/robustness_s20261016/frozen.json \
  --out ml_data/review_NEW --sample-size 300 --seed 20261020

.venv/bin/python -m app.ml.behavioral.review_html \
  --packet ml_data/review_NEW --out ml_data/review_NEW_ui
```
