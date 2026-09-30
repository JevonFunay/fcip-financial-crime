# Feature set `fs_v1`

Rendered from `backend/app/ml/feature_set.py`; do not edit by hand. TRD §9.2 (C-04), §10.5.

Parameters are locked in the feature set with their source and are not read from the ACTIVE rule
versions: a rule change must not change a model's inputs silently. A new value is a new version.

A feature with a gate is **NaN** (not zero) below its sufficiency threshold; the availability
features always say how much history there was.

## Parameters

| Parameter | Value | Source |
|---|---|---|
| `reporting_threshold_idr` | `500000000` | FRD §8.2 REPORTING_THRESHOLD (MQ-02) |
| `band_lower_ratio` | `0.7` | FRD §8.2 BAND_LOWER |
| `passthrough_min_inbound_idr` | `50000000` | FRD §8.3 MIN_INBOUND_AMOUNT |
| `passthrough_hours` | `24` | FRD §8.3 MAX_ELAPSED_HOURS |
| `rounding_unit_idr` | `1000000` | FRD §8.6 ROUNDING_UNIT |
| `round_min_amount_idr` | `5000000` | FRD §8.6 MIN_AMOUNT |
| `baseline_days` | `90` | FRD §8.1/§8.4 and TRD §10.2: 90-day baseline |
| `baseline_weeks` | `12` | FRD §8.7: 12-week baseline |
| `device_window_days` | `30` | FRD §8.9: 30 days rolling |
| `funnel_compression_hours` | `48` | FRD §8.11: 48-hour compression sub-window |
| `relative_min_history_txns` | `20` | FRD §8.1 MIN_HISTORY_TXNS (relative-to-own-p95 features) |
| `velocity_min_baseline_days` | `30` | FRD §8.4 MIN_BASELINE_DAYS (velocity-against-baseline features) |
| `spike_min_baseline_weeks` | `6` | FRD §8.7 MIN_BASELINE_WEEKS (value-spike features) |
| `anomaly_min_history_txns` | `30` | FR-401 E1: 30 transactions ... |
| `anomaly_min_history_days` | `30` | FR-401 E1: ... over at least 30 days (NO_BASELINE below) |
| `count_baseline_txns` | `50` | Ours: a count-based baseline (the entity's last N transactions, however old), so an entity back from dormancy is still compared with its own past; the FRD's baselines are 90 days of time and are empty after a dormancy |
| `min_history_days` | `30` | Novelty features (a device, counterparty or merchant never seen before) are unknown below this much observed history; same value as FR-401 E1. Decided 30 Sep 2026 |
| `z_sd_floor_count` | `1.0` | TRD §10.2 names a floor on the standard deviation without a value. ASSUMPTION AS-02: one transaction (or one counterparty) for count z-scores |
| `z_sd_floor_value_fraction` | `0.1` | ASSUMPTION AS-02: 10% of the baseline mean for value z-scores |
| `geo_list` | `geo_list_v1` | app/reference/geo_list_v1.csv: the organisation's configured list |
| `ticket_band_midpoints_idr` | `{'LT_50K': 25000, '50K_250K': 150000, '250K_1M': 625000, 'GT_1M': 2000000}` | merchants.csv declared_expected_ticket_band, taken at its midpoint (GT_1M at 2M); FRD §8.10 compares ticket size with the merchant's category band |

## AML model: one row per (entity, calendar week)

| Feature | Window | Definition | Serves | NaN below |
|---|---|---|---|---|
| `TXN_COUNT_R7D` | R7D | Transactions | P04, volume |  |
| `TXN_COUNT_R30D` | R30D | Transactions | P06, P10, volume |  |
| `TXN_COUNT_R90D` | R90D | Transactions | volume |  |
| `TXN_VALUE_SUM_R7D` | R7D | Sum of amounts (IDR) | P02, P07 |  |
| `TXN_VALUE_SUM_R30D` | R30D | Sum of amounts (IDR) | P08 |  |
| `TXN_VALUE_SUM_R90D` | R90D | Sum of amounts (IDR) | volume |  |
| `INBOUND_SUM_R7D` | R7D | Sum of incoming amounts | P03, P11 |  |
| `OUTBOUND_SUM_R7D` | R7D | Sum of outgoing amounts | P03, P11 |  |
| `INBOUND_SUM_R30D` | R30D | Sum of incoming amounts | volume |  |
| `OUTBOUND_SUM_R30D` | R30D | Sum of outgoing amounts | volume |  |
| `TXN_VALUE_MAX_R7D` | R7D | Largest single amount | P01 |  |
| `MAX_R7D_OVER_OWN_P95` | R7D vs prior 90d | Largest amount this week / p95 of the 90 days before the week | P01 | `relative_min_history_txns` |
| `P02_BAND_COUNT_R7D` | R7D | Amounts in [band_lower_ratio x threshold, threshold) | P02 |  |
| `P02_BAND_SUM_R7D` | R7D | Sum of in-band amounts | P02 |  |
| `P02_BAND_COUNT_ROLLING_7D` | rolling 7d ending this week | Most in-band amounts in any 7 days ending at one of this week's rows (a cluster that straddles two calendar weeks is still one cluster) | P02 |  |
| `P02_BAND_SUM_ROLLING_7D` | rolling 7d ending this week | Their sum, for the same window | P02 |  |
| `DISTINCT_ACCOUNTS_R7D` | R7D | Own accounts used | P02 |  |
| `PASSTHROUGH_RATIO_MAX_R7D` | R7D | Over credits >= passthrough_min_inbound: max share sent out again within passthrough_hours | P03 |  |
| `PASSTHROUGH_CREDITS_R7D` | R7D | Credits >= passthrough_min_inbound | P03 |  |
| `MAX_DAILY_COUNT_R7D` | R7D | Most transactions on one local day | P04 |  |
| `MAX_COUNT_ROLLING_24H` | rolling 24h ending this week | Most transactions in any 24 hours | P04 |  |
| `COUNT_R7D_OVER_BASELINE` | R7D vs prior 12 weeks | (count this week + 1) / (weekly mean over the 12 weeks before + 1) | P04 | `velocity_min_baseline_days` |
| `LONGEST_GAP_DAYS_R7D` | R7D | Longest silence before any of this week's transactions (entity) | P05 |  |
| `LONGEST_ACCOUNT_GAP_DAYS_R7D` | R7D | Same, per account (FRD §8.5 is per account) | P05 |  |
| `ROUND_COUNT_R30D` | R30D | Amounts >= round_min_amount that are exact multiples of rounding_unit | P06 |  |
| `ROUND_SHARE_R30D` | R30D | Round count / transactions >= round_min_amount | P06 |  |
| `WEEK_VALUE_OVER_BASELINE` | week vs prior 12 weeks | Value this week / weekly mean value over the 12 weeks before | P07 | `spike_min_baseline_weeks` |
| `WEEK_VALUE_INCREASE` | week vs prior 12 weeks | Value this week - that weekly mean (IDR) | P07 | `spike_min_baseline_weeks` |
| `GEO_EXPOSURE_COUNT_R30D` | R30D | Transactions with a counterparty country on geo_list | P08 |  |
| `GEO_EXPOSURE_SUM_R30D` | R30D | Their amount | P08 |  |
| `GEO_EXPOSURE_SHARE_R30D` | R30D | Their share of the value | P08 |  |
| `DEVICE_ENTITY_COUNT_R30D` | R30D | Most distinct entities on any device this entity used | P09 |  |
| `DEVICE_ACCOUNT_COUNT_R30D` | R30D | Most distinct accounts on any device this entity used | P09 |  |
| `AVG_TICKET_R30D` | R30D | Mean incoming merchant payment through the entity's own merchants | P10 | `business entities only` |
| `TICKET_OVER_BAND` | R30D | That mean / the merchants' declared ticket-band midpoint | P10 | `business entities only` |
| `OFF_HOURS_SHARE_R30D` | R30D | Share of those payments outside the merchant's declared hours | P10 | `business entities only` |
| `PAYER_TOP5_SHARE_R30D` | R30D | Share of incoming value from the five largest payers | P10 | `business entities only` |
| `REVERSAL_RATE_R30D` | R30D | Share of transactions the source reports as REVERSED | P10 |  |
| `FUNNEL_IN_DEGREE_R7D` | R7D | Distinct counterparties sending money in | P11 |  |
| `FUNNEL_OUT_DEGREE_R7D` | R7D | Distinct counterparties receiving money | P11 |  |
| `FUNNEL_IN_DEGREE_ROLLING_7D` | rolling 7d ending this week | Most distinct senders in any 7 days ending at one of this week's incoming rows | P11 |  |
| `TIME_COMPRESSION_R7D` | R7D | Largest share of incoming value inside any funnel_compression_hours | P11 |  |
| `INBOUND_AMOUNT_CV_R7D` | R7D | Coefficient of variation of incoming amounts | P11 |  |
| `Z_TXN_COUNT_W` | week vs prior 12 weeks | (count - weekly mean) / max(sd, z_sd_floor_count) | anomaly (FR-401) | `anomaly_min_history_*` |
| `Z_TXN_VALUE_W` | week vs prior 12 weeks | (value - weekly mean) / max(sd, z_sd_floor_value_fraction x mean) | anomaly (FR-401) | `anomaly_min_history_*` |
| `Z_DISTINCT_COUNTERPARTIES_W` | week vs prior 12 weeks | (distinct counterparties - weekly mean) / max(sd, z_sd_floor_count) | anomaly (FR-401) | `anomaly_min_history_*` |
| `CHANNEL_MIX_JSD` | R7D vs prior 90d | Jensen-Shannon divergence of the channel mix | anomaly (FR-401) | `anomaly_min_history_*` |
| `HOUR_PROFILE_JSD` | R7D vs prior 90d | Jensen-Shannon divergence of the hour profile (six 4-hour bins) | anomaly (FR-401) | `anomaly_min_history_*` |
| `BASELINE_CV` | prior 12 weeks | sd / mean of weekly value: how stable the baseline is (FR-401 E2) | anomaly (FR-401) | `anomaly_min_history_*` |
| `NEW_COUNTERPARTY_SHARE_R7D` | R7D | Share of this week's counterparties never seen before | ATO, P11 | `min_history_days` |
| `NEW_DEVICE_R7D` | R7D | 1 if a device never seen for this entity was used this week | ATO | `min_history_days` |
| `HISTORY_DAYS` | point | Days since the entity's first observed transaction | availability |  |
| `HISTORY_TXN_COUNT` | point | Transactions observed up to this point | availability |  |
| `BASELINE_DAYS_AVAILABLE` | point | Days of history before the week, capped at baseline_days | availability (TRD §9.2 BASELINE_DAYS_AVAILABLE) |  |
| `BASELINE_TXN_COUNT` | prior 90d | Transactions in the 90 days before the week | availability |  |
| `ACCOUNT_AGE_DAYS` | point | Days since the entity's oldest account was opened | availability |  |
| `ENTITY_IS_BUSINESS` | static | 1 for a business customer | availability |  |

## Fraud model: one row per transaction

| Feature | Window | Definition | Serves | NaN below |
|---|---|---|---|---|
| `AMOUNT` | row | Amount (IDR) | all |  |
| `LOG_AMOUNT` | row | log10(amount) | all |  |
| `IS_OUTBOUND` | row | 1 for money leaving the account | ATO |  |
| `CHANNEL_CODE` | row | Channel, as its position in the TRD §6.1 allowed list | all |  |
| `TXN_TYPE_CODE` | row | Transaction type, as its position in the TRD §6.1 allowed list | all |  |
| `LOCAL_HOUR` | row | Hour of day, Asia/Jakarta | all |  |
| `AMOUNT_OVER_OWN_P95` | prior 90d | Amount / p95 of the entity's prior 90 days | P01, ATO | `relative_min_history_txns` |
| `AMOUNT_OVER_OWN_MEDIAN` | prior 90d | Amount / median of the entity's prior 90 days | P01, ATO | `relative_min_history_txns` |
| `DAYS_SINCE_PREV_TXN` | point | Days since the entity's previous transaction | P05, ATO |  |
| `DAYS_SINCE_PREV_TXN_ACCOUNT` | point | Days since this account's previous transaction | P05, ATO |  |
| `COUNT_R1H` | R1H | Transactions in the last hour, this one included | P04, ATO |  |
| `COUNT_R24H` | R24H | Transactions in the last 24 hours | P04, ATO |  |
| `OUT_SUM_R1H` | R1H | Outgoing amount in the last hour | ATO |  |
| `OUT_SUM_R24H` | R24H | Outgoing amount in the last 24 hours | ATO |  |
| `COUNT_R24H_OVER_DAILY_BASELINE` | R24H vs prior 90d | (count in 24h + 1) / (daily mean over the prior 90 days + 1) | P04, ATO | `velocity_min_baseline_days` |
| `OUT_SUM_R24H_OVER_OWN_P95` | R24H vs prior 90d | Outgoing amount in 24h / p95 of prior amounts | ATO | `relative_min_history_txns` |
| `AMOUNT_OVER_P95_LAST_N` | last count_baseline_txns rows | Amount / p95 of the entity's previous count_baseline_txns transactions, however old | ATO, P01 | `relative_min_history_txns` |
| `OUT_SUM_R24H_OVER_P95_LAST_N` | R24H vs last count_baseline_txns rows | Outgoing amount in 24h / that p95 | ATO | `relative_min_history_txns` |
| `DEVICE_PRESENT` | row | 1 if the row carries a device | data quality |  |
| `DEVICE_FIRST_USE` | point | 1 if this entity never used this device before | ATO | `min_history_days` |
| `DEVICE_TENURE_DAYS` | point | Days since this entity first used this device | ATO | `min_history_days` |
| `COUNTERPARTY_FIRST_USE` | point | 1 if this entity never dealt with this counterparty before | ATO | `min_history_days` |
| `MERCHANT_FIRST_USE` | point | 1 if this entity never paid this merchant before | ATO | `min_history_days` |
| `NEW_COUNTERPARTIES_R24H` | R24H | First-time counterparties in the last 24 hours | ATO | `min_history_days` |
| `DISTINCT_DEVICES_R24H` | R24H | Distinct devices in the last 24 hours | ATO, P09 |  |
| `DEVICE_ENTITY_COUNT_R30D` | R30D | Distinct entities on this row's device | P09 |  |
| `DEVICE_IS_EMULATOR` | static | Device master data flag | device risk |  |
| `DEVICE_IS_ROOTED` | static | Device master data flag | device risk |  |
| `COUNTERPARTY_COUNTRY_LISTED` | row | 1 if the counterparty country is on geo_list | P08 |  |
| `HISTORY_DAYS` | point | Days since the entity's first observed transaction | availability |  |
| `PRIOR_TXN_COUNT_90D` | prior 90d | Transactions in the prior 90 days | availability |  |
| `ACCOUNT_AGE_DAYS` | point | Days since the entity's oldest account was opened | availability |  |
| `ENTITY_IS_BUSINESS` | static | 1 for a business customer | availability |  |
