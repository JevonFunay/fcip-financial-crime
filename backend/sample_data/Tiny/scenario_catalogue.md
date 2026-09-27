# Scenario catalogue — injected positives, edge cases and control cohort

Profile `tiny`, seed `20260923`. Every row below has matching rows in
`labels.csv`, so recall and control-cohort tests are mechanical rather than eyeballed (TRD §11.3).

| Pattern | Scenario | Construction | Expected reason code | Labelled |
|---|---|---|---|---|
| `P01` | Unusually large single transfer | One transfer of IDR 2-8bn against an ordinary baseline | `P01_UNUSUALLY_LARGE_SINGLE_TRANSFER` | 1 positive, 2 edge |
| `P02` | Structuring below the reporting threshold | 3-5 deposits inside [350M, 500M) within 7 days, aggregate >= 500M | `P02_STRUCTURING_BELOW_THE_REPORTING_THRESHOLD` | 1 positive, 2 edge |
| `P03` | Same-day pass-through | Credit in the morning, 94-99% of it out again the same day | `P03_SAME-DAY_PASS-THROUGH` | 1 positive, 2 edge |
| `P04` | Transaction count spike against baseline | 40-90 payments over 3 days against a much lower baseline | `P04_TRANSACTION_COUNT_SPIKE_AGAINST_BASELINE` | 1 positive, 2 edge |
| `P05` | Dormant account reactivation | 150 days with no activity, then a credit of IDR 400M-1.5bn | `P05_DORMANT_ACCOUNT_REACTIVATION` | 1 positive, 2 edge |
| `P06` | Uniform round amounts | 6-12 repetitions of one identical round amount | `P06_UNIFORM_ROUND_AMOUNTS` | 1 positive, 2 edge |
| `P07` | Stepped weekly value increase | Weekly value stepping up 2-3x for five consecutive weeks | `P07_STEPPED_WEEKLY_VALUE_INCREASE` | 1 positive, 2 edge |
| `P08` | Concentrated high-risk geography exposure | 8-18 remittances concentrated on one listed geography | `P08_CONCENTRATED_HIGH-RISK_GEOGRAPHY_EXPOSURE` | 1 positive, 2 edge |
| `P09` | Device shared by unrelated entities | One device used by 4-7 otherwise unrelated entities | `P09_DEVICE_SHARED_BY_UNRELATED_ENTITIES` | 6 positive, 2 edge |
| `P10` | Merchant activity inconsistent with its MCC | 20-45 payments at 100-1000x the ticket size the MCC implies | `P10_MERCHANT_ACTIVITY_INCONSISTENT_WITH_ITS_MCC` | 1 positive, 2 edge |
| `P11` | Many-to-one funnel with device overlap | 8-16 senders converging on one account within 5 days, sharing a device | `P11_MANY-TO-ONE_FUNNEL_WITH_DEVICE_OVERLAP` | 1 positive, 2 edge |
| `P12` | Name similar to a synthetic list record | Customer name one character away from a synthetic list record | `P12_NAME_SIMILAR_TO_A_SYNTHETIC_LIST_RECORD` | 1 positive, 2 edge |

## Control cohort (FRD §8.14)

9 entities are generated with deliberately modest,
well-spread behaviour and must raise **zero** alerts at approved default parameters. A control-cohort
alert is a rule defect, not a finding.

## Entity resolution population (TRD §11.5)

| Construction | Count | Expectation |
|---|---|---|
| `SAME_ID_NAME_VARIANT` | 2 | must auto-merge |
| `SAME_PHONE_DOB` | 2 | must auto-merge |
| `SAME_NAME_ONLY` | 2 | must **not** auto-merge (US-111) |
| `IDENTIFIER_CONFLICT` | 2 | must force `PENDING_REVIEW` with `IDENTIFIER_CONFLICT` |
| `AMBIGUOUS_BAND` | 2 | lands in the 0.75-0.95 manual review band |
