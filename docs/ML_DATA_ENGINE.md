# Behavioral data and unsupervised ML guide

15 October 2026. The reusable normalization, generation, training and evaluation
flow is implemented. The current models remain **DRAFT** because benchmark
quality is poor; this guide does not recommend activation.

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
