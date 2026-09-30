"""What the supervised models are compared against, on the same rows.

1. **The FRD §8 default rules**, evaluated offline on the feature matrices
   (the app implements only P02 so far). Parameters are FRD §8 defaults; where
   a rule's exact window cannot be read from one row of a matrix the
   approximation is named in RULES_NOTE.
2. **The TRD §10.2 unsupervised anomaly score** (FR-401): a documented
   weighted combination of clipped per-feature deviations from the entity's
   own baseline. The TRD leaves weights and clipping to documentation;
   ASSUMPTION AS-05: equal weights, |z| clipped at 5 and scaled to [0, 1],
   Jensen-Shannon divergences taken as they are (already in [0, 1]). Five of
   the TRD's seven baseline features exist in fs_v2 (TXN_VALUE_MEAN and
   GEO_EXPOSURE_SHARE deviations do not). No baseline (FR-401 E1) means no
   score, so NaN never flags.

Labels are used by no comparator except to choose its threshold on the
validation split, the same way as for the models.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from app.ml.features import week_end

# --- FRD §8 default rules --------------------------------------------------------------------

AML_RULES = {
    # P01: an amount above the absolute threshold, or 5x the entity's own p95 (FRD §8.1)
    "P01": lambda f: (f.TXN_VALUE_MAX_R7D > np.where(f.ENTITY_IS_BUSINESS == 1, 500e6, 100e6))
                     | (f.MAX_R7D_OVER_OWN_P95 >= 5),
    # P02: >= 3 in-band amounts adding up to >= the threshold in 7 rolling days (FRD §8.2)
    "P02": lambda f: (f.P02_BAND_COUNT_ROLLING_7D >= 3) & (f.P02_BAND_SUM_ROLLING_7D >= 500e6),
    # P03: >= 80% of a >= 50M credit out again within 24 h (FRD §8.3)
    "P03": lambda f: f.PASSTHROUGH_RATIO_MAX_R7D >= 0.80,
    # P04: >= 15 in 24 h and >= 4x the baseline (FRD §8.4; the week's ratio stands in for the day's)
    "P04": lambda f: (f.MAX_COUNT_ROLLING_24H >= 15) & (f.COUNT_R7D_OVER_BASELINE >= 4),
    # P05: an account quiet >= 90 days, then >= 25M or >= 5 transactions (FRD §8.5; the week for 7 days)
    "P05": lambda f: (f.LONGEST_ACCOUNT_GAP_DAYS_R7D >= 90) & ((f.TXN_VALUE_SUM_R7D >= 25e6) | (f.TXN_COUNT_R7D >= 5)),
    # P06: >= 5 round amounts >= 5M, >= 60% of those >= 5M, in 30 days (FRD §8.6)
    "P06": lambda f: (f.ROUND_COUNT_R30D >= 5) & (f.ROUND_SHARE_R30D >= 0.60),
    # P07: the week >= 4x the 12-week mean and >= 100M above it (FRD §8.7)
    "P07": lambda f: (f.WEEK_VALUE_OVER_BASELINE >= 4) & (f.WEEK_VALUE_INCREASE >= 100e6),
    # P08: >= 3 listed-geography transactions, or >= 50M, or >= 25% of value, in 30 days (FRD §8.8)
    "P08": lambda f: (f.GEO_EXPOSURE_COUNT_R30D >= 3) | (f.GEO_EXPOSURE_SUM_R30D >= 50e6)
                     | (f.GEO_EXPOSURE_SHARE_R30D >= 0.25),
    # P09: a device used by >= 4 entities or >= 6 accounts in 30 days (FRD §8.9)
    "P09": lambda f: (f.DEVICE_ENTITY_COUNT_R30D >= 4) | (f.DEVICE_ACCOUNT_COUNT_R30D >= 6),
    # P10: a merchant with >= 30 transactions in 30 days and any sub-condition (FRD §8.10)
    "P10": lambda f: (f.ENTITY_IS_BUSINESS == 1) & (f.TXN_COUNT_R30D >= 30)
                     & ((f.TICKET_MULTIPLE_R30D >= 3) | (f.TICKET_MULTIPLE_R30D <= 1 / 3)
                        | (f.REVERSAL_RATE_R30D > 0.15) | (f.PAYER_TOP5_SHARE_R30D > 0.70)
                        | (f.OFF_HOURS_SHARE_R30D > 0.60)),
    # P11: >= 8 senders, >= 200M, >= 60% inside 48 h, in 7 rolling days (FRD §8.11)
    "P11": lambda f: (f.FUNNEL_IN_DEGREE_ROLLING_7D >= 8) & (f.INBOUND_SUM_R7D >= 200e6)
                     & (f.TIME_COMPRESSION_R7D >= 0.60),
}

FRAUD_RULES = {
    # P04 at the moment of the transaction (FRD §8.4)
    "P04": lambda f: (f.COUNT_R24H >= 15) & (f.COUNT_R24H_OVER_DAILY_BASELINE >= 4),
    # P05 on the reactivating transaction: >= 90 quiet days on the account, >= 25M (FRD §8.5)
    "P05": lambda f: (f.DAYS_SINCE_PREV_TXN_ACCOUNT >= 90) & (f.AMOUNT >= 25e6),
    # A simple takeover rule of ours (ATO is not an FRD pattern), its three
    # signals: no activity in the 90 days before the last 24 hours (dormancy,
    # FRD §8.5's 90 days) after at least 90 days of history, a device this
    # customer first used within the day, and a burst of >= 3 outgoing rows
    "ATO": lambda f: (f.IS_OUTBOUND == 1) & (f.DEVICE_TENURE_DAYS < 1) & (f.COUNT_R24H >= 3)
                     & (f.PRIOR_TXN_COUNT_90D - (f.COUNT_R24H - 1) <= 0) & (f.HISTORY_DAYS >= 90),
}

RULES_NOTE = (
    "Offline approximations of the FRD §8 defaults on the feature matrices: AML rules per entity-week; "
    "P04's baseline ratio uses the week in the AML unit; P05's 5-transactions branch is not visible on a "
    "single Fraud row; the ATO rule is ours (ATO is not an FRD pattern)."
)


def rule_flags(model: str, frame: pd.DataFrame, patterns: tuple[str, ...] | None = None) -> pd.DataFrame:
    rules = AML_RULES if model == "aml" else FRAUD_RULES
    chosen = patterns or tuple(rules)
    return pd.DataFrame({p: rules[p](frame).fillna(False).astype(bool) for p in chosen if p in rules},
                        index=frame.index)


# --- TRD §10.2 unsupervised anomaly score (AS-05) --------------------------------------------

Z_COLUMNS = ("Z_TXN_COUNT_W", "Z_TXN_VALUE_W", "Z_DISTINCT_COUNTERPARTIES_W")
JSD_COLUMNS = ("CHANNEL_MIX_JSD", "HOUR_PROFILE_JSD")
Z_CLIP = 5.0


def anomaly_score(aml: pd.DataFrame) -> np.ndarray:
    """Per entity-week, in [0, 1]; NaN without a baseline."""
    parts = [np.minimum(aml[c].abs().to_numpy(), Z_CLIP) / Z_CLIP for c in Z_COLUMNS]
    parts += [np.clip(aml[c].to_numpy(), 0.0, 1.0) for c in JSD_COLUMNS]
    stacked = np.vstack(parts)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN column: no baseline, NaN score
        return np.nanmean(stacked, axis=0)


def anomaly_score_for_transactions(aml: pd.DataFrame, fraud: pd.DataFrame) -> np.ndarray:
    """The entity-week score mapped onto each transaction in that week. It sees
    the whole week, later transactions included, so it is optimistic as a
    per-transaction comparator; the TRD defines no per-transaction version."""
    weekly = dict(zip(zip(aml.entity_id, aml.as_of), anomaly_score(aml)))
    return np.array([weekly.get((e, week_end(ts)), np.nan) for e, ts in zip(fraud.entity_id, fraud.ts)])
