# Precision research and review readiness — 20 October 2026

## Outcome

The approved engineering and offline experiments are complete. **Useful model
quality is still not established, and human review has not been completed.**
No model is promoted or registered; the app, demo data, P02 and CF-05 are unchanged.
No new dummy rows or invented banking facts were needed for this stage.

- The AML benchmark now retains every supplied edge and measures whole patterns.
  Its reference remains selected; account-window precision is still **0.677%**.
- On a different public FRAUD source, the selected candidate raises precision
  **1.09% → 2.02%**, but misses one extra positive: recall **88% → 86.67%**.
  It fails the strict final coverage guard, including the low-amount slice.
- A blinded, offline review tool contains **300 PENDING cases** from the original
  unlabelled source. No annotation or confirmed crime outcome is fabricated.

[Aggregated evidence and hashes](precision_20261020.json),
[all 24 validation options](precision_20261020_candidates.csv),
[AML evidence](aml_temporal_20261020.json) and
[FRAUD evidence](fraud_public_20261020.json) preserve the exact findings.
Precision from different sources/populations must not be compared as if the
model improved on one fixed test. All new models remain **DRAFT**.

## Protocol and evaluation boundaries

Jevon's 20 October approval covers the preceding precision-improvement plan.
AS-11 records the numerical choices as research assumptions, not FRD operating
thresholds or mentor approval. Existing generator versions, datasets, app code
and the 16 October protected evidence remain unchanged.

Six Isolation Forest models fit without labels: three AML graph/peer candidates
and three FRAUD raw/peer candidates, each with 100 trees. TRAIN preprocessing,
medians, activity profiles and clustering exclude heldout observations/targets.
The optional FRAUD peer view uses TRAIN-only MiniBatchKMeans with 16 population
clusters; these are not customer identities or interpretable crime typologies.
Each arm evaluates 12 fixed model/threshold options: score 0.5 and TRAIN-score
q90/q95/q99. Validation labels select precision subject to reference TP coverage
in declared slices; no estimator receives a target. Final metrics never retune
a model, feature, threshold, peer profile or selection decision.

Both sources are fixed public historical datasets. New protocol boundaries are
not new independent real-world evidence. AML models can score label-free final
features before selection, but final target parsing and metric evaluation are
permitted only after the freeze; those scores are never used for selection.
Fraud final targets are projected only after frozen selection. Unknown source
facts remain unknown; no currency, exact time, beneficiary or session is invented.

## Whole-network AML temporal benchmark

The pinned [IBM AMLSim published dataset](https://github.com/IBM/AMLSim/wiki/Download-Example-Data-Set)
contains **885,744 directed transactions**. The prior account-group experiment
fragmented all complete holdout scenarios. The new protocol retains all supplied
sender/receiver edges and uses 24 non-overlapping 30-day windows:

| Split | Windows | Transactions |
|---|---|---:|
| TRAIN | 0–11 | 444,325 |
| Validation | 12–17 | 220,467 |
| Final | 18–23 | 220,952 |

No edge is discarded by account grouping. Final contains 117,039 active
account-windows from 19,999 accounts, **all seen in TRAIN**. This measures
future activity of known accounts; it does not establish performance on new
customers. Inactive account-windows are absent. Final runs from 25 June to
21 December 2018 across six 30-day calendar windows; the partition metadata
does not mark the last window as partial.

Candidates are graph IF256, graph IF2048 and an IF2048 peer view with causal
prior peer novelty/activity, TRAIN-only activity quartiles and median/IQR
normalization. Raw amount, IDs, SAR/alert columns and supplied label-derived
GPML features never enter fitting. Only the reference `graph256__auto` retains
validation coverage; all 11 alternatives lose overall or recipe coverage.

| Final account-windows | TP | FP | FN | TN | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|
| Reference = selected | 147 | 21,561 | 106 | 95,225 | 0.677% | 58.10% |

The final contains 27 whole-source-complete scenarios: cycle 6/6 detected,
gather-scatter 14/14 and scatter-gather 7/7. Three boundary fragments have a
separate denominator. **A pattern hit means at least one participating endpoint
was flagged at a window end. It does not mean every SAR transaction was caught,
that interception was immediate, or that fraud was confirmed.** Fifteen complete
patterns span multiple feature windows. The high flag workload matters: 21,708
of 117,039 account-windows are flagged. An explicitly post-freeze descriptive
uniform-random reference at the same workload expects about **21.97/27** complete
patterns to receive some endpoint flag. Actual 27/27 therefore must be read
alongside low account-window precision; the random calculation is neither a
model candidate nor a hypothesis test and did not select/tune anything.

The TRAIN low-amount cutoff is USD 47.83, but final contains **zero low-amount
SAR-positive units**. Low-amount laundering effectiveness remains unassessed.
The simulator's stylized SAR amounts and pattern recipes are not real crime
outcomes. The archive's generating commit and separate data licence remain
unknown; the reference repository is Apache-2.0 and raw data stays ignored.

Models freeze at 13:39:45.736674 UTC, before final target evaluation at
13:39:45.736863 UTC; all 26 protected files still match. A review after completion
found that the frozen selector did not explicitly enforce the requested FP cap.
The additive strict selector and post-freeze validation-only audit prove that
**all 12 options already had FP ≤ reference**, and eligibility/winner are exactly
unchanged. The omission is disclosed; it is not retroactively called a
predeclared cap, and the audit neither fits models nor reads targets again.

## Independent public fraud benchmark

[OpenML dataset 1597 v1](https://www.openml.org/d/1597) supplies 284,807 historical
anonymized card observations and publisher-provided binary `Class` references.
The official [metadata API](https://www.openml.org/api/v1/json/data/1597) describes
European-card activity across two days in September 2013. The 73,231,184-byte
Parquet SHA-256 is
`b7efcb35a428bbe22347a05d2437d9177bab07ce61e51214a17bec584ad9496d`.

This is not another dummy augmentation or a Kaggle-source label inferred from
our own scores. However, FCIP has no independent adjudication of the supplied
reference labels. The metadata licence literally says `Public`, without a
verified precise reusable licence grant. Raw data is not committed/redistributed.
Currency is not stated in verified metadata and remains **unknown source units**;
Jevon's USD confirmation applies to the earlier bank-transaction source.

The adapter preserves opaque V1–V28 components plus Amount; `Class`, `Time`, row
indices and invented customer/session identities are not predictors. Relative
Time is used only to form chronological 60/20/20 row targets with whole timestamp
ties kept together: 170,888 TRAIN / 56,957 validation / 56,962 final observations.
There are no account IDs, so customer independence cannot be verified. The
publisher's upstream PCA fitting scope is unknown; only FCIP's added scaler,
cluster/profile and estimator fits are verified TRAIN-only.

Raw IF256/reference, raw IF2048 and a TRAIN-only peer IF2048 are compared. The
selected `peer_2048__train_q0.95` preserves validation TP overall, for low amounts
and in each of three relative-time blocks while reducing FP. It does not fully
retain that coverage in the final period:

| Final, same 56,962 observations | TP | FP | FN | TN | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|
| Raw reference | 66 | 5,987 | 9 | 50,900 | 1.090% | 88.00% |
| Selected peer | 65 | 3,156 | 10 | 53,731 | 2.018% | 86.67% |
| Low amount, reference | 38 | 1,539 | 2 | 14,125 | 2.410% | 95.00% |
| Low amount, peer | 37 | 762 | 3 | 14,902 | 4.631% | 92.50% |

Low amount is at most the TRAIN q25, **5.48 unknown source units**. FP falls by
2,831 (47.3%), but the candidate misses one extra positive, also reflected in
the low-amount and middle relative-time block. **The strict final recall guard
fails.** No threshold is changed afterward and no useful production-quality or
calibrated fraud probability is claimed.

An initial run exposed a missing early model/card checksum guard. The corrected
run is an **integrity reproduction using the same final labels**, not a fresh
independent final. All three model files, the peer profile, all validation/final
scores, selection and metrics match exactly. Original run records remain intact;
its original code is archived under its run directory and matches the initial
checksum, while that original live module path is superseded. The authoritative
v2 run verifies all 15 protected files plus its own frozen manifest. Freeze at
13:41:30.488573 UTC precedes final parsing at 13:41:30.514681 UTC. The linked
reproduction note discloses these limits explicitly.

## Blinded review packet and unresolved evidence

The original source still has no verified fraud outcome or distinguishing
facts for observationally identical controls. Its records are not automatically
labelled normal. The new pipeline samples only the 74 original validation
parents, using the existing frozen DRAFT reference scores:

- 150 AML account-weeks and 150 FRAUD transactions; each unit samples 74 flagged
  and 76 nonflagged cases across flagged × low-amount strata.
- Recorded inclusion probabilities and sampling weights support design-aware
  estimates after review, rather than judging only the flagged examples.
- Reviewer-visible observations and causal prior context omit model scores,
  thresholds, flags, sampling strata, scenario IDs and answer labels. Date-only
  and minute-level precision remain explicit, with full oldest-boundary ties.
- Status starts `PENDING`; allowed completed judgements are `NORMAL`, `ANOMALOUS`
  and `INSUFFICIENT_EVIDENCE`, with reviewer, reason and offset-aware timestamp.
  Crime outcome stays `UNKNOWN`: visible anomalies are not confirmed fraud/AML.

Open the generated [offline review page](../../backend/ml_data/review_s20261020_ui/review.html).
It embeds only the 300 blinded cases, has no network or browser storage, and
exports all 300 annotation rows on a user click. **Save each judgement, then
export CSV regularly: closing/reloading the tab loses its in-memory answers.**
No application alert disposition or database record is modified.

The separate sealed mapping contains evaluation scores/weights; the separation
is logical, not encrypted. Give reviewers only the HTML or the visible packet,
blank template and review instructions. Keep sealed files away until review ends.
The importer validates IDs/schema/status/evidence metadata and writes a new
immutable result. Unknown/pending/insufficient cases never become normal labels.
Weighted estimates with conservative sampling intervals concern **anomaly-review
agreement**, not crime precision; incomplete review does not produce a population
point estimate. Selective unresolved review and reviewer disagreement remain
limits. Currently **all 300 cases are pending**, all anomaly precision/interval
estimates are null, and confirmed fraud/AML precision is unknown.

## Verification and independently chosen assumptions

AS-11 records temporal windows, candidate sets, peer definitions, quantiles,
coverage constraints and the 300-case sample as research choices. FR-110/TRD
§6.1–6.2 support versioned input contracts and reconciliation; existing FR-401
and CF-05 approval limits are unchanged. Nothing in this stage approves an
ACTIVE model, changes P02, or writes to the live app.

**254 focused checks pass**, including 95 new checks (21 AML/strict selection,
17 public FRAUD, 27 review labels, 11 reviewer HTML and 19 reporting diagnostics).
The focused checks cover chronology/ties, strictly prior history, target
projection and all-fits-before-validation, guarded model selection, immutable
outputs, initial/frozen hashes, peer training isolation, actual score parity,
missing-positive/null metrics, complete-pattern versus fragment denominators,
review blinding/weights/uncertainty and exact JavaScript-to-import CSV handling.
Historical 52/35/17 protected files remain unchanged. Full DB-backed application
and frontend suites were not rerun for these offline modules. Browser visual
QA is not claimed; actual generated JavaScript and CSV round trips are checked.

**Decisions taken independently within the approved plan:** retain old evidence;
use full-network known-account chronology; add a public anonymized FRAUD source
with a separate numeric adapter; keep currency/labels/PCA limitations explicit;
fit peers on TRAIN only; preserve strict recall criteria despite partial precision
improvement; disclose both integrity corrections; generate a usable blinded
review tool and leave actual human judgements pending. More copies of identical
unlabelled patterns cannot supply missing intent or investigation outcomes.

## Capstone summary — Indonesian

Benchmark AML diperbaiki dengan mempertahankan seluruh 885.744 transaksi sehingga
27 pola utuh dapat dinilai. Hasil hit pola harus dibaca bersama precision
account-window yang masih 0,677% dan beban false positive yang tinggi. Di sumber
FRAUD publik berbeda, precision naik dari 1,09% menjadi 2,02%, tetapi satu kasus
positif tambahan terlewat dan syarat recall final gagal. Enam model dilatih tanpa
label; label validasi hanya untuk seleksi dan evaluasi. Sebanyak 300 kasus siap
direview lewat halaman offline, tetapi semuanya masih PENDING dan outcome
kejahatan UNKNOWN. Semua model tetap DRAFT; belum ada dasar untuk mengklaim
ketepatan deteksi fraud/AML di produksi atau mengaktifkannya.
