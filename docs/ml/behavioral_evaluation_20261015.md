# Behavioral model evaluation — 15 October 2026

## Quality outcome

**DRAFT research result; high-precision detection quality has not been achieved.**
The frozen baseline has final synthetic-test precision of **3.13% for AML** and
**2.25% for FRAUD**. It flags **42.22% of unlabelled source test account-weeks**
and **92.16% of source test transactions**. These source flags measure review
load; the source has no verified targets from which to estimate precision,
recall, false-positive rate or detection accuracy. Neither this baseline nor
the three later validation-only prototypes is promoted by this report.
The prepared operational demo has zero transactions and 800 fresh account
proxies; previous model versions are RETIRED and the new versions remain DRAFT.

The normalization, label-free fit, immutable model storage and isolated runtime
checks work. That technical result does not establish useful detection quality.
All reported precision/recall values below use explicit **synthetic intervention
labels versus assumed generated background/lookalikes**, never verified crime.
The compact [JSON companion](behavioral_evaluation_20261015.json) preserves the
exact counts and unrounded metrics.

## Data and provenance

The supplied source is `bank_transactions_data_2_augmented_clean_2.csv`: 50,000
rows, 15 columns and 495 account identifiers. All 50,000 rows pass the explicit
`kaggle_bank_v1` profile; none is quarantined. Its SHA-256 is
`c4c44ae11496e31f6dea5126322463af719c0f175c6c2f7f0abbc15f96575b2a`.
The original publisher, ancestry and augmentation recipe are **not independently
verified**. The filename is not evidence of legitimate or fraudulent outcomes.

Jevon confirmed USD on 14 October 2026. Amounts remain USD without FX conversion
or IDR valuation. **43,750 source rows (87.5%) are day-only**, 6,250 supply minute
precision, and every source timezone is unknown. UTC is a storage carrier for
those source wall-clock values; no source intraday order, UTC hour or geographic
timezone is invented. Generated exact UTC timestamps are simulation choices.

Original accounts are partitioned before augmentation, with split seed
`20261014`: **346 train / 74 validation / 75 test** accounts, containing
**35,662 / 6,809 / 7,529** original source rows. Source parents are disjoint
between splits. Clones sharing a parent remain dependent within a split.

| Generated split | Rows | Simulated accounts | Stress-labelled rows | Lookalike rows | UTC simulation window, end exclusive | Seed |
|---|---:|---:|---:|---:|---|---:|
| Train | 1,000,000 | 4,000 | 10,250 | 19,827 | 2025-04-24 to 2025-10-21 | 20261015 |
| Validation | 200,000 | 800 | 1,963 | 4,014 | 2025-10-21 to 2026-04-19 | 20261016 |
| Final test | 200,000 | 800 | 1,974 | 4,045 | 2026-04-19 to 2026-10-16 | 20261017 |

Generator version is `behavioral_source_conditioned_v1.0.0`. Global generator
statistics use original source training accounts only. Stable demographics,
ordinary changes and stress/lookalike recipe rates are documented simulation
choices, not FRD/TRD thresholds or measured crime prevalence. Larger generated
volume does not create additional independent real observations.

## Frozen fit and final-test results

The unsupervised fit combines the 35,662 original training rows and 1,000,000
generated training rows: **1,035,662 input transactions**, yielding **134,067
AML account-week units** and **1,035,662 FRAUD transaction units**. `behavioral_v1`
has 32 AML and 35 FRAUD features. Vocabulary, amount references and numeric
imputation medians are fitted on training observations only. Neither labels nor
scenario sidecars enter model fitting; identifiers and demographics are not
numeric risk features. Historical comparisons require 30 prior observations
and 30 days of observed history, with unavailable baselines retained as null.

Both baselines use 100 Isolation Forest trees, `max_samples=256`, seed
`20261015`, and `contamination='auto'`. FCIP scores are `-score_samples` and
review flags use the **inclusive cutoff `score >= 0.5`**. The 0.5 reference
comes from sklearn's automatic offset; it is not fraud prevalence or a calibrated
fraud probability. Equality is an FCIP boundary convention, so matching numeric
scores does not claim identical sklearn binary `predict` decisions at exactly
0.5. See the [sklearn 1.5 IsolationForest documentation](https://scikit-learn.org/1.5/modules/generated/sklearn.ensemble.IsolationForest.html).

Validation diagnostics were added to the initial cards after fit, without
changing estimators, features, preprocessing or thresholds. Both model text and
cards were frozen at **2026-10-15 12:54:27.284238 UTC**. Final-test generation
started at **12:54:27.284259 UTC**, after that freeze; the pipeline completed at
**12:54:43.516679 UTC**. Both model and card checksums remain unchanged through
final evaluation. The final-test manifest SHA-256 is
`94816e04cfc4430eb224452b296fee57ae1330cb92d83883f6bab3f7635c3269`.

| Frozen artefact | SHA-256 |
|---|---|
| `aml_if_v1/model.txt` | `7c43fa14f5b49c588a1dbb6336554f82867fc5687c85a87f98cff72814019a39` |
| `aml_if_v1/model_card.json` | `1e07036a58d67ba36ed5832634c94c19ae55bd50245221458ea86677da12d8b1` |
| `fraud_if_v1/model.txt` | `fc18bc5511a701bf5c1d1553f8e2731435d2cb8c84c369fe66614ac4ff475d6a` |
| `fraud_if_v1/model_card.json` | `836c280a4bb0912275bb045c0e8bc2f18d2e75d46a0c080ca4d6e3258ac422b5` |

AML units end Sunday at 23:59:59 UTC on the storage/simulation clock. A weekly
synthetic label is positive when any stress-labelled transaction falls in that
account-week; FRAUD uses each transaction's label. The units are not resolved
people or confirmed AML/fraud cases.

| Split / unit | Units | Flagged | TP | FP | FN | TN | Precision | Recall | AP | ROC AUC |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Validation AML | 20,417 | 2,127 | 66 | 2,061 | 56 | 18,234 | 3.10% | 54.10% | 0.09574 | 0.84036 |
| Validation FRAUD | 200,000 | 13,338 | 308 | 13,030 | 1,655 | 185,007 | 2.31% | 15.69% | 0.01903 | 0.70100 |
| Final-test AML | 20,840 | 2,270 | 71 | 2,199 | 43 | 18,527 | 3.13% | 62.28% | 0.12971 | 0.86555 |
| Final-test FRAUD | 200,000 | 16,336 | 368 | 15,968 | 1,606 | 182,058 | 2.25% | 18.64% | 0.01797 | 0.68841 |

FP/TN mean the generator's assumed zero-labelled background/lookalikes, whose
legitimacy has not been independently established. AP is average precision.
Ranking metrics do not overcome the poor precision at the frozen review cutoff.

The small-amount slice uses the frozen training 25th percentile, **USD 83.44**,
as a descriptive amount reference, not a reporting/legal threshold. FRAUD tests
individual amount; AML tests the **whole week's mean amount**. AML has only four
positive validation weeks and eighteen positive final-test weeks in this slice.

| Small-amount split / unit | TP | FP | FN | TN | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|
| Validation AML | 2 | 291 | 2 | 606 | 0.68% | 50.00% |
| Validation FRAUD | 163 | 5,001 | 443 | 42,437 | 3.16% | 26.90% |
| Final-test AML | 12 | 816 | 6 | 1,086 | 1.45% | 66.67% |
| Final-test FRAUD | 210 | 8,251 | 431 | 47,048 | 2.48% | 32.76% |

The baseline small-amount reporting was corrected at **2026-10-15
13:16:17.721958 UTC**: a Decimal/binary-float comparison had excluded rows
exactly at USD 83.44. The inclusive slice now uses matching numeric
representations. Final-test FRAUD small-amount recall is exactly **210/641**.
No model, card, data, score, fitted parameter or threshold changed; this was a
reporting correction, not a refit or a threshold selection.

Scenario-level detection counts a scenario when any of its labelled rows/weekly
units is flagged. It is a different denominator from unit recall above.

| Final-test simulated recipe | AML detected / total | FRAUD detected / total |
|---|---:|---:|
| `card_testing_burst` | 20 / 20 | 16 / 20 |
| `session_change` | 19 / 19 | 18 / 19 |
| `dormant_reactivation` | 14 / 19 | 19 / 19 |
| `micro_splitting` | 6 / 19 | 7 / 19 |
| `pass_through_proxy` | 5 / 19 | 8 / 19 |

## Unlabelled source review load

| Original heldout source | AML account-weeks flagged / total | FRAUD transactions flagged / total |
|---|---:|---:|
| Validation, 6,809 transactions | 2,549 / 5,764 (44.22%) | 6,383 / 6,809 (93.74%) |
| Test, 7,529 transactions | 2,612 / 6,187 (42.22%) | 6,939 / 7,529 (92.16%) |

Calling these rows fraudulent or counting unflagged rows as legitimate would
invent labels. Exploratory validation diagnostics suggest source shift: median
history length is **108.42 versus 1,100 days** for AML training/source-validation
units, and **93.37 versus 1,119 days** for FRAUD. Median prior 30-day count is
**23 versus 1** for AML and **44 versus 1** for FRAUD. Source date precision also
differs sharply from generated exact-time simulations. These comparisons
support a shift hypothesis; they do not identify causal reasons or prove which
source flags are wrong. The diagnostic ablation summary retains only each
row's top eight median-replacement differences over the first 100 flagged
source-validation rows; it is not population feature importance or SHAP.

## Validation-only quality prototypes

Three exploratory alternatives were fitted without targets and evaluated on
training and validation only. Their work **did not open the final-test data or
results**, and none was promoted. This is separate from the baseline's already
completed frozen final-test evaluation above. Validation informed diagnosis;
these alternatives do not inherit an untouched final-test result.

- **A — `context_if_256_auto`:** contextual feature subset, Isolation Forest
  with 256 samples/tree, inclusive cutoff 0.5.
- **B — `context_if_2048_train995`:** contextual subset, 2,048 samples/tree,
  cutoff at the training-score 99.5th percentile.
- **C — `multiview_train995`:** training empirical ranks over behavioural
  views, combined using the second-most-extreme view; training 99.5th-percentile
  cutoff.

B/C's approximately 0.5% training review budget is a label-free experimental
reference selected before their validation metrics, not a crime-prevalence
estimate or a precision-maximizing label threshold. Their scores/cutoffs have
different meanings from the frozen baseline's score and cannot be substituted
into its registry entry.

| Validation-only unit / candidate | Source validation flagged / total | Generated validation flagged | TP | FP | FN | TN | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| AML A | 1,830 / 5,764 (31.75%) | 3,019 | 65 | 2,954 | 57 | 17,341 | 2.15% | 53.28% |
| AML B | 134 / 5,764 (2.32%) | 24 | 5 | 19 | 117 | 20,276 | 20.83% | 4.10% |
| AML C | 122 / 5,764 (2.12%) | 39 | 9 | 30 | 113 | 20,265 | 23.08% | 7.38% |
| FRAUD A | 255 / 6,809 (3.75%) | 30,965 | 1,314 | 29,651 | 649 | 168,386 | 4.24% | 66.94% |
| FRAUD B | 0 / 6,809 (0.00%) | 1,360 | 210 | 1,150 | 1,753 | 196,887 | 15.44% | 10.70% |
| FRAUD C | 2 / 6,809 (0.03%) | 977 | 147 | 830 | 1,816 | 197,207 | 15.05% | 7.49% |

Generated validation denominators remain 20,417 AML units and 200,000 FRAUD
units. B/C reduce source review load but miss most labelled synthetic stress.
Zero source flags from FRAUD B is not evidence of correct source detection.
On small-amount validation positives, AML A detects 3/4 and B/C 1/4; FRAUD A
detects 427/606, B 72/606 and C 45/606. No candidate establishes high precision
with useful stress coverage, and the tiny AML slice prevents a strong claim.
These candidate small-amount totals are descriptive counts. Their full
small-slice boundary metrics were not recomputed, so candidate small-slice
FP/TN, AP, AUC, precision and recall are omitted from the companion JSON.

## Limits and reproducibility

An isolated runtime verification ingested **200,000 / 200,000** final-test rows,
with zero quarantine and 800 technical account proxies. Every stored observed
metadata field matched the offline normalized input. Runtime scored 20,840 AML
units and 200,000 FRAUD units, flagging 2,270 and 16,336 respectively, matching
offline counts. It stored those flags, raised no P02 or model alerts, and skipped
zero rows for currency. The isolated run took **62.48 seconds**, including
**9.5 seconds** for ML scoring/explanations; the live database was untouched.
This demonstrates pipeline and serving consistency, not quality approval.

No counterparty network, merchant ownership, MCC, country or running balance is
recovered from the source. A pass-through recipe is an alternating-direction
account proxy. Source demographics vary between observations; simulated stable
profiles do not repair that ancestry. Model explanations are per-feature
median-replacement score differences, neither additive TreeSHAP nor causal
findings. Model outputs remain advisory observations; these metric surfaces
retain the research/simulation caveat (TRD §10.6, FRD L-01).

Train-only learned preprocessing and identical transforms at inference follow
the [sklearn 1.5 data-leakage guidance](https://scikit-learn.org/1.5/common_pitfalls.html#data-leakage).
Any future feature/threshold proposal needs a separately versioned experiment
and a fresh untouched final holdout after selection; repeated validation reuse
does not become independent evidence.

Local evidence is in `backend/ml_data/behavioral_s20261015/evaluation.json`,
the three split manifests and source profile;
`backend/local_backups/behavioral_replace_20261015/runtime_verification_v1.json`;
and the exploratory `/tmp/fcip_quality_validation_report.json`. Generated data,
models and private runtime evidence remain local; the companion JSON commits
their relevant results and evidence hashes without source-account lists or
credentials. This report records no new training, threshold tuning, promotion
or database mutation.

**Ringkasan capstone:** Pipeline normalisasi, pelatihan tanpa label, pengujian
setelah model dibekukan, dan konsistensi hasil offline/aplikasi sudah
terverifikasi. Kualitas deteksi belum memenuhi tujuan presisi tinggi: presisi
uji sintetis AML 3,13% dan FRAUD 2,25%, sedangkan akurasi pada sumber tanpa label
belum dapat diukur. Ketiga prototipe tambahan hanya dievaluasi pada validasi
dan belum dipromosikan; model baru tetap DRAFT.
