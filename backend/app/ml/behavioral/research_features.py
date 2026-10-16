"""Controlled feature representations for research; no model or label fitting.

``full`` delegates the frozen behavioral_v1 feature builder unchanged.
``portable`` uses observed-event references instead of requiring thirty events
inside ninety days. A reference includes at least thirty strictly available
prior observations, expanding to include every availability tie at its oldest
boundary. ``LAST30`` in feature identifiers denotes this minimum-count boundary
rule, not an arbitrary selection of exactly thirty tied observations. Precision,
timezone, history length and availability flags remain data-quality metadata
rather than risk inputs. Rates use the included observations' actual calendar
span; sparse histories are never compressed or given fabricated times.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import features as original

REPRESENTATION_VERSION = "behavioral_research_views_v2_boundary_ties"
REFERENCE_OBSERVATIONS = 30
_META = ["account_number", "transaction_ref", "as_of"]

_FRAUD = {
    "LOG_AMOUNT": "log1p of the observed amount, in unchanged source currency.",
    "IS_DEBIT": "1 for an observed debit; 0 for an observed credit.",
    "CHANNEL_TRAIN_FREQUENCY": "Observed channel frequency in the frozen training vocabulary.",
    "LOGIN_ATTEMPTS": "Observed login attempts; no unit or missing value is invented.",
    "TRANSACTION_DURATION": "Observed duration in its unspecified source unit.",
    "ACCOUNT_BALANCE": "Observed balance snapshot, not a reconstructed running balance.",
    "AMOUNT_OVER_LAST30_PRIOR_MEAN": "Current amount divided by mean amount of at least the last 30 strictly available prior observations, including every oldest-boundary availability tie.",
    "AMOUNT_Z_LAST30_PRIOR": "Current amount minus their mean, divided by their population standard deviation.",
    "BALANCE_OVER_LAST30_PRIOR_MEAN": "Current balance divided by their mean recorded balance, ignoring missing snapshots.",
    "GAP_OVER_LAST30_PRIOR_MEAN_GAP": "Elapsed time since the latest strictly prior availability bound divided by the reference's mean calendar-day gap (n minus one intervals over actual observed day span). A date-only observation becomes available at its day end; this is not a recovered physical inactivity gap.",
    "COUNT_R7D_OVER_LAST30_PRIOR_RATE": "Strictly prior seven-day count divided by seven times their observed-event day rate.",
    "VALUE_R7D_OVER_LAST30_PRIOR_RATE": "Strictly prior seven-day amount divided by seven times their observed-event day rate and mean amount.",
    "SMALL_PAYMENT_RELATIVE_ACCELERATION": "If current amount is below the frozen train p25, prior seven-day small count plus this observation, divided by the expected seven-day small count from the prior minimum-30 reference with all oldest-boundary availability ties; otherwise zero when a reference exists.",
    "DEVICE_FIRST_USE": "Observed device absent from strictly prior available history; unknown without supported history or an observed device.",
    "IP_FIRST_USE": "Observed IP absent from strictly prior available history; no location is inferred.",
    "LOCATION_FIRST_USE": "Observed location string absent from strictly prior available history; no distance/country is inferred.",
    "MERCHANT_FIRST_USE": "Observed merchant identifier absent from strictly prior available history; no ownership/network is inferred.",
    "NEW_CONTEXT_EXCESS_LOGIN": "Any observed device/IP/location novelty multiplied by max(login attempts minus one, zero); unknown if context or login is unavailable.",
}
_AML = {
    "COUNT_W_OVER_LAST30_PRIOR_RATE": "This observed account-week count divided by seven times the observed-event day rate of at least the last 30 observations strictly available before the week, including all oldest-boundary availability ties.",
    "VALUE_W_OVER_LAST30_PRIOR_RATE": "This observed account-week amount divided by seven times their day rate and mean amount.",
    "TICKET_W_OVER_LAST30_PRIOR_MEAN": "This week's mean ticket divided by their mean amount.",
    "DEBIT_SHARE_W": "Observed debit share of this week's value; no counterparty linkage is inferred.",
    "SMALL_AMOUNT_SHARE_W": "Share of this week's amounts below the frozen training p25; not a legal threshold.",
    "SMALL_PAYMENT_RELATIVE_ACCELERATION_W": "This week's small-amount count divided by its expected count from the prior minimum-30 reference's small share and observed-event day rate, including all oldest-boundary availability ties.",
    "LOGIN_ATTEMPTS_MEAN_W": "Mean recorded login attempts in the observed week.",
    "LOGIN_ATTEMPTS_MAX_W": "Largest recorded login-attempt count in the observed week.",
    "DURATION_MEAN_W": "Mean source-recorded duration in the observed week, in its unspecified source unit.",
    "DURATION_MAX_W": "Largest source-recorded duration in the observed week.",
    "BALANCE_W_OVER_LAST30_PRIOR_MEAN": "This week's mean recorded balance divided by the prior minimum-30 reference's mean recorded balance, including all oldest-boundary availability ties.",
    "DEVICE_NEW_SHARE_W": "Share of observed devices absent before this week, with a supported prior history.",
    "IP_NEW_SHARE_W": "Share of observed IPs absent before this week; no country is inferred.",
    "LOCATION_NEW_SHARE_W": "Share of observed location strings absent before this week.",
    "NEW_CONTEXT_EXCESS_LOGIN_W": "Mean within-week observation-level context novelty times excess login attempts, with the novelty reference fixed before the week.",
}


def feature_definitions(unit: str, representation: str = "full") -> dict[str, str]:
    unit = str(unit).upper()
    original.feature_names(unit)  # Preserve the original invalid-unit exception.
    if representation == "full":
        return {name: original.FEATURE_DEFINITIONS[(unit, name)] for name in original.feature_names(unit)}
    if representation != "portable":
        raise ValueError("representation must be 'full' or 'portable'")
    definitions = _AML if unit == "AML" else _FRAUD
    gate = (" Requires at least 30 strictly available prior observations over at least 30 observed days."
            " The reference includes every availability tie at its oldest included boundary, so n can exceed 30;"
            " means and rates use that actual n. This is an event-count reference, not a 90-day requirement.")
    return {name: definition + (gate if "LAST30" in name or "ACCELERATION" in name else "")
            for name, definition in definitions.items()}


def feature_names(unit: str, representation: str = "full") -> list[str]:
    return list(feature_definitions(unit, representation))


def _divide(numerator, denominator) -> np.ndarray:
    numerator, denominator = np.asarray(numerator, dtype=float), np.asarray(denominator, dtype=float)
    return np.divide(numerator, denominator, out=np.full(np.broadcast(numerator, denominator).shape, np.nan),
                     where=np.isfinite(numerator) & np.isfinite(denominator) & (denominator != 0))


def _references(frame: pd.DataFrame, queries: pd.DataFrame, vocabulary: dict) -> pd.DataFrame:
    names = ["mean", "sd", "balance", "rate", "small_share", "gap", "count7", "value7", "small7"]
    result = pd.DataFrame(np.nan, index=queries.index, columns=names)
    groups = {account: group.sort_values(["_available", "transaction_ref"], kind="stable")
              for account, group in frame.groupby("account_number", sort=False)}
    for account, query in queries.groupby("account_number", sort=False):
        h = groups[account]
        available = h._available.to_numpy(dtype=np.int64)
        observed = h._time.to_numpy(dtype=np.int64)
        days = h._day.to_numpy(dtype=np.int64)
        cut = query._cut.to_numpy(dtype=np.int64)
        hi = np.searchsorted(available, cut, side="left")
        minimum_lo = np.maximum(0, hi - REFERENCE_OBSERVATIONS)
        # A date-only/availability tie has no defensible internal ordering.
        # Include its entire boundary group rather than selecting by ID order.
        lo = np.searchsorted(available, available[minimum_lo], side="left")
        amount = h.amount.to_numpy(dtype=float)
        small = (amount < vocabulary["small_amount_threshold"]).astype(float)
        balance = h.account_balance.to_numpy(dtype=float)
        values = {"amount": amount, "square": amount * amount, "small": small,
                  "balance": np.nan_to_num(balance, nan=0), "balance_count": np.isfinite(balance).astype(float)}
        cumulative = {name: np.r_[0., np.cumsum(value)] for name, value in values.items()}

        def total(name, lower):
            return cumulative[name][hi] - cumulative[name][lower]

        last = np.maximum(0, hi - 1)
        earliest_prior = np.minimum.accumulate(observed)[last]
        observed_history_days = (cut - earliest_prior) / original.DAY_NS
        gate = (hi >= REFERENCE_OBSERVATIONS) & (observed_history_days >= original.MIN_HISTORY_DAYS)
        n = hi - lo
        mean = _divide(total("amount", lo), n)
        sd = np.sqrt(np.maximum(0, _divide(total("square", lo), n) - mean * mean))
        # Calendar days are common to day-only and exact observations. No
        # fabricated midnight order or source UTC-hour is used in this rate.
        span_days = (days[last] - days[lo]) / original.DAY_NS
        rate = _divide(n - 1, span_days)
        values_out = {
            "mean": mean, "sd": sd,
            "balance": _divide(total("balance", lo), total("balance_count", lo)),
            "rate": rate, "small_share": _divide(total("small", lo), n),
            # The availability bound is commutative within mixed-precision
            # ties. Selecting observed[last] would pick an arbitrary carrier
            # time according to transaction_ref and could change under renaming.
            "gap": np.where(hi > 0, (cut - available[last]) / original.DAY_NS, np.nan),
        }
        lower7 = np.searchsorted(available, cut - 7 * original.DAY_NS, side="left")
        values_out.update({"count7": hi - lower7, "value7": total("amount", lower7),
                           "small7": total("small", lower7)})
        for name, value in values_out.items():
            result.loc[query.index, name] = np.where(gate, value, np.nan)
    return result


def _new_context_login(frame: pd.DataFrame, cut: pd.Series, supported: pd.Series) -> pd.Series:
    novelty = []
    for column in ("device_ref", "ip_address", "location"):
        first = frame.groupby(["account_number", column], sort=False)._available.transform("min")
        novelty.append((first >= cut).astype(float).where(frame[column].ne("")))
    any_context = pd.concat(novelty, axis=1).max(axis=1, skipna=True)
    return (any_context * (frame.login_attempts - 1).clip(lower=0)).where(supported)


def build_experiment_features(observations: pd.DataFrame, unit: str, vocabulary: dict,
                              representation: str = "full") -> pd.DataFrame:
    """Build a label-free view; no source fitting or artifact mutation occurs."""
    names = feature_names(unit, representation)
    base = original.build_features(observations, unit, vocabulary)
    if representation == "full" or base.empty:
        return base if representation == "full" else pd.DataFrame(columns=[*_META, *names])
    unit = unit.upper()
    frame = original._prepared(observations)
    if unit == "FRAUD":
        # Work in original row order, then restore the public builder ordering.
        base = base.set_index("transaction_ref").loc[frame.transaction_ref].reset_index()
        query = frame[["account_number"]].copy()
        query["_cut"] = np.where(frame.timestamp_precision.eq("day"), frame._day, frame._time)
    else:
        query = base[["account_number"]].copy()
        query["_cut"] = (base.as_of - pd.Timedelta(days=7) + pd.Timedelta(seconds=1)).astype("int64")
    reference = _references(frame, query, vocabulary)
    out = base[_META].copy()
    if unit == "FRAUD":
        for name in ("LOG_AMOUNT", "IS_DEBIT", "CHANNEL_TRAIN_FREQUENCY", "LOGIN_ATTEMPTS",
                     "TRANSACTION_DURATION", "ACCOUNT_BALANCE", "DEVICE_FIRST_USE", "IP_FIRST_USE",
                     "LOCATION_FIRST_USE", "MERCHANT_FIRST_USE"):
            out[name] = base[name]
        out["AMOUNT_OVER_LAST30_PRIOR_MEAN"] = _divide(frame.amount, reference["mean"])
        out["AMOUNT_Z_LAST30_PRIOR"] = _divide(frame.amount - reference["mean"], reference.sd)
        out["BALANCE_OVER_LAST30_PRIOR_MEAN"] = _divide(frame.account_balance, reference.balance)
        out["GAP_OVER_LAST30_PRIOR_MEAN_GAP"] = reference.gap * reference.rate
        out["COUNT_R7D_OVER_LAST30_PRIOR_RATE"] = _divide(reference.count7, reference.rate * 7)
        out["VALUE_R7D_OVER_LAST30_PRIOR_RATE"] = _divide(reference.value7, reference.rate * reference["mean"] * 7)
        is_small = frame.amount.lt(vocabulary["small_amount_threshold"])
        acceleration = _divide(reference.small7 + 1, reference.rate * reference.small_share * 7)
        out["SMALL_PAYMENT_RELATIVE_ACCELERATION"] = np.where(is_small, acceleration,
                                                                              np.where(reference["mean"].notna(), 0, np.nan))
        out["NEW_CONTEXT_EXCESS_LOGIN"] = _new_context_login(frame, query._cut, reference["mean"].notna())
    else:
        for name in ("DEBIT_SHARE_W", "SMALL_AMOUNT_SHARE_W", "LOGIN_ATTEMPTS_MEAN_W", "LOGIN_ATTEMPTS_MAX_W",
                     "DURATION_MEAN_W", "DURATION_MAX_W", "DEVICE_NEW_SHARE_W", "IP_NEW_SHARE_W", "LOCATION_NEW_SHARE_W"):
            out[name] = base[name]
        out["COUNT_W_OVER_LAST30_PRIOR_RATE"] = _divide(base.TXN_COUNT_W, reference.rate * 7)
        out["VALUE_W_OVER_LAST30_PRIOR_RATE"] = _divide(base.TXN_VALUE_W, reference.rate * reference["mean"] * 7)
        out["TICKET_W_OVER_LAST30_PRIOR_MEAN"] = _divide(base.AMOUNT_MEAN_W, reference["mean"])
        out["SMALL_PAYMENT_RELATIVE_ACCELERATION_W"] = _divide(base.TXN_COUNT_W * base.SMALL_AMOUNT_SHARE_W,
                                                                  reference.rate * reference.small_share * 7)
        out["BALANCE_W_OVER_LAST30_PRIOR_MEAN"] = _divide(base.BALANCE_MEAN_W, reference.balance)
        frame["_week"] = frame.transaction_date.dt.floor("D") - pd.to_timedelta(frame.transaction_date.dt.dayofweek, unit="D")
        # Context novelty is fixed at the week boundary for every observation,
        # then joined with that observation's login count before aggregation.
        frame["_interaction"] = _new_context_login(frame, frame._week.astype("int64"), pd.Series(True, index=frame.index))
        weekly = frame.groupby(["account_number", "_week"], sort=True)._interaction.mean()
        index = pd.MultiIndex.from_arrays([base.account_number, pd.to_datetime(query._cut, utc=True)])
        out["NEW_CONTEXT_EXCESS_LOGIN_W"] = np.where(reference["mean"].notna(), weekly.reindex(index).to_numpy(), np.nan)
    out[names] = out[names].astype("float64").replace([np.inf, -np.inf], np.nan)
    return out[[*_META, *names]].sort_values(["account_number", "as_of", "transaction_ref"], kind="stable").reset_index(drop=True)
