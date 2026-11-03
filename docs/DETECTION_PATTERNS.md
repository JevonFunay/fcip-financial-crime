# Detection patterns — implementation and parameter sheet

Status as of 3 November 2026. Covers FRD §8 / TRD §9.3 patterns **P01, P02, P03–P10**.
P11 (funnel flow, needs the relationship graph) and P12 (watchlist potential match, needs the
screening engine) are not built. `ML_SCORE` is separate (model alerts, CF-05).

## How a pattern works here

Every pattern is a *template* (code, `app/services/detection/pNN_*.py`) plus *parameters* (data, the
ACTIVE `rule_version` row). Changing a number means a new rule version, not a code change
(TRD ADR-006). A template reads the parameters of the ACTIVE version, evaluates the transactions
and returns candidates; `run_pattern` turns them into alerts that carry the factors (observed value
next to the threshold applied), the exact transactions, the rule version and the template version.

- A re-run on the same data raises nothing new (same rule + customer + evidence set).
- A simulation (`POST /rules/{ref}/versions/{v}/simulate`) runs the same `evaluate()` on a DRAFT
  version and writes no alert.
- A parameter that is missing or wrong makes the rule fail loudly (`ValueError`), never approximated.
- Windows that are days, weeks or opening hours use Asia/Jakarta; storage is UTC (ADR-010).
- Temporary simplification, as for P02: `customer_id` stands in for `entity_id` (no entity resolution yet).
- Run: `POST /detection/run` (all patterns) or `POST /detection/patterns/{pattern_code}/run`.

## Parameter sheet (for the mentor: values are the FRD §8 defaults, not decisions)

These are what migration 0013 seeded as ACTIVE (RUL-0003…RUL-0011), bypassing maker-checker because
FR-304 is not built. Please confirm or change each line. Amounts are IDR.

| Pattern | Window | Parameters (default) | Keep / change |
|---|---|---|---|
| **P01** Large transaction | rolling 90 d (baseline) | individual ≥ 100,000,000 · merchant ≥ 500,000,000 (MQ-01) · or amount > 5 × own p95 · relative judged only with ≥ 20 earlier transactions | |
| **P02** Structuring *(existing)* | rolling 7 d | reporting threshold 500,000,000 (MQ-02) · band ≥ 70% · ≥ 3 transactions · aggregate ≥ 1.0 × threshold | |
| **P03** Rapid movement | rolling 24 h | credit ≥ 50,000,000 · debits within 24 h sum to ≥ 80% of it · ≥ 1 debit | |
| **P04** High velocity | rolling 24 h vs 90-day daily baseline | count ≥ 15 **and** (≥ mean + 3σ **or** ≥ 4 × mean) · baseline needs ≥ 30 days | |
| **P05** Dormant reactivation | 7 d after a gap | gap ≥ 90 d · then ≥ 25,000,000 or ≥ 5 transactions · severity HIGH above 100,000,000 | |
| **P06** Round amounts | rolling 30 d | exact multiple of 1,000,000 · among transactions ≥ 5,000,000 · ≥ 5 round and ≥ 60% of them | |
| **P07** Sudden spike | calendar week vs 12 weeks | week ≥ 4 × weekly mean **and** rise ≥ 100,000,000 · needs ≥ 6 weeks · HIGH above 10 × | |
| **P08** High-risk geography | rolling 30 d | list `geo_list_v1` (KY, PA, SC, VU, BZ, synthetic) · count ≥ 3 **or** value ≥ 50,000,000 **or** share ≥ 25% | |
| **P09** Shared device | rolling 30 d | ≥ 4 distinct entities **or** ≥ 6 distinct accounts on one device | |
| **P10** Unusual merchant | rolling 30 d, ≥ 30 transactions | ticket ≥ 3 × band midpoint · reversal rate > 15% · top-5 payers > 70% of value · off-hours share > 60% | |

Questions worth a decision, because they change volume (see "Volume" below): the P01 thresholds
(MQ-01), whether P08's *share* condition should need a minimum count, and whether P10 should alert once
per merchant or once per 30-day window.

Assumptions the team made where the FRD is silent (all in the rule version, so changeable):
the four ticket-band midpoints of P10 (`LT_50K` 25,000 · `50K_250K` 150,000 · `250K_1M` 625,000 ·
`GT_1M` 2,000,000); P08's list of five country codes is the same synthetic list the data generator
treats as high-risk, not an official designation; "reversed" for P10 means source status `REVERSED`.

## What each pattern cannot see yet (reported, never invented)

| Pattern | Missing input | What the alert says instead |
|---|---|---|
| P01, P04, P05 | `entity_id` (no entity resolution) | alerts per `customer_id` |
| P08 | IP country (not in the ingested data) | `IP_VS_REGISTERED_MISMATCH = DATA_UNAVAILABLE` |
| P09 | household / low-specificity context (needs the graph, FR-402) | `HOUSEHOLD_RELATION_COUNT = DATA_UNAVAILABLE`, `SPECIFICITY_FLAG = UNASSESSED` |
| P10 | merchant registration date | `MERCHANT_AGE_DAYS = DATA_UNAVAILABLE`; ticket/hours not judged when the merchant has no declared band/hours (listed in `SUB_CONDITION_NOT_JUDGED`) |
| P01, P04, P07 | enough history | relative branch not judged; counted in the run's notes, not silently passed |

## Evidence: generated "small" profile (synthetic, 20,458 accepted transactions)

Run with the project's generator (`--profile small`, seed 20260923), loaded and ingested, then all ten
rule patterns once. All ten patterns together took about 2 seconds. Ground truth is the generator's own
`labels.csv`.

| Pattern | Injected positives caught | Look-alike (edge-case) entities alerted | Alerts raised |
|---|---|---|---|
| P01 | 3 / 3 | 1 / 2 | 161 |
| P02 | 2 / 2 | 0 / 2 | 2 |
| P03 | 2 / 2 | 1 / 2 | 4 |
| P04 | 3 / 3 | 0 / 2 | 9 |
| P05 | 2 / 2 | 1 / 2 | 9 |
| P06 | 2 / 2 | 1 / 2 | 3 |
| P07 | 2 / 2 | 1 / 1 | 48 |
| P08 | 2 / 2 | 0 / 1 | 2 |
| P09 | 10 / 10 | 0 / 6 | 4 |
| P10 | 2 / 2 | 1 / 1 | 46 |

**Control cohort (clean, SM-03): 0 of 42 entities alerted by any pattern.**

How to read this honestly:
- The positives are few (2–10 per pattern in this profile) and are the generator's own scenarios, so
  this shows the templates match the FRD definitions, not that they detect real-world behaviour (L-01).
  SM-05 (≥ 90% recall) should be re-measured on the `full` profile.
- **Volume.** P01 (161 alerts for 46 entities), P07 (48) and P10 (46) raise far more alerts than the
  labelled scenarios explain. For P01 that is 0.8% of all transactions: 93 alerts from the absolute
  threshold alone, 62 from the relative condition alone and 6 from both. These are exactly the numbers a
  simulation should put in front of the mentor before the defaults are accepted (FRD §5.3: a
  projection above 5% of transactions blocks submission).
- Look-alike entities are the generator's legitimate behaviour (arisan collectors, payroll, kiosks).
  Some are alerted (expected false positives, FRD §8.0); the point of each alert is that the factors
  show why.

## Tests

`tests/test_detection_patterns.py` — five kinds of test per pattern as TRD §15.3 asks (positive,
look-alike negative, exact boundary, complete explanation, not enough data), plus: every seeded version
is accepted by its template, a missing parameter is refused, a run raises alerts and is idempotent, the
run endpoints (all patterns, one pattern, unknown pattern → 404, role guard), and simulation of a DRAFT
version writes no alert. `tests/test_p02_rule_seed.py` was updated for the nine new seeded rules.
The numbers above come from `python -m app.scripts.evaluate_patterns --raw-dir <dataset>` (read-only against
the database it points at, after the dataset has been loaded and ingested). It is not yet a pytest.

## Known limitations of this first version

- Aggregation is in Python over one pass of the transactions (about 2 s for 20k rows). The TRD asks for
  set-based SQL and a 30-minute target at 420k–500k transactions; measure on the `full` profile (T-PERF-04)
  before relying on it.
- Severity differs per condition in the FRD (P01 HIGH/MEDIUM, P05 MEDIUM/HIGH, P07, P08, P09): a rule
  version has one severity, so the alert records `SUGGESTED_SEVERITY`; priority scoring (FR-311) does not
  use it yet.
- Concurrent patterns on one subject (FRD §8.13) are not yet written to `concurrent_patterns`.
- P09 is device-scoped: one alert per device and window, attached to the entity with most transactions on
  it; all entities are listed in `ENTITY_IDS`.
- Rule versions are seeded ACTIVE without maker-checker (stated in each version's change summary and in
  an audit event `RULE_SEEDED_ACTIVE`) until FR-304 exists.
- No frontend change: new alerts appear in the existing alert queue with their pattern code and factors.
