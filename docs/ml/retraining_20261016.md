# Controlled unsupervised retraining — 16 October 2026

## Outcome

**Training is complete. There is a partial improvement in synthetic precision and F1, but useful high-precision crime detection has not been
established. All artefacts remain DRAFT research outputs and are not registered or activated.**

On the same fresh final simulation, selected AML precision rises from **3.52% to 9.38%**, while recall falls from **63.79% to 38.79%**. Only
**45/74 (60.81%)** of reference true positives remain, below the experiment's 80% validation retention objective. Its small-amount retention
also falls to **8/12 (66.67%)**. The validation coverage guard therefore **did not generalize for AML**.

Selected FRAUD precision rises from **3.28% to 6.71%**, with recall **22.56% versus 22.98%**; the paired uncertainty interval does not
establish a recall improvement. FRAUD retains total and small-amount reference coverage, but absolute precision and recall remain low. These
figures evaluate deliberately simulated stress interventions, not confirmed fraud or money laundering.

Exact metrics and provenance are in the [compact JSON](retraining_20261016.json). The [complete validation candidate
CSV](retraining_20261016_candidates.csv) contains all 64 unit/threshold choices, eligibility decisions and exclusions. No candidate was
reselected or retuned after viewing the fresh final result.

## Protocol and data provenance

The source remains the supplied 50,000-row, 495-account, 15-column `bank_transactions_data_2_augmented_clean_2.csv`, SHA-256
`c4c44ae11496e31f6dea5126322463af719c0f175c6c2f7f0abbc15f96575b2a`. USD was confirmed by Jevon on 14 October; no FX or IDR conversion
occurs. Publisher ancestry and the source's prior augmentation are unverified. There are no bank-verified target labels. **43,750 rows
(87.5%) are date-only**, 6,250 have minute precision, and all original timezones are unknown. Generated exact UTC clocks are simulations,
not recovered source timestamps.

The original account split is unchanged: **346 training / 74 validation / 75 test parents**. Models use 35,662 original training
transactions, alone or with the existing 1,000,000 generated training transactions. Both validation arms are fixed: 6,809 unlabelled
original transactions and the same 200,000 generated validation transactions. Source parents were split before augmentation.

The controlled full-feature pair uses the same `behavioral_v1` registry, 100 Isolation Forest trees, seed `20261015`, 256 samples/tree and
inclusive review cutoff `score >= 0.5`. Each pipeline learns vocabulary and imputation from its own training observations. Thus this
compares complete training pipelines; changing training composition also changes learned references and does not isolate a single causal
mechanism. Both mixed full-reference model texts were verified byte-identical to their 15 October `aml_if_v1`/`fraud_if_v1` texts.

An Isolation Forest score describes isolation relative to the fitted distribution; it is not a calibrated crime probability. FCIP's
inclusive boundary at 0.5 is its own review convention, not a claim of identical sklearn binary `predict` decisions at equality. See the
[sklearn 1.5 outlier-detection guide](https://scikit-learn.org/1.5/modules/outlier_detection.html).

The complete research grid comprises **eight configurations and 16 fitted models**: full features with source-only/mixed training at 256
samples/tree; portable features with source-only/mixed/balanced-origin training at 256 or 2,048 samples/tree. Full models use the 0.5
cutoff. Each portable model has 0.5 plus its training-score 95th, 97th, 99th and 99.5th percentile cutoffs: **32 threshold choices per unit,
64 in total**. Quantiles are training review-load references, not fraud prevalence estimates.

For balanced-origin variants, vocabulary is fitted on equal numbers of source and randomly sampled generated training observations. Complete
account histories are built before equal source/generated feature-row sampling for model fitting; raw event histories are not thinned before
feature construction. Models fit without targets. All 16 fits finish before validation answers are parsed. **Synthetic validation labels do
inform candidate and threshold selection**; this is unsupervised estimator fitting with label-informed research selection.

Selection was declared in the protocol before candidate evaluation. A choice must retain at least `ceil(0.8 × reference TP)` overall and on
small amounts, and must not exceed the mixed full reference's unlabelled-source flag count. Eligible choices are ranked by validation F1,
then average precision, then fewer source flags, then lexical identifier. The **80% retention parameter is a research assumption, not an
FRD/TRD threshold or a production approval gate**.

The common evaluation small-amount cutoff is **USD 84.4325**, the original source **training** amount 25th percentile. It is identical for
every candidate. FRAUD uses transaction amount; AML uses the account-week's mean amount. This differs intentionally from 15 October's
mixed-training cutoff USD 83.44; small-slice counts across those reports are not the same cohort.

## Source-only versus mixed training

Source flag counts below are **review load**, not measured false positives. Neither original-source precision nor recall can be estimated
without labels.

| Unit / full-feature training | Original-source validation flagged / total | Generated validation flagged / total |
|---|---:|---:|
| AML source-only | 1,151 / 5,764 (19.97%) | 20,391 / 20,417 (99.87%) |
| AML mixed | 2,549 / 5,764 (44.22%) | 2,127 / 20,417 (10.42%) |
| FRAUD source-only | 1,502 / 6,809 (22.06%) | 199,971 / 200,000 (99.99%) |
| FRAUD mixed | 6,383 / 6,809 (93.74%) | 13,338 / 200,000 (6.67%) |

| Generated validation unit / training | TP | FP | FN | TN | Precision | Recall | F1 | AP |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| AML source-only | 120 | 20,271 | 2 | 24 | 0.59% | 98.36% | 0.01170 | 0.03924 |
| AML mixed | 66 | 2,061 | 56 | 18,234 | 3.10% | 54.10% | 0.05869 | 0.09574 |
| FRAUD source-only | 1,963 | 198,008 | 0 | 29 | 0.98% | 100.00% | 0.01944 | 0.00890 |
| FRAUD mixed | 308 | 13,030 | 1,655 | 185,007 | 2.31% | 15.69% | 0.04026 | 0.01903 |

Source-only training lowers original-source review load but calls almost the entire generated validation distribution anomalous. This
demonstrates a large distribution mismatch under the fixed reference representation/cutoff; it does not prove either pipeline detects real
crime better. Mixing generated data helps fit that simulated distribution while sharply increasing original source review load. More
generated volume is not independent source evidence.

A label-free diagnostic measured source training account spans at a median 2,152 days and gaps at 13 days. A descriptive sample of the first
64 complete generated training accounts has median span 178.38 days and gap 0.335 days. Those origin measurements support a
history-density/precision mismatch hypothesis. That diagnostic's **portable missingness figures belong to `behavioral_research_views_v1`**,
not the final v2 representation, and are not used here as v2 measurements or causal feature-importance evidence.

## Validation-directed feature experiments

The final portable representation is `behavioral_research_views_v2_boundary_ties`: **15 AML features and 18 FRAUD features**. It removes
explicit source precision, UTC-hour, lifetime count and history-age risk inputs; keeps those observation limits in the underlying
availability/gating logic; and uses amount, balance and activity comparisons against the newest prior 30 observations. At a tied
availability boundary it includes the **entire boundary group**, so a same-day date-only transaction cannot enter or leave the reference
merely because its identifier is renamed. At least 30 prior observations and 30 days of observed history remain required.

Rates use actual observed calendar-day exposure, supporting sparse source histories rather than requiring 30 transactions inside the
previous 90 days. The last-30 reference may span years: this is a research representation, **not an implementation of a 90-day FR-401
baseline**. The view also combines context novelty and excess login attempts on the same observation before aggregation. It does not infer a
counterparty graph, ownership, MCC, country or running ledger. Missing values remain unavailable before model imputation; explicit metadata
removal does not guarantee all origin effects disappear.

Both units select **mixed training + portable features + 100 trees with 2,048 samples/tree**. AML selects training-score q95,
**0.4852335819357355**; FRAUD selects **0.5**. AML fits 134,067 account-weeks (29,628 source and 104,439 generated); FRAUD fits 1,035,662
transactions (35,662 source and 1,000,000 generated). The selected changes combine representation, tree sample size and cutoff; their
combined outcome is not a single-factor feature effect.

| Selected validation unit | TP | FP | FN | TN | Precision | Recall | F1 | AP |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| AML | 53 | 699 | 69 | 19,596 | 7.05% | 43.44% | 0.12128 | 0.17892 |
| FRAUD | 374 | 7,153 | 1,589 | 190,884 | 4.97% | 19.05% | 0.07882 | 0.03898 |

AML must retain at least 53 total and two small-amount validation TPs; it achieves exactly 53 and two. FRAUD requires 247 and 132, achieving
374 and 160. The small slice has only four positive AML validation weeks, versus 612 positive FRAUD validation transactions. Selected
original-source review loads are **788/5,764 (13.67%) for AML** and **1,974/6,809 (28.99%) for FRAUD**. Those lower workloads do not
establish correct source detection. Ten AML and eleven FRAUD threshold choices are eligible; every exclusion is retained in the candidate
CSV. Balanced-origin alternatives were considered but not selected under this fixed grid and rule.

## Frozen configuration and fresh final test

Models, cards, protocol, selection, validation results, input checksums and executed helper code were frozen at **2026-10-16 08:13:39.103675
UTC**. Generation of a new 200,000-row final simulation with seed **2026101601** began at **08:13:39.348089 UTC**. Evaluation completed at
**08:14:01.290099 UTC**. All **52 frozen files**, plus the protocol's initial input/code digests, were independently checked unchanged
during report preparation. `pipeline.py` is included because its imported loading, label-alignment and metric helpers are executed by the
experiment.

The final draw has 800 simulated account proxies, 1,919 stress-labelled rows and 4,153 lookalike rows, over **2026-04-20 to 2026-10-17 UTC,
end exclusive**. All 200,000 observations normalize successfully; none is quarantined. The draw uses the original frozen training fit
profile. Its 75 source test parents and recipe families are already known from prior research. This is a new stochastic stress draw, **not a
new independently labelled real-account population**. The original source test set is not re-evaluated in this experiment.

| Frozen evidence | SHA-256 |
|---|---|
| Protocol | `412a3ffb8c8e8d1224f725ec4000375af9fcfac8f06b1a5662745e84c22c0006` |
| Final manifest | `46e2617bc4463b0479d5d66aa084300d5009d6ffe9296808fe7db5ff5be0e0ba` |
| Final observations | `50b11e553e67c981ac12f9c3a3fdc243b1a5847b9da806d671bd028b1af3199b` |
| Selected AML model | `97d6020ef1bdc73cd8a45b1e8aee36040aff6b592b318d81b985b59912fbbde6` |
| Selected AML card | `87c08a39e5fa365015e69ad7ee771cd43709e4a501e2db824c21612bece80a15` |
| Selected FRAUD model | `a276fd2a635878ccabc8a1b7df5151068ea890b404ebb8852a6df2700ab27b7e` |
| Selected FRAUD card | `4772b868b7d338e549f32edc0f8515716a6cae23cf9c55ce8c6760a1ece91cfa` |

The following comparisons score **the same fresh draw**. Different seeds' headline results from 15 October are not substituted as this
reference.

| Final unit / model | Units flagged / total | TP | FP | FN | TN | Precision | Recall | F1 | AP |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| AML reference | 2,100 / 20,398 | 74 | 2,026 | 42 | 18,256 | 3.52% | 63.79% | 0.06679 | 0.10380 |
| AML selected | 480 / 20,398 | 45 | 435 | 71 | 19,847 | 9.38% | 38.79% | 0.15101 | 0.22574 |
| FRAUD reference | 13,201 / 200,000 | 433 | 12,768 | 1,486 | 185,313 | 3.28% | 22.56% | 0.05728 | 0.02405 |
| FRAUD selected | 6,577 / 200,000 | 441 | 6,136 | 1,478 | 191,945 | 6.71% | 22.98% | 0.10381 | 0.05804 |

FP/TN are relative to assumed synthetic background/lookalike labels, not independently verified legitimate activity. AML labels an
account-week positive when it contains a stress-labelled transaction; FRAUD labels the individual transaction. These units cannot be
combined as a count of crime cases.

| Final small-amount unit / model | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| AML reference | 12 | 696 | 4 | 1,200 | 1.69% | 75.00% | 0.03315 |
| AML selected | 8 | 105 | 8 | 1,791 | 7.08% | 50.00% | 0.12403 |
| FRAUD reference | 251 | 7,081 | 529 | 48,995 | 3.42% | 32.18% | 0.06188 |
| FRAUD selected | 237 | 2,615 | 543 | 53,461 | 8.31% | 30.38% | 0.13051 |

There are only 16 positive final AML small-amount weeks and 780 positive FRAUD small transactions. FRAUD retains **441/433 (101.85%)** total
and **237/251 (94.42%)** small-amount reference TPs. AML retains only 60.81% and 66.67%. The 80% rule was a **validation selection
constraint**, not a guarantee on final data; its final failure is reported without changing selection or cutoffs.

Paired percentile bootstrap intervals use **75 original source-parent clusters, 300 replicates, 95% intervals**. All clones/rows from a
sampled parent stay together. Precision/recall deltas below are percentage points, selected minus reference; F1 deltas use the 0–1 scale.

| Unit | Precision delta interval | Recall delta interval | F1 delta interval |
|---|---:|---:|---:|
| AML | +3.27 to +9.05 pp | −33.93 to −16.73 pp | +0.04540 to +0.12438 |
| FRAUD | +1.51 to +5.38 pp | −6.95 to +6.90 pp | +0.01856 to +0.07194 |

Under this simulation, precision and F1 gains are supported by these paired intervals; AML recall clearly worsens and FRAUD recall
improvement remains inconclusive. The intervals condition on the generator, parent sample and assumed labels. They do not account for
incorrect real-world labels or establish coverage for independent bank populations.

## Limitations and decisions

The unchanged generator still makes activity denser and time precision more complete than the public source. Its dormant-reactivation stress
uses a terminal 35–65-day gap without an equivalent legitimate dormant-reactivation control. That construction can make the recipe easier to
distinguish and must not be sold as realistic crime identification. The five stress recipes and overlapping lookalikes are research
interventions; AML network patterns remain unobservable without genuine counterparty/ownership relationships.

Decisions taken within the authorized experiment were to preserve all original inputs/models; compare source-only and mixed pipelines; fit
all candidates before parsing validation answers; select under the declared coverage/load/F1 rule; include boundary ties symmetrically;
freeze every relevant artefact before the new draw; and retain all research outputs as DRAFT. The portable view and 80% retention setting
are explicit research choices, not mentor ratification or changes to FR-405/CF-05. Research cards are **not registration-compatible**; no
database registration, model promotion or `ML_SCORE` activation occurred.

Focused verification passed **42 new tests** (14 workflow, eight integrity, 20 feature tests) and **62 related existing tests: 104 in
total**. This is a portable focused test result, not a new complete backend/database regression run. Integrity checks cover prior-manifest
mismatches, mutations during fit, executed helper-code hashes, no answer access during fitting, and final-draw ordering. Point-in-time
checks cover future additions, unknown intraday order and renaming tied historical identifiers.

Learned preprocessing stays confined to training data, while candidate selection uses validation and the final draw is consumed after
freeze, following the [sklearn 1.5 leakage/model-selection guidance](https://scikit-learn.org/1.5/common_pitfalls.html#data-leakage). Any
further selection informed by these final results requires a separately declared experiment and another fresh holdout. Lower review load or
a higher synthetic F1 alone does not justify production promotion.

Local detailed evidence remains in `backend/ml_data/retraining_s20261016/` and `backend/ml_models/retraining_s20261016/`. The committed
JSON/CSV preserve the result, complete validation grid and relevant hashes without host-specific absolute paths, credentials or
source-account lists.

## Ringkasan capstone

Pelatihan ulang selesai dengan 16 model dan 64 pilihan model/ambang yang dievaluasi menggunakan protokol tetap. Estimator tetap dilatih
tanpa label; label validasi sintetis digunakan secara terbuka untuk memilih konfigurasi. Model terpilih memakai fitur perilaku yang lebih
sesuai untuk riwayat jarang, tetapi hasilnya baru merupakan perbaikan sebagian. Pada simulasi uji baru 200.000 transaksi, presisi AML
meningkat dari 3,52% menjadi 9,38%, sementara recall turun dari 63,79% menjadi 38,79% dan syarat retensi 80% tidak bertahan. Presisi FRAUD
meningkat dari 3,28% menjadi 6,71%; recall 22,56% menjadi 22,98% belum menunjukkan kenaikan yang meyakinkan. Sumber asli belum memiliki
label fraud terverifikasi, sehingga akurasi fraud nyata belum dapat dinilai. Semua hasil tetap DRAFT penelitian; tidak ada aktivasi model
atau `ML_SCORE`.
