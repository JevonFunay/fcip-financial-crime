# Recall-preserving research and AML network benchmark — 16 October 2026

## Outcome

All five authorised steps were executed. **No improved eligible model was
found.** Both AML and FRAUD retain the new v2 full-view reference; all 20
non-reference model/threshold options fail the declared recall/workload guard.
The data/measurement pipeline is more faithful and rejects misleading precision
trades, but the models remain **offline DRAFT and unsuitable for deployment**.
The separate graph benchmark also fails to establish useful AML quality.

The run generated **1,000,000 TRAIN + 200,000 validation + 200,000 fresh final
rows**, then fitted six target-free models. Combined with 35,662 original
source TRAIN rows, each FRAUD fit uses 1,035,662 observations; AML fits use
843,129 account-weeks. No DB registration, promotion, CF-05 change or app/demo
mutation occurred. Detailed [results and hashes](robustness_20261016.json) and
[all 22 validation options](robustness_20261016_candidates.csv) are preserved.

## Validation error audit

The audit uses only the prior run's **validation** observations, answers and
frozen scores. It never opens prior final data/results. Full counts, disjoint
account-week taxonomy and descriptive slices are in the [audit](validation_error_audit_20261016.md),
[JSON](validation_error_audit_20261016.json) and [CSV](validation_error_audit_20261016.csv).
Slice associations are not causal feature importance.

- AML dormant-reactivation detection fell from **14/30 to 3/30** positive
  validation weeks. **68/69** selected-model AML misses already had a supported
  portable history; adding baseline availability alone cannot explain them.
- FRAUD micro-splitting detected **18/394**, pass-through proxy **30/351** and
  card-testing **67/440** transactions. These all lost TP against the reference.
- New context with at least two login attempts flagged **321 FRAUD observations:
  31 TP and 290 FP**. This motivates testing relative behaviour without
  unconditional context/login inputs, rather than assuming novelty proves crime.
- Prior generated validation had exact-second timestamps throughout. It could
  not establish behaviour on the source's mostly date-only observations.

## Source-aligned generator repair

`behavioral_source_cadence_v2.0.0` is a new module; v1, previous datasets/models
and their negative results are immutable. The source remains the same 50,000
unlabelled USD observations, SHA-256
`c4c44ae11496e31f6dea5126322463af719c0f175c6c2f7f0abbc15f96575b2a`.
Account partitions before augmentation are still 346/74/75 source parents.

The generator bootstraps each actual parent's empirical day gaps and complete
observations within that parent's actual multi-year calendar support. It keeps
observed day/minute precision and unknown timezone. It removes the 180-day
compression/exact-UTC-clock shortcut. Ordinary hiatuses occur independently of
scenario labels; all event accounts retain ordinary prehistory and follow-up.
Global reference statistics use source TRAIN only; heldout parent conditioning
is local and recorded row-by-row in lineage. The generated 15-column companion
and canonical input normalize to the same observations.

The declared 12% stress/24% lookalike account allocation, 20% shared-hiatus
probability, small-amount bands and perturbations are **simulation assumptions,
not FRD thresholds or observed crime prevalence**. Account-heldout calendar
spans overlap: this is **not a chronological holdout**.

Three stress/control pairs are observationally equivalent conditional on the
same parent and random draw: **dormant reactivation / legitimate dormant
reactivation; card testing / shopping; micro-splitting / repeated small
payments**. Their labels express assumed intent that these columns cannot
identify. More training rows cannot recover absent distinguishing facts.
All positive stress remains in evaluation, including date-only transactions
that cannot establish intraday timing. Metrics measure these assumptions,
not verified fraud or real-world legitimacy.

## Grouped validation and selection protocol

The protocol is written before fitting. Three fixed configurations per unit
use the same mixed TRAIN histories and train-only vocabulary/medians:

| Candidate | Representation | Isolation Forest subsample |
|---|---|---:|
| Reference | Original full view | 256 |
| Portable | Minimum-30 causal prior observation reference | 2,048 |
| Relative | Eight relative/direction inputs; adds AML own-rate-normalized prior gap | 2,048 |

Each fit uses 100 trees, without targets. Relative inputs omit unconditional
amount/balance, login/context and duration signals. Availability ties, the
30-prior-observation/30-observed-day gate and actual calendar exposure remain
unchanged. This separately versioned experimental view does not replace the
FR-401 baseline or serving model. The audit informed this feature choice.

There are **six fitted models and 22 model/threshold validation options**.
The reference uses score 0.5; alternatives try 0.5 and TRAIN-score quantiles
.90/.95/.97/.99. Synthetic validation labels are opened only after all fits.
Selection is therefore **label-informed selection of unsupervised estimators**,
not target-free model selection.

Three fixed heldout source-parent groups keep every clone of a parent together.
These are three validation groups after one TRAIN-only fit, not three k-fold
refits. Eligibility requires TP counts at least equal to the new v2 reference
overall, at source-TRAIN-q25 low amounts (**USD 84.4325**), within every parent
group and its low-amount subset, per stress recipe, and within FRAUD clock
slices. It also requires no increase in overall simulated FP or unlabelled
source flags. Ranking uses precision, AP, lower source review flags and ID.
The reference is the fallback; no improvement is a permitted result.

These constraints preserve empirical recall counts, not necessarily the same
individual hits. They do not guarantee future recall. Unlabelled source flags
are workload only: source precision/recall remain unknown. A new 200,000-row
final draw follows frozen input/code/model/card/protocol/selection hashes;
checks reuse initial hashes and reject mutation during fitting/selection.

## Frozen fresh final evaluation

Selection retained only `mixed_full_256__auto` for both units. For example,
validation AML relative q99 raises precision to 7.27% but drops recall from
73.66% to 27.07%; FRAUD relative q99 raises precision to 16.89% but drops recall
from 60.18% to 16.92%. Both are rejected. These validation numbers were never
used to retune the new final after it was generated.

The 35 input/code/model/card/selection checksums were frozen at
**08:52:55.849364 UTC**; final generation started at **08:52:55.973062 UTC**
and evaluation completed at **08:53:23.686445 UTC**. Final seed is 2026101602,
distinct from the earlier final seeds. The original run's 52 frozen files still
match. New final source parents are 75 known heldout parents, yielding 1,940
simulated proxy accounts; clones are not independent new customers.

| New v2 final unit | TP | FP | FN | TN | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|
| AML, 160,270 account-weeks |291|30,060|141|129,778|0.959%|67.36%|
| FRAUD, 200,000 transactions |1,001|32,277|734|165,988|3.008%|57.69%|
| AML low amounts |107|12,308|34|27,632|0.862%|75.89%|
| FRAUD low amounts |460|12,203|210|42,125|3.633%|68.66%|

Selected and reference are identical, so paired differences are zero and the
final recall guard passes **by reference fallback**, not by model improvement.
A 300-replicate paired bootstrap clusters by the 75 original parents, never by
rows/clones; full intervals are in JSON. All figures concern simulated stress
versus assumed background/lookalikes, not verified financial crime.

Final timestamps are 175,067 date-only and 24,933 minute-level; all 200,000 have
unknown timezone. Median gap is 12 days; median parent span is 2,160 days.
FRAUD date-only precision/recall is 4.23%/53.95%, versus 1.27%/86.14% for supplied
minute clocks. These observed groups differ in composition; the association
is not causal proof that clock precision causes false flags. Both remain in
metrics. Recipe-only diagnostics contain positives only and estimate recall,
not precision.

The same original-source validation observations now receive 959/5,764 AML
review flags (16.64%) and 987/6,809 FRAUD flags (14.50%), compared with the prior
full-view mixed run's 2,549 and 6,383. Source alignment reduces that unlabeled
review workload; **it cannot establish source detection accuracy**. Old/new
final metrics use different generators and are not an apples-to-apples model
improvement comparison.

## Separate sender–receiver AML benchmark

The [official IBM AMLSim published-data page](https://github.com/IBM/AMLSim/wiki/Download-Example-Data-Set)
links the v2.1 archive used here: 885,744 transactions, 20,000 accounts and 807
simulator SAR transactions, all USD. The archive SHA-256 is
`e36015be8c728dc0acdc437aa7ab494fdc24d3289c0090673b07de31ac12a5fa`.
The [reference repository](https://github.com/IBM/AMLSim/tree/7338a4bcb1af9bcfea2201ad7daccfe2a4d569ca)
is pinned for provenance; the archive's generating commit is unknown.
Repository code is [Apache-2.0](https://github.com/IBM/AMLSim/blob/7338a4bcb1af9bcfea2201ad7daccfe2a4d569ca/LICENSE).
The separately hosted archive has no separately stated data licence in its
README/wiki; repository licensing does not establish archive licensing. Raw
files stay local/ignored. [Exact benchmark evidence](aml_network_20261016.json)
records source files, exclusions, hashes and results.

The engine uses actual simulator sender/receiver columns, 18 numeric directed
graph features, 100 trees and subsample 256. It excludes raw amounts, identifiers,
SAR/pattern columns and the archive's label-derived GPML features. This avoids
using its stylized large SAR amounts as an answer key. Labels are opened for
evaluation only after model/threshold freeze. Fixed 0.5 and TRAIN q99 are both
reported; neither is selected by targets. Units are account/30-day windows
known at window end, not real-time transaction interception.

All 20,000 accounts form **one weak component**. Deterministic 70/30 account
assignment plus a 14/10-window chronological split at 25 February 2018 retains
250,600 TRAIN edges and 33,744 holdout edges; 601,400 are excluded: 375,063 cross-
group, 178,791 TRAIN-group after cutoff and 47,546 holdout-group before cutoff.
Only 22 SAR transactions and 35 positive account-windows remain in holdout.
Training has 178,897 account-windows; holdout has 39,828. Account/time separation
is real, but the induced subgraphs substantially fragment published patterns.

| Holdout account/30-day window | TP | FP | FN | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| Fixed 0.5 |9|2,673|26|0.336%|25.71%|
| TRAIN q99 (0.62269618) |1|56|34|1.754%|2.86%|

Transaction endpoint-support recall is 10/22 (45.45%) at 0.5, with 7,638 FP and
precision 0.131%. A transaction receives the larger of its endpoint window
scores; this is not evidence of detection before the transaction completes.
There are **zero complete retained AML patterns**, so complete-pattern recall
is **undefined**. Four gather-scatter fragments are flagged and one of nine
scatter-gather fragments is flagged at 0.5; this is not 100% AML pattern recall.
No cycle is present in the retained holdout. The benchmark is independent of
the Kaggle generator but remains public synthetic data with a highly restricted
holdout; its results cannot establish real-world AML quality.

## Limitations, verification and decisions

**159 focused checks pass:** 146 related behavioural ML checks plus 13 external
network checks. Of these, 55 are new: 6 generator, 17 audit, 19 robustness and 13
network. Checks cover causal history/future invariance, complete availability
ties, exact money boundaries, clone grouping, recall-loss rejection, estimator/
label separation, all-fits-before-validation, freeze-before-final, source/code/
protocol/model/card mutation and immutable output roots. Prior 52 research
hashes remain unchanged. Docker is unavailable; no new full DB-backed app suite,
frontend build, browser UAT or live upload run is claimed.

**Decisions taken independently within Jevon's approval:** preserve old evidence;
create v2 in new roots; test one audit-motivated relative view alongside the
reference/portable view; use three parent groups, 100% recall-count retention
and reference fallback; keep observationally identical stress/control pairs
honest; pin published AMLSim instead of installing obsolete simulator tooling;
exclude amounts and label-derived graph inputs; disclose all partition losses.
AS-10 records the numerical choices as research assumptions. FR-110/TRD §6.1–6.2
support versioned contracts/checksums; FR-401/TRD §10.2's existing baseline is
not replaced. CF-05 and live approval remain separate.

The next evidence needed is independently reviewed outcomes and distinguishing
behaviour/context, not another million copies of the same unlabelled patterns.
A further network experiment should preserve complete published scenarios and
separately state known-account temporal versus unseen-account generalization;
it needs a new protocol/evaluation boundary. Do not tune against either final
set reported here. No high-precision fraud/AML claim or model promotion is
supported by this stage.

## Ringkasan capstone

Lima langkah sudah dikerjakan: audit validasi, generator sesuai pola sumber,
training enam model tanpa label, seleksi dengan penjagaan recall, tes final
baru setelah freeze, serta benchmark AML graf terpisah. Sebanyak 1,4 juta data
baru dibuat, tetapi belum ada kandidat yang memenuhi syarat perbaikan.
Model pembanding dipertahankan; precision final sintetis AML 0,959% dan
FRAUD 3,008% masih rendah. Data tanpa label dan tanpa fakta pembeda tidak bisa
menentukan niat fraud, terutama pada pasangan aktivitas yang terlihat sama.
Benchmark graf pun belum membuktikan kualitas AML: pemisahan memotong pola
sehingga tak ada pola utuh dalam holdout. Semua hasil tetap DRAFT; 159
pengujian teknis lulus, tanpa aktivasi model atau perubahan data aplikasi.
