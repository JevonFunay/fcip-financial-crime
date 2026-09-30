# Scenario catalogue — injected positives, edge cases and control cohort

Profile `tiny`, seed `20260923`. Every row below has matching rows in
`labels.csv`, so recall and control-cohort tests are mechanical rather than eyeballed (TRD §11.3).

| Pattern | Scenario | Construction | Expected reason code | Labelled |
|---|---|---|---|---|
| `P01` | Unusually large single transfer | One transfer of IDR 2-8bn against an ordinary baseline | `P01_UNUSUALLY_LARGE_SINGLE_TRANSFER` | 1 positive, 2 edge |
| `P02` | Structuring below the reporting threshold | 3-5 deposits inside [350M, 500M) within 7 days, aggregate >= 500M | `P02_STRUCTURING_BELOW_THE_REPORTING_THRESHOLD` | 1 positive, 2 edge |
| `P03` | Same-day pass-through | A credit, then 94-99% of it out again within 1-5 hours | `P03_SAME-DAY_PASS-THROUGH` | 1 positive, 2 edge |
| `P04` | Transaction count spike against baseline | 40-90 payments over 3 days against a much lower baseline | `P04_TRANSACTION_COUNT_SPIKE_AGAINST_BASELINE` | 1 positive, 2 edge |
| `P05` | Dormant account reactivation | 90+ days with no activity on the account, then a credit of IDR 400M-1.5bn | `P05_DORMANT_ACCOUNT_REACTIVATION` | 1 positive, 2 edge |
| `P06` | Uniform round amounts | 6-12 repetitions of one identical round amount within two weeks | `P06_UNIFORM_ROUND_AMOUNTS` | 1 positive, 2 edge |
| `P07` | Stepped weekly value increase | Weekly value stepping up 2-3x for five consecutive weeks | `P07_STEPPED_WEEKLY_VALUE_INCREASE` | 1 positive, 1 edge |
| `P08` | Concentrated high-risk geography exposure | 8-18 remittances concentrated on one listed geography | `P08_CONCENTRATED_HIGH-RISK_GEOGRAPHY_EXPOSURE` | 1 positive, 1 edge |
| `P09` | Device shared by unrelated entities | One device used by 4-7 otherwise unrelated entities | `P09_DEVICE_SHARED_BY_UNRELATED_ENTITIES` | 4 positive, 6 edge |
| `P10` | Merchant activity inconsistent with its MCC | 20-45 payments at 100-1000x the ticket size the MCC implies | `P10_MERCHANT_ACTIVITY_INCONSISTENT_WITH_ITS_MCC` | 1 positive, 1 edge |
| `P11` | Many-to-one funnel with device overlap | 8-16 senders converging on one account within 5 days, sharing a device | `P11_MANY-TO-ONE_FUNNEL_WITH_DEVICE_OVERLAP` | 1 positive, 2 edge |
| `P12` | Name similar to a synthetic list record | Customer name one character away from a synthetic list record | `P12_NAME_SIMILAR_TO_A_SYNTHETIC_LIST_RECORD` | 1 positive, 1 edge |
| `ATO` | Account takeover after dormancy on a new device | 90-150 quiet days, then 5-15 outgoing payments at 2-8x the customer's usual p95 within 6 hours, from a device the customer never used | `ATO_ACCOUNT_TAKEOVER_AFTER_DORMANCY_ON_A_NEW_DEV` | 1 positive, 1 edge |

## Look-alikes and boundary cases (1.4.0)

Look-alikes follow the TRD §11.2 examples and the FRD §8 lists of expected false positives, so
some of them do fire the default rule (payroll, float top-ups, a family handset, a B2B supplier,
about half the salaries and returns from abroad); that is the false-positive burden a model
should lower. A boundary case sits exactly on its pattern's own threshold, on the side that
must not fire; the party's own background that could tip it over is carved away. P07, P08, P10
and P12 have none: P07 needs a high-value baseline no ordinary customer has, P08 and P10 fire
on any of several OR-ed sub-conditions, and P12 is the screening engine's.

| Pattern | Look-alike | Boundary case |
|---|---|---|
| `P01` | Annual bonus or vehicle down payment: one IDR 60-95M transfer | exactly IDR 100,000,000 from a customer with 8 transactions in 90 days |
| `P02` | Arisan collector: in-band cash deposits always spread past the 7-day window | 3 deposits of exactly IDR 500,000,000 (the threshold is exclusive) |
| `P03` | Salary of IDR 20-80M in, 85-95% out to bills and savings the same day, two months | 79% of a IDR 60M credit out within hours |
| `P04` | Payroll disburser: 15-40 staff paid within 3 hours on the company's payday, two months | exactly 14 transactions in 24 hours |
| `P05` | Back from working abroad: 90+ quiet days, then IDR 10-40M and ordinary spending | 89 quiet days, then a IDR 30M credit |
| `P06` | Agent kiosk: 8 round float top-ups of about IDR 20M in 16 days | exactly 4 round amounts in 30 days |
| `P07` | Seasonal trader: three weeks of 20-35 sales of IDR 2-6M from many buyers | - |
| `P08` | Student abroad: fortnightly family support from a normal corridor | - |
| `P09` | A family of 3-4 at one address sharing one handset | one device used by exactly 3 entities |
| `P10` | B2B supplier: 20-40 payments from 3-5 clients at 1.5-2.5x its category's ticket | - |
| `P11` | School fees: 30-60 payers, one fee, the same names each month, spread over 12 days | exactly 7 senders within 48 hours, over IDR 200M |
| `P12` | A common given name shared with a list record, a different family name | - |

## ATO look-alikes

Each carries one or two of the three takeover signals (dormancy, a never-used device, a burst),
never all three, so a detector that keys on any single signal pays for it in false positives.

| Kind | Signals | Construction |
|---|---|---|
| `RETURNING_NEW_PHONE` | dormancy + new device | 90-150 quiet days, then 2-4 ordinary payments over a few days |
| `NEW_PHONE_PAYDAY` | new device + burst | 5-10 routine payments at the customer's own amounts on payday |
| `RETURNING_SAME_PHONE` | dormancy + burst | 90-150 quiet days, then 3-6 ordinary payments on the usual phone |

## Ordinary device changes

15% of individuals start using a new device part-way through the period, so a
device never seen for a customer is common in legitimate traffic too.

## Counterparties (TRD §11.2)

Retail customers send P2P money to 3-8 regular counterparties, pay 4-10 usual merchants and
1-3 billers, and top up from 1-2 bank accounts of their own; 10% of their
transfers and merchant payments go to someone new, and 25% of those become regulars. Businesses have a broad
payer base: an established one at the start (a third of their payment count), and 30% of
incoming payments from first-time payers. ATO pays only recipients its victim never paid.

## Background behaviour (TRD §11.2, 1.4.0)

Activity is heavy-tailed within the TRD §11.1 total: each entity's share is lognormal (sigma 1.0), merchants
3x as active as retail customers. Retail rows are 40% merchant payment, 20% wallet topup, 15% bill payment, 25% p2p transfer;
top-ups come in (round to IDR 50,000), bills go out, 5% of merchant payments are refunds and 40% of
transfers are received. A merchant is paid while it is open (declared hours), at a ticket around its
MCC's typical ticket, and sweeps its takings to its own bank account every week; a sole trader is
paid by transfer.

## Per-transaction ground truth

`label_transactions.csv` lists every row each scenario and look-alike emitted, for evaluating a
per-transaction model against exact rows. Like `labels.csv` it is evaluation-only: never loaded
into the app, never a feature input.

## Population (TRD §11.2)

Assigned as exact counts, so the shares hold at every scale. Real businesses are all in the
business cohort; §11.1's volumes leave them short of §11.2's share, and individual sole traders
make up the difference.

| Cohort | Entities | Share | Spec |
|---|---|---|---|
| `RETAIL_NORMAL` | 64 | 62.7% | ~62% |
| `BUSINESS_NORMAL` | 18 | 17.6% | ~18% |
| `CONTROL_CLEAN` | 8 | 7.8% | ~8% |
| `EDGE_AMBIGUOUS` | 8 | 7.8% | ~8% |
| `INJECTED_CANDIDATE` | 4 | 3.9% | ~4% |

Business cohort: 12 real businesses + 6 individual sole traders.

## Control cohort (FRD §8.14)

8 entities are generated with deliberately modest,
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
