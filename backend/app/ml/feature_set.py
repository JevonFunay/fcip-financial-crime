"""Feature set fs_v1: the shared vocabulary between rules, risk and anomaly
(TRD §9.2, C-04), and the inputs of the two ML models.

A feature set is a versioned artefact (TRD §10.5, FEATURE_SET). Its parameters
are locked here with their source, and are deliberately NOT read from the
ACTIVE rule versions: when a rule's threshold changes, the model's inputs must
not change underneath it silently. A new parameter value is a new feature set
version.

Two units (PROJECT_CONTEXT §8):
- AML: one row per (entity, calendar week), as of Sunday 23:59:59 Asia/Jakarta,
  for weeks in which the entity transacted.
- FRAUD: one row per transaction, as of that transaction (itself included,
  nothing after it).

Short history is explicit, never silent: a feature that compares the present
with the entity's own past is NaN below its sufficiency threshold (each taken
from the FRD pattern that owns it), and the availability features say how much
history there was. See docs/ml/feature_set_fs_v1.md, rendered from this file.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

FEATURE_SET_VERSION = "fs_v1"
JAKARTA = timezone(timedelta(hours=7))
REFERENCE_DIR = Path(__file__).resolve().parents[1] / "reference"


@dataclass(frozen=True)
class Parameter:
    name: str
    value: object
    source: str


# Every number a feature depends on, with where it comes from. An assumption
# of ours is labelled as one (PROJECT_CONTEXT §8, "Our own assumptions").
PARAMETERS: tuple[Parameter, ...] = (
    Parameter("reporting_threshold_idr", 500_000_000, "FRD §8.2 REPORTING_THRESHOLD (MQ-02)"),
    Parameter("band_lower_ratio", 0.70, "FRD §8.2 BAND_LOWER"),
    Parameter("passthrough_min_inbound_idr", 50_000_000, "FRD §8.3 MIN_INBOUND_AMOUNT"),
    Parameter("passthrough_hours", 24, "FRD §8.3 MAX_ELAPSED_HOURS"),
    Parameter("rounding_unit_idr", 1_000_000, "FRD §8.6 ROUNDING_UNIT"),
    Parameter("round_min_amount_idr", 5_000_000, "FRD §8.6 MIN_AMOUNT"),
    Parameter("baseline_days", 90, "FRD §8.1/§8.4 and TRD §10.2: 90-day baseline"),
    Parameter("baseline_weeks", 12, "FRD §8.7: 12-week baseline"),
    Parameter("device_window_days", 30, "FRD §8.9: 30 days rolling"),
    Parameter("funnel_compression_hours", 48, "FRD §8.11: 48-hour compression sub-window"),
    Parameter("relative_min_history_txns", 20, "FRD §8.1 MIN_HISTORY_TXNS (relative-to-own-p95 features)"),
    Parameter("velocity_min_baseline_days", 30, "FRD §8.4 MIN_BASELINE_DAYS (velocity-against-baseline features)"),
    Parameter("spike_min_baseline_weeks", 6, "FRD §8.7 MIN_BASELINE_WEEKS (value-spike features)"),
    Parameter("anomaly_min_history_txns", 30, "FR-401 E1: 30 transactions ..."),
    Parameter("anomaly_min_history_days", 30, "FR-401 E1: ... over at least 30 days (NO_BASELINE below)"),
    Parameter("count_baseline_txns", 50,
              "Ours: a count-based baseline (the entity's last N transactions, however old), so an entity "
              "back from dormancy is still compared with its own past; the FRD's baselines are 90 days of "
              "time and are empty after a dormancy"),
    Parameter("min_history_days", 30,
              "Novelty features (a device, counterparty or merchant never seen before) are unknown below "
              "this much observed history; same value as FR-401 E1. Decided 30 Sep 2026"),
    Parameter("z_sd_floor_count", 1.0, "TRD §10.2 names a floor on the standard deviation without a value. "
              "ASSUMPTION AS-02: one transaction (or one counterparty) for count z-scores"),
    Parameter("z_sd_floor_value_fraction", 0.10, "ASSUMPTION AS-02: 10% of the baseline mean for value z-scores"),
    Parameter("geo_list", "geo_list_v1", "app/reference/geo_list_v1.csv: the organisation's configured list"),
    Parameter("ticket_band_midpoints_idr",
              {"LT_50K": 25_000, "50K_250K": 150_000, "250K_1M": 625_000, "GT_1M": 2_000_000},
              "merchants.csv declared_expected_ticket_band, taken at its midpoint (GT_1M at 2M); "
              "FRD §8.10 compares ticket size with the merchant's category band"),
)
P = {parameter.name: parameter.value for parameter in PARAMETERS}

SECONDS_PER_DAY = 86_400.0
HOUR = 3_600.0


def geo_list() -> frozenset[str]:
    """The organisation's configured high-risk geography list (FRD §8.8, TRD
    geo_list). A configuration of the organisation, like P02's threshold, not
    derived from the countries any scenario happens to use."""
    with (REFERENCE_DIR / "geo_list_v1.csv").open(encoding="utf-8") as handle:
        return frozenset(row["country_code"] for row in csv.DictReader(handle))


# --- feature definitions ---------------------------------------------------------------

# Sufficiency gates: below these a feature is NaN, not zero.
GATE_NONE = "always"
GATE_RELATIVE = "relative_min_history_txns"      # FRD §8.1
GATE_VELOCITY = "velocity_min_baseline_days"     # FRD §8.4
GATE_SPIKE = "spike_min_baseline_weeks"          # FRD §8.7
GATE_ANOMALY = "anomaly_min_history_*"           # FR-401 E1
GATE_NOVELTY = "min_history_days"                # our parameter, documented
GATE_BUSINESS = "business entities only"         # FRD §8.10 describes merchants


@dataclass(frozen=True)
class Feature:
    code: str
    unit: str          # "AML" or "FRAUD"
    window: str
    definition: str
    patterns: str      # the FRD patterns (or model) it serves
    gate: str = GATE_NONE


def _aml(code, window, definition, patterns, gate=GATE_NONE):
    return Feature(code, "AML", window, definition, patterns, gate)


def _fraud(code, window, definition, patterns, gate=GATE_NONE):
    return Feature(code, "FRAUD", window, definition, patterns, gate)


FEATURES: tuple[Feature, ...] = (
    # --- AML, per (entity, week) ---
    _aml("TXN_COUNT_R7D", "R7D", "Transactions", "P04, volume"),
    _aml("TXN_COUNT_R30D", "R30D", "Transactions", "P06, P10, volume"),
    _aml("TXN_COUNT_R90D", "R90D", "Transactions", "volume"),
    _aml("TXN_VALUE_SUM_R7D", "R7D", "Sum of amounts (IDR)", "P02, P07"),
    _aml("TXN_VALUE_SUM_R30D", "R30D", "Sum of amounts (IDR)", "P08"),
    _aml("TXN_VALUE_SUM_R90D", "R90D", "Sum of amounts (IDR)", "volume"),
    _aml("INBOUND_SUM_R7D", "R7D", "Sum of incoming amounts", "P03, P11"),
    _aml("OUTBOUND_SUM_R7D", "R7D", "Sum of outgoing amounts", "P03, P11"),
    _aml("INBOUND_SUM_R30D", "R30D", "Sum of incoming amounts", "volume"),
    _aml("OUTBOUND_SUM_R30D", "R30D", "Sum of outgoing amounts", "volume"),
    _aml("TXN_VALUE_MAX_R7D", "R7D", "Largest single amount", "P01"),
    _aml("MAX_R7D_OVER_OWN_P95", "R7D vs prior 90d",
         "Largest amount this week / p95 of the 90 days before the week", "P01", GATE_RELATIVE),
    _aml("P02_BAND_COUNT_R7D", "R7D",
         "Amounts in [band_lower_ratio x threshold, threshold)", "P02"),
    _aml("P02_BAND_SUM_R7D", "R7D", "Sum of in-band amounts", "P02"),
    _aml("P02_BAND_COUNT_ROLLING_7D", "rolling 7d ending this week",
         "Most in-band amounts in any 7 days ending at one of this week's rows (a cluster that straddles "
         "two calendar weeks is still one cluster)", "P02"),
    _aml("P02_BAND_SUM_ROLLING_7D", "rolling 7d ending this week", "Their sum, for the same window", "P02"),
    _aml("DISTINCT_ACCOUNTS_R7D", "R7D", "Own accounts used", "P02"),
    _aml("PASSTHROUGH_RATIO_MAX_R7D", "R7D",
         "Over credits >= passthrough_min_inbound: max share sent out again within passthrough_hours", "P03"),
    _aml("PASSTHROUGH_CREDITS_R7D", "R7D", "Credits >= passthrough_min_inbound", "P03"),
    _aml("MAX_DAILY_COUNT_R7D", "R7D", "Most transactions on one local day", "P04"),
    _aml("MAX_COUNT_ROLLING_24H", "rolling 24h ending this week", "Most transactions in any 24 hours", "P04"),
    _aml("COUNT_R7D_OVER_BASELINE", "R7D vs prior 12 weeks",
         "(count this week + 1) / (weekly mean over the 12 weeks before + 1)", "P04", GATE_VELOCITY),
    _aml("LONGEST_GAP_DAYS_R7D", "R7D", "Longest silence before any of this week's transactions (entity)", "P05"),
    _aml("LONGEST_ACCOUNT_GAP_DAYS_R7D", "R7D", "Same, per account (FRD §8.5 is per account)", "P05"),
    _aml("ROUND_COUNT_R30D", "R30D",
         "Amounts >= round_min_amount that are exact multiples of rounding_unit", "P06"),
    _aml("ROUND_SHARE_R30D", "R30D", "Round count / transactions >= round_min_amount", "P06"),
    _aml("WEEK_VALUE_OVER_BASELINE", "week vs prior 12 weeks",
         "Value this week / weekly mean value over the 12 weeks before", "P07", GATE_SPIKE),
    _aml("WEEK_VALUE_INCREASE", "week vs prior 12 weeks", "Value this week - that weekly mean (IDR)", "P07",
         GATE_SPIKE),
    _aml("GEO_EXPOSURE_COUNT_R30D", "R30D", "Transactions with a counterparty country on geo_list", "P08"),
    _aml("GEO_EXPOSURE_SUM_R30D", "R30D", "Their amount", "P08"),
    _aml("GEO_EXPOSURE_SHARE_R30D", "R30D", "Their share of the value", "P08"),
    _aml("DEVICE_ENTITY_COUNT_R30D", "R30D", "Most distinct entities on any device this entity used", "P09"),
    _aml("DEVICE_ACCOUNT_COUNT_R30D", "R30D", "Most distinct accounts on any device this entity used", "P09"),
    _aml("AVG_TICKET_R30D", "R30D", "Mean incoming merchant payment through the entity's own merchants", "P10",
         GATE_BUSINESS),
    _aml("TICKET_OVER_BAND", "R30D", "That mean / the merchants' declared ticket-band midpoint", "P10",
         GATE_BUSINESS),
    _aml("OFF_HOURS_SHARE_R30D", "R30D", "Share of those payments outside the merchant's declared hours", "P10",
         GATE_BUSINESS),
    _aml("PAYER_TOP5_SHARE_R30D", "R30D", "Share of incoming value from the five largest payers", "P10",
         GATE_BUSINESS),
    _aml("REVERSAL_RATE_R30D", "R30D", "Share of transactions the source reports as REVERSED", "P10"),
    _aml("FUNNEL_IN_DEGREE_R7D", "R7D", "Distinct counterparties sending money in", "P11"),
    _aml("FUNNEL_OUT_DEGREE_R7D", "R7D", "Distinct counterparties receiving money", "P11"),
    _aml("FUNNEL_IN_DEGREE_ROLLING_7D", "rolling 7d ending this week",
         "Most distinct senders in any 7 days ending at one of this week's incoming rows", "P11"),
    _aml("TIME_COMPRESSION_R7D", "R7D", "Largest share of incoming value inside any funnel_compression_hours",
         "P11"),
    _aml("INBOUND_AMOUNT_CV_R7D", "R7D", "Coefficient of variation of incoming amounts", "P11"),
    _aml("Z_TXN_COUNT_W", "week vs prior 12 weeks", "(count - weekly mean) / max(sd, z_sd_floor_count)",
         "anomaly (FR-401)", GATE_ANOMALY),
    _aml("Z_TXN_VALUE_W", "week vs prior 12 weeks",
         "(value - weekly mean) / max(sd, z_sd_floor_value_fraction x mean)", "anomaly (FR-401)", GATE_ANOMALY),
    _aml("Z_DISTINCT_COUNTERPARTIES_W", "week vs prior 12 weeks",
         "(distinct counterparties - weekly mean) / max(sd, z_sd_floor_count)", "anomaly (FR-401)", GATE_ANOMALY),
    _aml("CHANNEL_MIX_JSD", "R7D vs prior 90d", "Jensen-Shannon divergence of the channel mix", "anomaly (FR-401)",
         GATE_ANOMALY),
    _aml("HOUR_PROFILE_JSD", "R7D vs prior 90d", "Jensen-Shannon divergence of the hour profile (six 4-hour bins)",
         "anomaly (FR-401)", GATE_ANOMALY),
    _aml("BASELINE_CV", "prior 12 weeks", "sd / mean of weekly value: how stable the baseline is (FR-401 E2)",
         "anomaly (FR-401)", GATE_ANOMALY),
    _aml("NEW_COUNTERPARTY_SHARE_R7D", "R7D", "Share of this week's counterparties never seen before", "ATO, P11",
         GATE_NOVELTY),
    _aml("NEW_DEVICE_R7D", "R7D", "1 if a device never seen for this entity was used this week", "ATO",
         GATE_NOVELTY),
    _aml("HISTORY_DAYS", "point", "Days since the entity's first observed transaction", "availability"),
    _aml("HISTORY_TXN_COUNT", "point", "Transactions observed up to this point", "availability"),
    _aml("BASELINE_DAYS_AVAILABLE", "point", "Days of history before the week, capped at baseline_days",
         "availability (TRD §9.2 BASELINE_DAYS_AVAILABLE)"),
    _aml("BASELINE_TXN_COUNT", "prior 90d", "Transactions in the 90 days before the week", "availability"),
    _aml("ACCOUNT_AGE_DAYS", "point", "Days since the entity's oldest account was opened", "availability"),
    _aml("ENTITY_IS_BUSINESS", "static", "1 for a business customer", "availability"),
    # --- FRAUD, per transaction ---
    _fraud("AMOUNT", "row", "Amount (IDR)", "all"),
    _fraud("LOG_AMOUNT", "row", "log10(amount)", "all"),
    _fraud("IS_OUTBOUND", "row", "1 for money leaving the account", "ATO"),
    _fraud("CHANNEL_CODE", "row", "Channel, as its position in the TRD §6.1 allowed list", "all"),
    _fraud("TXN_TYPE_CODE", "row", "Transaction type, as its position in the TRD §6.1 allowed list", "all"),
    _fraud("LOCAL_HOUR", "row", "Hour of day, Asia/Jakarta", "all"),
    _fraud("AMOUNT_OVER_OWN_P95", "prior 90d", "Amount / p95 of the entity's prior 90 days", "P01, ATO",
           GATE_RELATIVE),
    _fraud("AMOUNT_OVER_OWN_MEDIAN", "prior 90d", "Amount / median of the entity's prior 90 days", "P01, ATO",
           GATE_RELATIVE),
    _fraud("DAYS_SINCE_PREV_TXN", "point", "Days since the entity's previous transaction", "P05, ATO"),
    _fraud("DAYS_SINCE_PREV_TXN_ACCOUNT", "point", "Days since this account's previous transaction", "P05, ATO"),
    _fraud("COUNT_R1H", "R1H", "Transactions in the last hour, this one included", "P04, ATO"),
    _fraud("COUNT_R24H", "R24H", "Transactions in the last 24 hours", "P04, ATO"),
    _fraud("OUT_SUM_R1H", "R1H", "Outgoing amount in the last hour", "ATO"),
    _fraud("OUT_SUM_R24H", "R24H", "Outgoing amount in the last 24 hours", "ATO"),
    _fraud("COUNT_R24H_OVER_DAILY_BASELINE", "R24H vs prior 90d",
           "(count in 24h + 1) / (daily mean over the prior 90 days + 1)", "P04, ATO", GATE_VELOCITY),
    _fraud("OUT_SUM_R24H_OVER_OWN_P95", "R24H vs prior 90d", "Outgoing amount in 24h / p95 of prior amounts", "ATO",
           GATE_RELATIVE),
    _fraud("AMOUNT_OVER_P95_LAST_N", "last count_baseline_txns rows",
           "Amount / p95 of the entity's previous count_baseline_txns transactions, however old", "ATO, P01",
           GATE_RELATIVE),
    _fraud("OUT_SUM_R24H_OVER_P95_LAST_N", "R24H vs last count_baseline_txns rows",
           "Outgoing amount in 24h / that p95", "ATO", GATE_RELATIVE),
    _fraud("DEVICE_PRESENT", "row", "1 if the row carries a device", "data quality"),
    _fraud("DEVICE_FIRST_USE", "point", "1 if this entity never used this device before", "ATO", GATE_NOVELTY),
    _fraud("DEVICE_TENURE_DAYS", "point", "Days since this entity first used this device", "ATO", GATE_NOVELTY),
    _fraud("COUNTERPARTY_FIRST_USE", "point", "1 if this entity never dealt with this counterparty before", "ATO",
           GATE_NOVELTY),
    _fraud("MERCHANT_FIRST_USE", "point", "1 if this entity never paid this merchant before", "ATO", GATE_NOVELTY),
    _fraud("NEW_COUNTERPARTIES_R24H", "R24H", "First-time counterparties in the last 24 hours", "ATO", GATE_NOVELTY),
    _fraud("DISTINCT_DEVICES_R24H", "R24H", "Distinct devices in the last 24 hours", "ATO, P09"),
    _fraud("DEVICE_ENTITY_COUNT_R30D", "R30D", "Distinct entities on this row's device", "P09"),
    _fraud("DEVICE_IS_EMULATOR", "static", "Device master data flag", "device risk"),
    _fraud("DEVICE_IS_ROOTED", "static", "Device master data flag", "device risk"),
    _fraud("COUNTERPARTY_COUNTRY_LISTED", "row", "1 if the counterparty country is on geo_list", "P08"),
    _fraud("HISTORY_DAYS", "point", "Days since the entity's first observed transaction", "availability"),
    _fraud("PRIOR_TXN_COUNT_90D", "prior 90d", "Transactions in the prior 90 days", "availability"),
    _fraud("ACCOUNT_AGE_DAYS", "point", "Days since the entity's oldest account was opened", "availability"),
    _fraud("ENTITY_IS_BUSINESS", "static", "1 for a business customer", "availability"),
)

AML_FEATURES = tuple(f.code for f in FEATURES if f.unit == "AML")
FRAUD_FEATURES = tuple(f.code for f in FEATURES if f.unit == "FRAUD")


def render_markdown() -> str:
    """docs/ml/feature_set_fs_v1.md, rendered from this module so it cannot drift."""
    lines = [
        f"# Feature set `{FEATURE_SET_VERSION}`",
        "",
        "Rendered from `backend/app/ml/feature_set.py`; do not edit by hand. TRD §9.2 (C-04), §10.5.",
        "",
        "Parameters are locked in the feature set with their source and are not read from the ACTIVE rule",
        "versions: a rule change must not change a model's inputs silently. A new value is a new version.",
        "",
        "A feature with a gate is **NaN** (not zero) below its sufficiency threshold; the availability",
        "features always say how much history there was.",
        "",
        "## Parameters",
        "",
        "| Parameter | Value | Source |",
        "|---|---|---|",
    ]
    for parameter in PARAMETERS:
        lines.append(f"| `{parameter.name}` | `{parameter.value}` | {parameter.source} |")
    for unit, title in (("AML", "AML model: one row per (entity, calendar week)"),
                        ("FRAUD", "Fraud model: one row per transaction")):
        lines += ["", f"## {title}", "", "| Feature | Window | Definition | Serves | NaN below |", "|---|---|---|---|---|"]
        for feature in FEATURES:
            if feature.unit == unit:
                gate = "" if feature.gate == GATE_NONE else f"`{feature.gate}`"
                lines.append(f"| `{feature.code}` | {feature.window} | {feature.definition} | {feature.patterns} | {gate} |")
    return "\n".join(lines) + "\n"
