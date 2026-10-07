# Fresh post-fit evaluation — seed 20261007

Measured 7 October 2026 on a dataset generated **after** the existing models
were fitted. All figures describe synthetic data from the same generator and
period as training; they do not estimate performance on real transactions.

## Provenance and procedure

- Generator 1.4.0, Full profile, seed 20261007, reference date 2026-09-30.
- Source: 421,746 transactions; accepted: 412,364; rejected: 9,382.
- Features: fs_v2, 147,931 AML entity-weeks and 412,364 Fraud transactions.
- Models: `aml_gbm_v1` and `fraud_gbm_v1`, trained on seed 20260923.
- Thresholds fixed from the original validation: AML 0.29797655491654246;
  Fraud 0.1775440063444594. No tuning, reference rebuilding or retraining.
- All ten existing model/reference file checksums were unchanged afterwards.
- Fresh manifest SHA-256:
  `5e59186fb5294ce280b1f830aa3d99fb1b2f4545ab3a4893344cb3ef92f45398`.

The existing `app.ml.build_features` and `app.ml.evaluate` were run without
`--train`. Ground truth was read only by offline evaluation; neither uploaded
CSV nor the inference path contains labels. Complete JSON and generated
Markdown are local under
`backend/ml_data/manual_full_s20261007/evaluation_manual_s20261007.*`.

## Results at the unchanged thresholds

| Model | Average precision | Unit precision | Unit recall | Owned positive scenario labels detected | Clean control entities flagged |
|---|---:|---:|---:|---:|---:|
| AML | 0.996909 | 98.50% | 98.42% | 517/517 (100%) | 0/830 |
| Fraud | 0.997048 | 98.79% | 98.44% | 153/155 (98.71%) | 0/830 |

Unit metrics use the existing evaluation label groups, excluding participants,
post-scenario and other out-of-scope groups as in the original evaluation.
Scenario recall counts a scenario detected when any relevant unit is flagged;
it is not transaction recall. Fraud detects ATO 60/60, P04 55/55 and P05 38/40.
Each owned AML pattern has 100% scenario recall on this seed.

Remaining errors matter: AML flags 13 look-alike labels; Fraud flags 16
look-alike and 4 boundary labels. AML also flags 396/401 P11 sender participants
that were excluded from training and from the headline precision calculation.
The earlier holdout's Fraud control false positive is still recorded in
[the original evaluation](evaluation_v1.md); a clean result on this seed does
not erase it. Leave-one-pattern-out robustness was not repeated because that
experiment retrains models; its earlier generalisation limits still apply.

## Agreement with manual-upload inputs

Uploading both prepared CSV parts to an isolated verification database accepted
exactly the offline rows and reconstructed identical transaction and master
feature inputs. The application and offline evaluation both flag **1,933 AML
entity-weeks** and **4,346 Fraud transactions**. AML stores additional unflagged
supporting scores for entities with rule alerts, so stored-score counts differ.
These are different units and are not added as a transaction count.

The application raises 58 P02 alerts and no model-only alerts while
`ML_SCORE` is DRAFT (CF-05). Triage decisions remain human actions on rule
alerts. The live demo database was left empty for Jevon's own uploads; see
[the demo guide](../DEMO_FRESH_DATA.md).

## Reproduce the offline evaluation

After generating the fresh dataset, from the repository root:

```bash
docker compose exec backend python -m app.ml.build_features ml_data/manual_full_s20261007
docker compose exec backend python -m app.ml.evaluate ml_data/manual_full_s20261007 \
  --json ml_data/manual_full_s20261007/evaluation_manual_s20261007.json \
  --markdown ml_data/manual_full_s20261007/evaluation_manual_s20261007.md
```

The explicit output paths preserve the original evaluation report. To reproduce
the browser uploads instead, follow the demo guide and upload both parts before
running detection.
