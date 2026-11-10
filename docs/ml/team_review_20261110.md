# Team-labelled transaction learning exercise — 10 November 2026

## Outcome

The supplied team key is joined and the explicitly approved offline training
finishes once. All three native candidates fit on **430 TRAIN rows**, validate
on 92 and leave 93 for selected-only TEST. LightGBM wins validation AP; its fixed
threshold 0.5038643408 yields **TP10 / FP0 / FN5 / TN78**, precision **100%**, recall
**66.67%** and F1 **0.80** on the 93-row TEST.

Precision 100% describes **only ten flagged transactions** in this small dataset.
Five of 15 positive TEST transactions are missed, and ATO/MULE/DORMANT have no
positive TEST support. This is not a perfect model or evidence that the earlier
68.49% precision benchmark improved. The dataset, labels and populations differ.
All new artifacts remain **OFFLINE_DRAFT**; no DB/model activation occurs.

[Results and original hash expectations](team_review_20261110_results.json),
[technical checks](team_review_20261110_checks.json) and
[independent verification](team_review_20261110_verification.json) accompany this
report. The separate [4 November benchmark](lab_repair_20261104.md) is unchanged.

## Source and label provenance

Jevon supplied five files from Downloads: `transaksi_untuk_diflag.csv`, its
`.xlsx` equivalent, `kunci_jawaban.csv`, `make_flag_dataset.py` and
`PANDUAN_FLAGGING.md`. He clarified that the key is the team's manual flagging
result and explicitly approved its use for this exercise. This overrides the
guide's original prohibition on training from that key.

There are **615 synthetic IDR transactions, 30 accounts, July–September 2026**.
CSV/XLSX headers and values match, allowing numeric Excel amount representation;
the workbook has one visible `Transaksi` sheet, no formulas, and initially empty
`is_fraud`/`catatan_reviewer` columns. Source files and the supplied generator
remain unchanged; the generator is read, never executed.

Manual provenance is **user-reported**, not independently verified. There is no
reviewer identity, timestamp or agreement log; the supplied script also writes
scenario labels and a key of this format. Labels represent team decisions on
synthetic cases, not adjudicated fraud or money laundering.

## Preparation and approved evaluation protocol

An additive local file is ready at
`backend/ml_data/team_review_s20261110/training_labeled.csv`. An exact one-to-one
`transaction_ref` join also checks `account_number`, unique IDs, complete matching
key sets and literal 0/1 labels. All 615 rows normalise with no quarantine:
**73 positive / 542 negative**. No label is guessed, flipped, duplicated or sampled.
The output SHA is `d2b8087e9fc07a2462d84bb1d3fb3be405035b8027430a670b1753e6f220aea6`.
The preparation manifest retains all five original input SHA values and the
mapping. Key `penjelasan` is not copied into the training file.

`pattern_code` is category metadata from key `skenario`, removing only a trailing
numeric episode suffix. Families are ATO 16, VELOCITY 27, STRUCTURING 12, MULE 16 and
DORMANT 2 positive rows. Categories, notes, explanations, names and recipient
relationships are excluded from the actual numerical model features. DORMANT
does not certify FRD P05: the supplied period has no earlier observed transaction
for that account and only about 60 days of designed quiet activity.

Default 60% TRAIN would omit DORMANT positives from TRAIN despite their presence
in validation. Jevon therefore explicitly selects **70/15/15**, before fitting:

| Period | Rows | Positive | Negative | Positive category support |
|---|---:|---:|---:|---|
| TRAIN | 430 | 46 | 384 | ATO 11, VELOCITY 11, STRUCTURING 6, MULE 16, DORMANT 2 |
| Validation | 92 | 12 | 80 | ATO 5, VELOCITY 7 |
| TEST | 93 | 15 | 78 | STRUCTURING 6, VELOCITY 9 |

Splits preserve whole availability-time groups and the actual current normalizer.
Validation/model selection is not based on TEST predictions. TEST label-support
metadata has already been inspected for readiness and is disclosed. VELOCITY-2
spans TRAIN/validation; same-account history is shared. There is no independent-
episode or globally blind final-test claim. The frozen, separate 250k/100k study
is not replaced by this small learning exercise.

The three native candidates use existing eight feature groups, model seed
20261026 and TRAIN-only vocabulary. Highest validation AP chooses one model;
native validation max-F1 fixes its threshold. All source/code/model/selection
hashes seal before only the chosen model's TEST is scored. No registry, DB,
ACTIVE model or rule is changed, and no final tuning occurs.

## Results and interpretation

Native model selection uses validation only:

| Candidate | Validation AP | Validation precision | Validation recall | Native default threshold |
|---|---:|---:|---:|---:|
| Logistic Regression | 0.792248 | 64.71% | 91.67% | 0.8950247540 |
| **LightGBM, selected** | **0.966667** | **100.00%** | **91.67%** | **0.5038643408** |
| Isolation Forest | 0.744100 | 64.71% | 91.67% | 0.5547109325 |

The nonselected candidates' TEST scores are never computed. After 28 protected
input/code/model/selection files and their own frozen-manifest SHA seal, only
the saved LightGBM is reloaded and scores TEST once. Frozen at
**2026-11-10 09:46:50.166457 UTC**, TEST scoring starts
**09:46:50.220743 UTC**. The cache has 93 rows, fifteen positives and 78 negatives.
There is no threshold change or refit after the result.

| Selected TEST slice | TP | FP | FN | TN | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|
| All 93 rows | 10 | 0 | 5 | 78 | 100.00% | 66.67% |
| Small amount, 25 rows / 8 positives | 6 | 0 | 2 | 17 | 100.00% | 75.00% |

The small cutoff is the earlier TRAIN q25 IDR110,625, not a TEST-derived value. Pattern
recall is STRUCTURING **3/6 (50%)**, VELOCITY **7/9 (77.78%)**; ATO, MULE and
DORMANT recall is **null/unassessed**, never 0% or 100% in the absence of positives.

Interpretation limits established before fitting: **613/615 rows (99.67%) lack
the feature library's minimum history gate**. Prior-mean amount comparisons are
available for only two observations. Source channels contain strong recipe
shortcuts (all WEB and AGENT examples are positive under the supplied key).
Current model features do not model recipient identity/relationship text, so
judgments such as own-account transfers or arisan are not automatically learned
from those narratives. A small high score could reflect recipe separation.

Only 15 positive TEST rows and two positive families cannot establish broad ATO,
MULE, dormancy or AML quality. More copies of these 615 rows would not create new
independent evidence. This dataset can teach a documented review target; it
cannot by itself justify claims of improved real-crime precision.

## Verification and reproduction

Preparation validates all 615 labels against the separate key, checks account/ID
joins, verifies CSV/XLSX equivalence and source hashes, and passes the existing
normalizer with no missing labels. Large local data/model artifacts stay ignored
under `backend/ml_data/`.

The preparation manifest's original SHA is externally pinned:
`99726eaed9d81461e9f79bf9bbef8c96d46b68dc61cdc6b7dd4babaa0fb3a481`.
The runner rejects rewritten source expectations, swapped labels, duplicate or
missing IDs, changed transaction fields and mapping changes that could invert
the target or introduce category/reviewer leakage.

**25 pure guard checks pass**, independently repeated (1.24s owner run, 1.11s
independent pytest run). Tests execute from an identical temporary copy outside
the repository's DB autouse fixtures, with no model fits or database connection.
Coverage includes external manifest rebasing, strict label/account joins,
mapping inversion/leakage, source/code tampering, validation-only comparison,
freeze-before-selected-TEST, existing-output refusal and nonfinite-score failure.
These are new-script checks; the 913-test application suite last passed 4 November
and is not represented as rerun in this exercise.

Independent preparation checks reproduce all615joins and CSV/XLSX equivalence.
Replacing reviewer/name/recipient/merchant-description text with label-bearing
strings leaves the canonical frame, TRAIN vocabulary and full numerical feature
matrix identical. The test is in-memory; original files remain unchanged.

The one actual CLI invocation exits 0, wall time 1.01s (not application upload
latency). The retained log contains Logistic Regression matrix warnings during
fit/validation. Selected LightGBM scoring is finite and emits no such warning;
the warnings are not hidden and no nonselected TEST scores are computed.
The original local report SHA is
`27f1ecf685750b4d0aa57a1808923109d4e511dddab16e617c7d26673ee620d4`;
the frozen-manifest SHA is
`dcaedabda6abdccbb418af503918f825bc31126e4516d691141080baed180c85`.
The actual log SHA is
`0a47e5dc2b3219ce974a1b4f209608ccc82ae01eb6b1050dda9854af055c1475`.

Independent verification checks all28original frozen expectations, the TEST
cache hash, allfive original sources and the externally pinned import manifest.
The three saved candidates reproduce their native validation AP, all curve
points and max-F1 defaults. Only selected LightGBM TEST scores are replayed;
all93references, scores, flags, family counts and small-slice metrics match
exactly. No additional fit, candidate TEST comparison or retuning occurs.

An optional non-BLAS LR validation calculation differs by at most2.22e-16;
one positive exactly at the native LR threshold falls just below it under the
alternate arithmetic. That boundary difference is recorded, not claimed as
identical. Native validation replay and the selected LightGBM TEST are exact;
LR TEST is never scored. Transient LR optimizer iterates are not reconstructed.

Run the declared exercise from `backend/` into a new study destination:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m app.scripts.train_team_review \
  --data ml_data/team_review_s20261110/training_labeled.csv \
  --manifest-sha256 99726eaed9d81461e9f79bf9bbef8c96d46b68dc61cdc6b7dd4babaa0fb3a481 \
  --out ml_data/team_review_s20261110/study
```

The runner refuses an existing study directory. Reproduction requires the same
prepared bytes, original five sources, externally pinned import manifest and
matching code/packages; relocation of absolute source paths needs explicit new
provenance. Recreating an exposed dataset is not a new blind evaluation.

## Decisions and capstone summary

**Decisions I took myself:** preserve original files and hashes; join by stable
ID with account checks; keep explanations out of features; retain scenario
families as category metadata; use unchanged native algorithms/threshold rules;
keep all results offline. **Jevon's explicit decision:** 70/15/15 chronological
split and use of the supplied key as team labels. AS-20 records the scope.

**Ringkasan capstone:** Dataset latihan berisi615 transaksi sintetis dipadankan
dengan label flagging tim melalui ID transaksi, menghasilkan73 label positif
dan 542 negatif tanpa kehilangan baris. Pembagian waktu 70/15/15 disetujui sebelum
training. Eksperimen dipisahkan dari benchmark besar dan model aktif; jumlah
contoh uji yang kecil, riwayat terbatas dan pola kanal buatan membatasi klaim
kualitas yang dapat diambil. LightGBM dipilih melalui validasi lalu mendapat
TP10/FP0/FN5/TN78 pada 93 transaksi TEST: precision 100% dari hanya 10 transaksi
yang ditandai, recall 66,67%. Hasil ini belum membuktikan peningkatan presisi
produksi atau kemampuan deteksi seluruh pola.
