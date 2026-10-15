"""Point-in-time behavioural observations, without fraud labels or invented facts.

AML is an account-week observation; FRAUD is a transaction observation. These
are storage/API units, not predictions of a crime type. Historical comparisons
need 30 prior observations over 30 observed days. Day-only transactions never
gain an intraday ordering: their history ends before their source date, and
their values become available to subsequent days only after that date ends.
Amounts stay in their explicitly stated source currency; no FX is performed.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
import pandas as pd

FEATURE_SET_VERSION = "behavioral_v1"
DAY_NS = 86_400_000_000_000
MIN_HISTORY_COUNT = 30
MIN_HISTORY_DAYS = 30

_COMMON = {
    "HISTORY_DAYS": "Days since the account's first observed transaction; not account opening age.",
    "PRIOR_HISTORY_COUNT": "Transactions strictly available before this observation; same-day ordering is unknown for day-only rows.",
    "NO_BASELINE": "1 when fewer than 30 prior observations or fewer than 30 observed days are available; 0 otherwise.",
    "PRIOR_COUNT_R7D": "Prior observed transaction count over seven source-clock days, excluding the current observation.",
    "PRIOR_VALUE_R7D": "Their amount sum in the unchanged source currency; no FX conversion.",
    "PRIOR_COUNT_R30D": "Prior observed transaction count over thirty source-clock days.",
    "PRIOR_VALUE_R30D": "Their amount sum in the unchanged source currency.",
    "PRIOR_DEBIT_VALUE_R7D": "Prior outgoing amount over seven source-clock days.",
    "PRIOR_SMALL_COUNT_R7D": "Prior transactions below the training amount's 25th percentile, over seven source-clock days.",
}
_FRAUD = {
    "AMOUNT": "Observed transaction amount in source currency; no IDR conversion.",
    "LOG_AMOUNT": "Natural logarithm of one plus the observed amount.",
    "AMOUNT_OVER_TRAIN_MEDIAN": "Amount divided by the frozen training-observation median amount.",
    "IS_DEBIT": "1 for an observed debit, 0 for a credit.",
    "CHANNEL_TRAIN_FREQUENCY": "This channel's frequency in training observations; 0 for an unseen channel.",
    "CHANNEL_UNSEEN": "1 if this channel was absent from the frozen training vocabulary.",
    "LOGIN_ATTEMPTS": "Login attempts recorded on this source transaction; missing remains unknown.",
    "TRANSACTION_DURATION": "Duration recorded by the source; the source field's unit is not inferred.",
    "ACCOUNT_BALANCE": "Recorded balance snapshot; no pre/post-transaction interpretation is inferred.",
    "BALANCE_OVER_PRIOR_MEAN": "Recorded balance divided by prior recorded mean balance; unavailable with insufficient history or zero denominator.",
    "DAYS_SINCE_PREV_OBSERVATION": "Source-clock days since the latest strictly prior available observation.",
    "AMOUNT_OVER_PRIOR_MEAN": "Amount divided by the prior ninety-day mean; unknown below the 30-observation/30-day history gate.",
    "AMOUNT_Z_PRIOR": "Amount deviation divided by prior ninety-day standard deviation; unknown below the history gate or with zero deviation.",
    "COUNT_R1H": "Strictly earlier exact-time observations in the prior source-clock hour plus this row; unknown for day-only timestamps.",
    "COUNT_R24H": "Strictly earlier exact-time observations in the prior source-clock 24 hours plus this row; unknown for day-only timestamps.",
    "DEBIT_VALUE_R24H": "Their outgoing value plus this row's debit value; unknown for day-only timestamps.",
    "UTC_HOUR_SIN": "Sine of UTC hour, only for an offset-known timestamp with intraday precision.",
    "UTC_HOUR_COS": "Cosine of UTC hour, only for an offset-known timestamp with intraday precision.",
    "DAY_ONLY_TIMESTAMP": "1 when source precision is a day, 0 when a time was supplied.",
    "DEVICE_PRESENT": "1 if the source supplied a device reference, otherwise 0.",
    "IP_PRESENT": "1 if the source supplied an IP address; no geolocation is inferred.",
    "LOCATION_PRESENT": "1 if the source supplied a location string; no distance or country is inferred.",
    "DEVICE_FIRST_USE": "1 if the device has not appeared in this account's strictly prior available history; unknown when device/history is unavailable.",
    "IP_FIRST_USE": "Same prior-history novelty observation for the IP address; the raw address is never a model input.",
    "LOCATION_FIRST_USE": "Same prior-history novelty observation for the source location; no travel conclusion is inferred.",
    "MERCHANT_FIRST_USE": "Same prior-history novelty observation for merchant identity; no merchant ownership or counterparty is inferred.",
}
_AML = {
    "TXN_COUNT_W": "Observed transactions in this account's UTC-carrier calendar week, ending Sunday.",
    "TXN_VALUE_W": "Their amount sum in source currency, without FX conversion.",
    "DEBIT_VALUE_W": "Their outgoing amount sum.",
    "CREDIT_VALUE_W": "Their incoming amount sum.",
    "NET_INFLOW_W": "Recorded credit amount minus debit amount for the observed week.",
    "DEBIT_SHARE_W": "Share of weekly amount recorded as outgoing, unknown when the weekly amount is zero.",
    "AMOUNT_MEAN_W": "Mean recorded weekly amount in source currency.",
    "AMOUNT_MAX_W": "Largest recorded weekly amount.",
    "SMALL_AMOUNT_SHARE_W": "Fraction of weekly transactions below the frozen training amount's 25th percentile.",
    "COUNT_OVER_PRIOR_WEEKLY_MEAN": "Weekly count divided by prior observed thirty-day count scaled to seven days; unknown below the history gate.",
    "VALUE_OVER_PRIOR_WEEKLY_MEAN": "Weekly amount divided by the similarly scaled prior thirty-day amount; unknown below the history gate.",
    "LOGIN_ATTEMPTS_MEAN_W": "Mean source-recorded login attempts for the week, ignoring missing observations.",
    "LOGIN_ATTEMPTS_MAX_W": "Largest recorded login-attempt count for the week.",
    "DURATION_MEAN_W": "Mean recorded transaction duration for the week; no unit is inferred.",
    "DURATION_MAX_W": "Largest recorded transaction duration for the week.",
    "BALANCE_MEAN_W": "Mean recorded balance snapshot; not a reconstructed running balance.",
    "DISTINCT_DEVICES_W": "Distinct supplied device references in the week; missing if no device was supplied.",
    "DISTINCT_IPS_W": "Distinct supplied IP addresses in the week; no country is inferred.",
    "DISTINCT_LOCATIONS_W": "Distinct supplied location strings in the week.",
    "DEVICE_NEW_SHARE_W": "Share of supplied device observations absent before this week; unknown below the history gate.",
    "IP_NEW_SHARE_W": "Same weekly novelty share for supplied IP addresses.",
    "LOCATION_NEW_SHARE_W": "Same weekly novelty share for supplied source location strings.",
    "DAY_ONLY_SHARE_W": "Share of weekly transactions whose source timestamp has day-only precision.",
}
FEATURE_DEFINITIONS = {(unit, code): definition for unit, definitions in
                       (("AML", {**_COMMON, **_AML}), ("FRAUD", {**_COMMON, **_FRAUD}))
                       for code, definition in definitions.items()}


class FeatureInputError(ValueError):
    pass


def feature_names(unit: Literal["AML", "FRAUD"] | str) -> list[str]:
    unit = unit.upper()
    if unit not in {"AML", "FRAUD"}:
        raise FeatureInputError("unit must be AML or FRAUD")
    return [code for owner, code in FEATURE_DEFINITIONS if owner == unit]


def _prepared(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"account_number", "transaction_ref", "transaction_date", "amount", "currency", "direction", "channel"}
    if not required.issubset(frame.columns):
        raise FeatureInputError(f"Missing canonical fields: {', '.join(sorted(required - set(frame.columns)))}")
    f = frame.copy().reset_index(drop=True)
    f["transaction_date"] = pd.to_datetime(f.transaction_date, utc=True, errors="raise")
    f["amount"] = pd.to_numeric(f.amount, errors="coerce").astype("float64")
    if f.transaction_date.isna().any() or not np.isfinite(f.amount).all() or (f.amount <= 0).any():
        raise FeatureInputError("Features require accepted finite canonical observations")
    if f.transaction_ref.duplicated().any() or f.account_number.isna().any():
        raise FeatureInputError("Transaction references must be unique and accounts present")
    if f.currency.isna().any() or f.currency.nunique() > 1:
        raise FeatureInputError("A feature build cannot mix currency units or silently perform FX")
    for name in ("device_ref", "merchant_ref", "ip_address", "location"):
        f[name] = f[name].fillna("").astype(str) if name in f else ""
    for name in ("login_attempts", "transaction_duration", "account_balance"):
        f[name] = pd.to_numeric(f[name], errors="coerce") if name in f else math.nan
    # DB callers without precision metadata must not acquire an exact time
    # merely because the database uses midnight as its timestamp carrier.
    f["timestamp_precision"] = f.timestamp_precision.fillna("day") if "timestamp_precision" in f else "day"
    if not f.timestamp_precision.isin(["day", "minute", "second"]).all():
        raise FeatureInputError("Timestamp precision must be day, minute or second")
    if "source_timezone_known" in f:
        known = f.source_timezone_known.fillna(False)
        if not known.map(lambda value: isinstance(value, (bool, np.bool_))).all():
            raise FeatureInputError("Canonical source_timezone_known values must be booleans")
        f["source_timezone_known"] = known.astype(bool)
    else:
        f["source_timezone_known"] = False
    f["_time"] = f.transaction_date.astype("int64")
    f["_day"] = f.transaction_date.dt.floor("D").astype("int64")
    # A date-only value becomes available after its whole day ends, not at fabricated midnight.
    f["_available"] = np.where(f.timestamp_precision.eq("day"), f._day + DAY_NS - 1, f._time)
    f["_debit"] = np.where(f.direction.eq("DEBIT"), f.amount, 0.0)
    return f


def fit_vocabulary(frame: pd.DataFrame) -> dict:
    f = _prepared(frame)
    if f.empty:
        raise FeatureInputError("Cannot fit a vocabulary on an empty training frame")
    counts = f.channel.value_counts()
    return {
        "feature_set_version": FEATURE_SET_VERSION,
        "currency": str(f.currency.iloc[0]),
        "channels": {str(k): float(v / len(f)) for k, v in sorted(counts.items())},
        "amount_median": float(f.amount.median()),
        "small_amount_threshold": float(f.amount.quantile(0.25)),
        "small_amount_definition": "training observations' amount 25th percentile; descriptive, not a reporting threshold",
        "min_history_count": MIN_HISTORY_COUNT, "min_history_days": MIN_HISTORY_DAYS,
        "training_observations": len(f),
    }


def _divide(numerator, denominator) -> np.ndarray:
    a, b = np.asarray(numerator, dtype=float), np.asarray(denominator, dtype=float)
    return np.divide(a, b, out=np.full(np.broadcast(a, b).shape, np.nan), where=np.isfinite(b) & (b != 0))


def _history(f: pd.DataFrame, queries: pd.DataFrame, vocabulary: dict) -> pd.DataFrame:
    columns = [*_COMMON, "_prior_mean", "_prior_sd", "_prior_balance", "_gap", "_count1h", "_count24h", "_debit24h"]
    result = pd.DataFrame(np.nan, index=queries.index, columns=columns)
    groups = {account: group.sort_values(["_available", "transaction_ref"], kind="stable")
              for account, group in f.groupby("account_number", sort=False)}
    for account, query in queries.groupby("account_number", sort=False):
        h = groups[account]
        times = h._available.to_numpy(dtype=np.int64)
        cut = query._cut.to_numpy(dtype=np.int64)
        hi = np.searchsorted(times, cut, side="left")
        amount = h.amount.to_numpy(dtype=float)
        values = {
            "amount": amount, "square": amount * amount, "debit": h._debit.to_numpy(dtype=float),
            "small": (amount < vocabulary["small_amount_threshold"]).astype(float),
            "balance": h.account_balance.fillna(0).to_numpy(dtype=float),
            "balance_count": h.account_balance.notna().to_numpy(dtype=float),
        }
        cumulative = {key: np.r_[0.0, np.cumsum(value)] for key, value in values.items()}

        def total(key: str, lo):
            return cumulative[key][hi] - cumulative[key][lo]

        # The first observation must also be available before this query.
        # A same-day date-only row becomes available after an exact-time row,
        # even though its midnight storage carrier sorts earlier by _time.
        earliest_prior = np.minimum.accumulate(h._time.to_numpy(dtype=np.int64))[np.maximum(hi - 1, 0)]
        history_days = np.where(hi > 0, np.maximum(0.0, (cut - earliest_prior) / DAY_NS), 0.0)
        result.loc[query.index, "HISTORY_DAYS"] = history_days
        result.loc[query.index, "PRIOR_HISTORY_COUNT"] = hi
        baseline = (hi >= MIN_HISTORY_COUNT) & (history_days >= MIN_HISTORY_DAYS)
        result.loc[query.index, "NO_BASELINE"] = (~baseline).astype(float)
        for days in (7, 30):
            lo = np.searchsorted(times, cut - days * DAY_NS, side="left")
            result.loc[query.index, f"PRIOR_COUNT_R{days}D"] = hi - lo
            result.loc[query.index, f"PRIOR_VALUE_R{days}D"] = total("amount", lo)
            if days == 7:
                result.loc[query.index, "PRIOR_DEBIT_VALUE_R7D"] = total("debit", lo)
                result.loc[query.index, "PRIOR_SMALL_COUNT_R7D"] = total("small", lo)
        lo90 = np.searchsorted(times, cut - 90 * DAY_NS, side="left")
        count90 = hi - lo90
        mean = _divide(total("amount", lo90), count90)
        variance = np.maximum(0, _divide(total("square", lo90), count90) - mean * mean)
        gate = baseline & (count90 >= MIN_HISTORY_COUNT)
        result.loc[query.index, "_prior_mean"] = np.where(gate, mean, np.nan)
        result.loc[query.index, "_prior_sd"] = np.where(gate, np.sqrt(variance), np.nan)
        result.loc[query.index, "_prior_balance"] = np.where(
            gate, _divide(total("balance", lo90), total("balance_count", lo90)), np.nan)
        previous = h._time.to_numpy(dtype=np.int64)[np.maximum(hi - 1, 0)]
        result.loc[query.index, "_gap"] = np.where(hi > 0, (cut - previous) / DAY_NS, np.nan)
        # Intraday windows use only source rows that actually supplied a time.
        exact = h.timestamp_precision.ne("day").to_numpy(dtype=float)
        exact_cumulative = np.r_[0.0, np.cumsum(exact)]
        debit_exact_cumulative = np.r_[0.0, np.cumsum(h._debit.to_numpy(dtype=float) * exact)]
        for hours in (1, 24):
            lo = np.searchsorted(times, cut - hours * DAY_NS // 24, side="left")
            result.loc[query.index, f"_count{hours}h"] = exact_cumulative[hi] - exact_cumulative[lo]
            if hours == 24:
                result.loc[query.index, "_debit24h"] = debit_exact_cumulative[hi] - debit_exact_cumulative[lo]
    return result


def _first_seen(f: pd.DataFrame, column: str) -> pd.Series:
    return f.groupby(["account_number", column], sort=False)._available.transform("min")


def _fraud(f: pd.DataFrame, vocabulary: dict) -> pd.DataFrame:
    query = f[["account_number", "transaction_ref", "transaction_date"]].copy()
    query["_cut"] = np.where(f.timestamp_precision.eq("day"), f._day, f._time)
    history = _history(f, query, vocabulary)
    out = pd.DataFrame({"account_number": f.account_number, "transaction_ref": f.transaction_ref,
                        "as_of": f.transaction_date})
    for name in _COMMON:
        out[name] = history[name]
    out["AMOUNT"] = f.amount
    out["LOG_AMOUNT"] = np.log1p(f.amount)
    out["AMOUNT_OVER_TRAIN_MEDIAN"] = _divide(f.amount, vocabulary["amount_median"])
    out["IS_DEBIT"] = f.direction.eq("DEBIT").astype(float)
    out["CHANNEL_TRAIN_FREQUENCY"] = f.channel.map(vocabulary["channels"]).fillna(0.0)
    out["CHANNEL_UNSEEN"] = (~f.channel.isin(vocabulary["channels"])).astype(float)
    out["LOGIN_ATTEMPTS"] = f.login_attempts
    out["TRANSACTION_DURATION"] = f.transaction_duration
    out["ACCOUNT_BALANCE"] = f.account_balance
    out["BALANCE_OVER_PRIOR_MEAN"] = _divide(f.account_balance, history._prior_balance)
    out["DAYS_SINCE_PREV_OBSERVATION"] = history._gap
    out["AMOUNT_OVER_PRIOR_MEAN"] = _divide(f.amount, history._prior_mean)
    out["AMOUNT_Z_PRIOR"] = _divide(f.amount - history._prior_mean, history._prior_sd)
    exact = f.timestamp_precision.ne("day")
    out["COUNT_R1H"] = (history._count1h + 1).where(exact)
    out["COUNT_R24H"] = (history._count24h + 1).where(exact)
    out["DEBIT_VALUE_R24H"] = (history._debit24h + f._debit).where(exact)
    hour_known = exact & f.source_timezone_known
    hour = f.transaction_date.dt.hour + f.transaction_date.dt.minute / 60
    out["UTC_HOUR_SIN"] = np.sin(2 * np.pi * hour / 24).where(hour_known)
    out["UTC_HOUR_COS"] = np.cos(2 * np.pi * hour / 24).where(hour_known)
    out["DAY_ONLY_TIMESTAMP"] = (~exact).astype(float)
    baseline = history.NO_BASELINE.eq(0)
    for col, prefix in (("device_ref", "DEVICE"), ("ip_address", "IP"), ("location", "LOCATION"), ("merchant_ref", "MERCHANT")):
        present = f[col].ne("")
        if prefix != "MERCHANT":
            out[f"{prefix}_PRESENT"] = present.astype(float)
        out[f"{prefix}_FIRST_USE"] = (_first_seen(f, col) >= query._cut).astype(float).where(present & baseline)
    return out


def _aml(f: pd.DataFrame, vocabulary: dict) -> pd.DataFrame:
    # Week boundaries describe the storage/source clock, never an inferred US or Jakarta timezone.
    starts = f.transaction_date.dt.tz_localize(None).dt.to_period("W-SUN").dt.start_time.dt.tz_localize("UTC")
    work = f.copy()
    work["_week"] = starts
    work["_credit"] = np.where(f.direction.eq("CREDIT"), f.amount, 0.0)
    work["_small"] = f.amount.lt(vocabulary["small_amount_threshold"]).astype(float)
    work["_day_only"] = f.timestamp_precision.eq("day").astype(float)
    keys = ["account_number", "_week"]
    aggregate = work.groupby(keys, sort=True).agg(
        TXN_COUNT_W=("amount", "size"), TXN_VALUE_W=("amount", "sum"), DEBIT_VALUE_W=("_debit", "sum"),
        CREDIT_VALUE_W=("_credit", "sum"), AMOUNT_MEAN_W=("amount", "mean"), AMOUNT_MAX_W=("amount", "max"),
        SMALL_AMOUNT_SHARE_W=("_small", "mean"), LOGIN_ATTEMPTS_MEAN_W=("login_attempts", "mean"),
        LOGIN_ATTEMPTS_MAX_W=("login_attempts", "max"), DURATION_MEAN_W=("transaction_duration", "mean"),
        DURATION_MAX_W=("transaction_duration", "max"), BALANCE_MEAN_W=("account_balance", "mean"),
        DAY_ONLY_SHARE_W=("_day_only", "mean"),
    ).reset_index()
    query = aggregate[["account_number"]].copy()
    query["_cut"] = aggregate._week.astype("int64")
    history = _history(f, query, vocabulary)
    out = pd.DataFrame({"account_number": aggregate.account_number, "transaction_ref": None,
                        "as_of": aggregate._week + pd.Timedelta(days=7) - pd.Timedelta(seconds=1)})
    for name in _COMMON:
        out[name] = history[name]
    for name in aggregate:
        if name not in keys:
            out[name] = aggregate[name]
    out["NET_INFLOW_W"] = out.CREDIT_VALUE_W - out.DEBIT_VALUE_W
    out["DEBIT_SHARE_W"] = _divide(out.DEBIT_VALUE_W, out.TXN_VALUE_W)
    gate = history.NO_BASELINE.eq(0)
    out["COUNT_OVER_PRIOR_WEEKLY_MEAN"] = pd.Series(_divide(out.TXN_COUNT_W, history.PRIOR_COUNT_R30D * 7 / 30)).where(gate)
    out["VALUE_OVER_PRIOR_WEEKLY_MEAN"] = pd.Series(_divide(out.TXN_VALUE_W, history.PRIOR_VALUE_R30D * 7 / 30)).where(gate)
    for col, plural, prefix in (("device_ref", "DEVICES", "DEVICE"), ("ip_address", "IPS", "IP"), ("location", "LOCATIONS", "LOCATION")):
        valid = work[col].ne("")
        count = work.loc[valid].groupby(keys)[col].nunique()
        index = pd.MultiIndex.from_frame(aggregate[keys])
        out[f"DISTINCT_{plural}_W"] = count.reindex(index).to_numpy(dtype=float)
        novelty = (_first_seen(work, col) >= work._week.astype("int64")).astype(float).where(valid)
        work["_novel"] = novelty
        share = work.groupby(keys)._novel.mean()
        out[f"{prefix}_NEW_SHARE_W"] = pd.Series(share.reindex(index).to_numpy(dtype=float)).where(gate)
    return out


def build_features(frame: pd.DataFrame, unit: Literal["AML", "FRAUD"] | str, vocabulary: dict) -> pd.DataFrame:
    unit = unit.upper()
    names = feature_names(unit)
    if vocabulary.get("feature_set_version") != FEATURE_SET_VERSION:
        raise FeatureInputError("Vocabulary belongs to another feature set")
    f = _prepared(frame)
    columns = ["account_number", "transaction_ref", "as_of", *names]
    if f.empty:
        return pd.DataFrame(columns=columns)
    if f.currency.iloc[0] != vocabulary.get("currency"):
        raise FeatureInputError("Observation currency differs from the trained vocabulary; no FX conversion is available")
    out = _aml(f, vocabulary) if unit == "AML" else _fraud(f, vocabulary)
    out[names] = out[names].astype("float64").replace([np.inf, -np.inf], np.nan)
    return out[columns].sort_values(["account_number", "as_of", "transaction_ref"], kind="stable").reset_index(drop=True)
