"""Feature computation for fs_v2 (see feature_set.py for every definition).

One function per unit, each callable for a single entity, because live
monitoring will call exactly these (PROJECT_CONTEXT §13):

    history = EntityHistory.build(entity, transactions, dataset_facts, context)
    aml_features(history, as_of, context)     # AML: one (entity, week)
    fraud_features(history, i, context)       # Fraud: the i-th transaction

Training calls the same functions in a loop (build_aml_matrix,
build_fraud_matrix). Everything is point-in-time: a feature at `as_of` reads
only transactions at or before it, so what the model learns from is exactly
what it would have seen at that moment.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import numpy as np

from app.ml.feature_set import (
    AML_FEATURES,
    FRAUD_FEATURES,
    HOUR,
    JAKARTA,
    SECONDS_PER_DAY,
    P,
    geo_list,
)
from app.ml.loader import Account, Dataset, Merchant, Txn
from app.ml.reference import load as load_mcc_reference
from app.scripts.raw_contract import TRANSACTIONS

DAY = SECONDS_PER_DAY
WEEK = 7 * DAY
NAN = float("nan")
_OFFSET = 7 * HOUR  # Asia/Jakarta has no daylight saving (FRD §8, AS-09)

_ALLOWED = {f.name: f.allowed for f in TRANSACTIONS.fields}
CHANNELS = {value: i for i, value in enumerate(_ALLOWED["channel"])}
TXN_TYPES = {value: i for i, value in enumerate(_ALLOWED["transaction_type"])}


class FeatureContext(Protocol):
    """What a feature needs to know beyond one entity's own rows. In memory for
    training; backed by the database for live scoring (stage 4)."""

    def device_entities(self, device: str, start: float, end: float) -> int: ...
    def device_accounts(self, device: str, start: float, end: float) -> int: ...
    def device_flags(self, device: str) -> tuple[bool, bool]: ...
    def listed_countries(self) -> frozenset[str]: ...
    def mcc_ticket(self, mcc: str) -> float: ...


class InMemoryContext:
    """Device usage over all accepted rows, as sorted timelines. The MCC ticket
    reference is the stored artefact, never recomputed from `dataset`: test
    and live data are scored against the training data's yardstick."""

    def __init__(self, dataset: Dataset, mcc_reference: dict[str, float] | None = None) -> None:
        by_device: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
        for txn in dataset.transactions:
            if txn.device:
                by_device[txn.device].append((txn.ts, txn.entity, txn.account))
        self._timelines = {}
        for device, rows in by_device.items():
            rows.sort()
            self._timelines[device] = ([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows])
        self._flags = dataset.devices
        self._listed = geo_list()
        self._mcc = load_mcc_reference() if mcc_reference is None else mcc_reference

    def _window(self, device: str, start: float, end: float):
        timeline = self._timelines.get(device)
        if timeline is None:
            return None
        times = timeline[0]
        return timeline, bisect_right(times, start), bisect_right(times, end)

    def device_entities(self, device: str, start: float, end: float) -> int:
        found = self._window(device, start, end)
        return len(set(found[0][1][found[1]:found[2]])) if found else 0

    def device_accounts(self, device: str, start: float, end: float) -> int:
        found = self._window(device, start, end)
        return len(set(found[0][2][found[1]:found[2]])) if found else 0

    def device_flags(self, device: str) -> tuple[bool, bool]:
        return self._flags.get(device, (False, False))

    def listed_countries(self) -> frozenset[str]:
        return self._listed

    def mcc_ticket(self, mcc: str) -> float:
        return self._mcc.get(mcc, NAN)


@dataclass
class EntityHistory:
    """One entity's accepted transactions in time order, as arrays, with the
    point-in-time facts each row carries (was this the first time this
    counterparty / device / merchant appeared?)."""

    entity: str
    is_business: bool
    opened_ts: float
    refs: list[str]
    t: np.ndarray
    amount: np.ndarray
    out: np.ndarray
    channel: np.ndarray
    txn_type: np.ndarray
    counterparty: np.ndarray       # per-entity code, -1 = missing
    device: list[str]
    account: np.ndarray            # per-entity code
    merchant: np.ndarray           # per-entity code, -1 = none
    listed: np.ndarray
    reversed: np.ndarray
    hour: np.ndarray
    local_day: np.ndarray
    cp_first: np.ndarray
    device_first: np.ndarray
    merchant_first: np.ndarray
    device_first_ts: np.ndarray    # when this entity first used the row's device (NaN: no device)
    gap_days: np.ndarray           # days since the entity's previous row (NaN for the first)
    account_gap_days: np.ndarray   # days since the account's previous row
    own_merchant_payment: np.ndarray
    category_ticket: np.ndarray    # reference ticket of the MCC of the row's own merchant (AS-04)
    off_hours: np.ndarray
    cum_amount: np.ndarray
    cum_out: np.ndarray
    cum_in: np.ndarray

    @classmethod
    def build(cls, entity: str, transactions: list[Txn], entity_accounts: list[Account],
              merchants: dict[str, Merchant], context: FeatureContext) -> "EntityHistory":
        """`entity_accounts` is master data (every account the entity owns), not
        whatever accounts its transactions happen to touch, so the account age
        does not depend on how much history was loaded."""
        rows = sorted(transactions, key=lambda x: (x.ts, x.ref))
        n = len(rows)
        listed = context.listed_countries()
        is_business = bool(entity_accounts) and entity_accounts[0].is_business
        opened = [a.opened_ts for a in entity_accounts if not math.isnan(a.opened_ts)]

        cp_codes: dict[str, int] = {}
        account_codes: dict[str, int] = {}
        merchant_codes: dict[str, int] = {}
        cp_first = np.zeros(n, dtype=bool)
        device_first = np.zeros(n, dtype=bool)
        merchant_first = np.zeros(n, dtype=bool)
        device_first_ts = np.full(n, NAN)
        account_gap = np.full(n, NAN)
        seen_devices: dict[str, float] = {}
        last_on_account: dict[str, float] = {}
        counterparty = np.full(n, -1, dtype=np.int32)
        account = np.zeros(n, dtype=np.int32)
        merchant = np.full(n, -1, dtype=np.int32)
        own_payment = np.zeros(n, dtype=bool)
        category_ticket = np.full(n, NAN)
        off_hours = np.zeros(n, dtype=bool)

        for i, r in enumerate(rows):
            if r.counterparty:
                if r.counterparty not in cp_codes:
                    cp_codes[r.counterparty] = len(cp_codes)
                    cp_first[i] = True
                counterparty[i] = cp_codes[r.counterparty]
            if r.device:
                if r.device not in seen_devices:
                    seen_devices[r.device] = r.ts
                    device_first[i] = True
                device_first_ts[i] = seen_devices[r.device]
            if r.merchant:
                if r.merchant not in merchant_codes:
                    merchant_codes[r.merchant] = len(merchant_codes)
                    merchant_first[i] = True
                merchant[i] = merchant_codes[r.merchant]
                info = merchants.get(r.merchant)
                if info is not None and info.business == entity and not r.out:
                    own_payment[i] = True
                    category_ticket[i] = context.mcc_ticket(info.mcc)
                    if info.hours is not None:
                        minute = int(((r.ts + _OFFSET) % DAY) // 60)
                        start, end = info.hours
                        inside = start <= minute <= end if start <= end else (minute >= start or minute <= end)
                        off_hours[i] = not inside
            account[i] = account_codes.setdefault(r.account, len(account_codes))
            if r.account in last_on_account:
                account_gap[i] = (r.ts - last_on_account[r.account]) / DAY
            last_on_account[r.account] = r.ts

        t = np.array([r.ts for r in rows], dtype=float)
        amount = np.array([r.amount for r in rows], dtype=float)
        out = np.array([r.out for r in rows], dtype=bool)
        gap = np.full(n, NAN)
        if n > 1:
            gap[1:] = np.diff(t) / DAY
        local = t + _OFFSET
        return cls(
            entity=entity, is_business=is_business, opened_ts=min(opened) if opened else NAN,
            refs=[r.ref for r in rows], t=t, amount=amount, out=out,
            channel=np.array([CHANNELS.get(r.channel, -1) for r in rows], dtype=np.int16),
            txn_type=np.array([TXN_TYPES.get(r.txn_type, -1) for r in rows], dtype=np.int16),
            counterparty=counterparty, device=[r.device for r in rows], account=account, merchant=merchant,
            listed=np.array([r.counterparty_country in listed for r in rows], dtype=bool),
            reversed=np.array([r.reversed for r in rows], dtype=bool),
            hour=((local % DAY) // HOUR).astype(np.int8) if n else np.zeros(0, dtype=np.int8),
            local_day=(local // DAY).astype(np.int64) if n else np.zeros(0, dtype=np.int64),
            cp_first=cp_first, device_first=device_first, merchant_first=merchant_first,
            device_first_ts=device_first_ts, gap_days=gap, account_gap_days=account_gap,
            own_merchant_payment=own_payment, category_ticket=category_ticket, off_hours=off_hours,
            cum_amount=np.concatenate([[0.0], np.cumsum(amount)]),
            cum_out=np.concatenate([[0.0], np.cumsum(np.where(out, amount, 0.0))]),
            cum_in=np.concatenate([[0.0], np.cumsum(np.where(out, 0.0, amount))]),
        )

    def index_at(self, moment: float) -> int:
        """Number of rows at or before `moment`."""
        return int(np.searchsorted(self.t, moment, side="right"))


# --- small helpers ------------------------------------------------------------------------


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else NAN


def _p95(values: np.ndarray) -> float:
    return float(np.percentile(values, 95)) if len(values) else NAN


def _jsd(a: np.ndarray, b: np.ndarray, bins: int) -> float:
    """Jensen-Shannon divergence (base 2) between two categorical samples."""
    if not len(a) or not len(b):
        return NAN
    p = np.bincount(a, minlength=bins).astype(float)
    q = np.bincount(b, minlength=bins).astype(float)
    p, q = p / p.sum(), q / q.sum()
    m = (p + q) / 2

    def kl(x, y):
        mask = x > 0
        return float(np.sum(x[mask] * np.log2(x[mask] / y[mask])))

    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def _gate(value: float, open_: bool) -> float:
    return value if open_ else NAN


# --- AML: one (entity, calendar week) -------------------------------------------------------


def week_end(moment: float) -> float:
    """The Sunday 23:59:59 (Asia/Jakarta) closing the calendar week of `moment`."""
    local = datetime.fromtimestamp(moment, JAKARTA)
    sunday = local.date() + timedelta(days=6 - local.weekday())
    return datetime(sunday.year, sunday.month, sunday.day, 23, 59, 59, tzinfo=JAKARTA).timestamp()


def aml_features(h: EntityHistory, as_of: float, context: FeatureContext) -> dict[str, float]:
    t = h.t
    idx = lambda moment: int(np.searchsorted(t, moment, side="right"))  # noqa: E731
    hi = idx(as_of)
    week_start = as_of - WEEK
    lo7, lo30, lo90 = idx(week_start), idx(as_of - 30 * DAY), idx(as_of - 90 * DAY)
    lo_prior90 = idx(week_start - P["baseline_days"] * DAY)
    first = t[0] if len(t) else as_of
    history_before_week = max(0.0, (week_start - first) / DAY)

    week = slice(lo7, hi)
    amounts7 = h.amount[week]
    prior = h.amount[lo_prior90:lo7]
    baseline_txns = lo7 - lo_prior90
    f: dict[str, float] = {}

    f["TXN_COUNT_R7D"] = hi - lo7
    f["TXN_COUNT_R30D"] = hi - lo30
    f["TXN_COUNT_R90D"] = hi - lo90
    f["TXN_VALUE_SUM_R7D"] = h.cum_amount[hi] - h.cum_amount[lo7]
    f["TXN_VALUE_SUM_R30D"] = h.cum_amount[hi] - h.cum_amount[lo30]
    f["TXN_VALUE_SUM_R90D"] = h.cum_amount[hi] - h.cum_amount[lo90]
    f["INBOUND_SUM_R7D"] = h.cum_in[hi] - h.cum_in[lo7]
    f["OUTBOUND_SUM_R7D"] = h.cum_out[hi] - h.cum_out[lo7]
    f["INBOUND_SUM_R30D"] = h.cum_in[hi] - h.cum_in[lo30]
    f["OUTBOUND_SUM_R30D"] = h.cum_out[hi] - h.cum_out[lo30]
    f["TXN_VALUE_MAX_R7D"] = float(amounts7.max()) if len(amounts7) else NAN
    f["MAX_R7D_OVER_OWN_P95"] = _gate(_ratio(f["TXN_VALUE_MAX_R7D"], _p95(prior)),
                                      baseline_txns >= P["relative_min_history_txns"])

    # P02
    lower = P["reporting_threshold_idr"] * P["band_lower_ratio"]
    in_band = (amounts7 >= lower) & (amounts7 < P["reporting_threshold_idr"])
    f["P02_BAND_COUNT_R7D"] = int(in_band.sum())
    f["P02_BAND_SUM_R7D"] = float(amounts7[in_band].sum())
    # The same cluster seen through rolling windows ending at this week's rows,
    # which may reach back into the previous week.
    band_all = (h.amount >= lower) & (h.amount < P["reporting_threshold_idr"])
    best_count, best_sum = 0, 0.0
    for j in np.nonzero(band_all[lo7:hi])[0] + lo7:
        start = idx(t[j] - WEEK)
        members = band_all[start:j + 1]
        if members.sum() > best_count or (members.sum() == best_count and h.amount[start:j + 1][members].sum() > best_sum):
            best_count, best_sum = int(members.sum()), float(h.amount[start:j + 1][members].sum())
    f["P02_BAND_COUNT_ROLLING_7D"] = best_count
    f["P02_BAND_SUM_ROLLING_7D"] = best_sum
    f["DISTINCT_ACCOUNTS_R7D"] = len(set(h.account[week].tolist()))

    # P03: credits sent out again within passthrough_hours (only what is visible by as_of)
    best, credits = 0.0, 0
    for j in range(lo7, hi):
        if h.out[j] or h.amount[j] < P["passthrough_min_inbound_idr"]:
            continue
        credits += 1
        end = idx(min(t[j] + P["passthrough_hours"] * HOUR, as_of))
        best = max(best, (h.cum_out[end] - h.cum_out[j + 1]) / h.amount[j])
    f["PASSTHROUGH_RATIO_MAX_R7D"] = best if credits else NAN
    f["PASSTHROUGH_CREDITS_R7D"] = credits

    # P04
    f["MAX_DAILY_COUNT_R7D"] = int(np.bincount(h.local_day[week] - h.local_day[lo7]).max()) if hi > lo7 else 0
    f["MAX_COUNT_ROLLING_24H"] = max((j + 1 - idx(t[j] - 24 * HOUR) for j in range(lo7, hi)), default=0)
    weekly_mean_count = (lo7 - idx(week_start - P["baseline_weeks"] * WEEK)) / P["baseline_weeks"]
    f["COUNT_R7D_OVER_BASELINE"] = _gate((f["TXN_COUNT_R7D"] + 1) / (weekly_mean_count + 1),
                                         history_before_week >= P["velocity_min_baseline_days"])

    # P05
    f["LONGEST_GAP_DAYS_R7D"] = float(np.nanmax(h.gap_days[week])) if np.any(~np.isnan(h.gap_days[week])) else NAN
    account_gaps = h.account_gap_days[week]
    f["LONGEST_ACCOUNT_GAP_DAYS_R7D"] = float(np.nanmax(account_gaps)) if np.any(~np.isnan(account_gaps)) else NAN

    # P06
    amounts30 = h.amount[lo30:hi]
    eligible = amounts30 >= P["round_min_amount_idr"]
    round_ = eligible & (np.mod(amounts30, P["rounding_unit_idr"]) == 0)
    f["ROUND_COUNT_R30D"] = int(round_.sum())
    f["ROUND_SHARE_R30D"] = _ratio(float(round_.sum()), float(eligible.sum()))

    # Weekly baseline over the 12 weeks before this one (P07 and FR-401)
    edges = [week_start - k * WEEK for k in range(P["baseline_weeks"], -1, -1)]
    bounds = [idx(edge) for edge in edges]
    weekly_count = np.diff(bounds).astype(float)
    weekly_value = np.array([h.cum_amount[b] - h.cum_amount[a] for a, b in zip(bounds[:-1], bounds[1:])])
    weekly_cp = np.array([len(set(h.counterparty[a:b][h.counterparty[a:b] >= 0].tolist()))
                          for a, b in zip(bounds[:-1], bounds[1:])], dtype=float)
    mean_value = float(weekly_value.mean())
    spike_open = history_before_week >= P["spike_min_baseline_weeks"] * 7
    f["WEEK_VALUE_OVER_BASELINE"] = _gate(f["TXN_VALUE_SUM_R7D"] / max(mean_value, 1.0), spike_open)
    f["WEEK_VALUE_INCREASE"] = _gate(f["TXN_VALUE_SUM_R7D"] - mean_value, spike_open)

    # P08
    listed30 = h.listed[lo30:hi]
    f["GEO_EXPOSURE_COUNT_R30D"] = int(listed30.sum())
    f["GEO_EXPOSURE_SUM_R30D"] = float(amounts30[listed30].sum())
    f["GEO_EXPOSURE_SHARE_R30D"] = _ratio(f["GEO_EXPOSURE_SUM_R30D"], float(amounts30.sum()))

    # P09
    devices30 = {d for d in h.device[lo30:hi] if d}
    start30 = as_of - P["device_window_days"] * DAY
    f["DEVICE_ENTITY_COUNT_R30D"] = max((context.device_entities(d, start30, as_of) for d in devices30), default=0)
    f["DEVICE_ACCOUNT_COUNT_R30D"] = max((context.device_accounts(d, start30, as_of) for d in devices30), default=0)

    # P10 (merchants belong to businesses)
    own = h.own_merchant_payment[lo30:hi]
    if h.is_business and own.any():
        tickets = amounts30[own]
        f["AVG_TICKET_R30D"] = float(tickets.mean())
        reference = h.category_ticket[lo30:hi][own]
        f["TICKET_MULTIPLE_R30D"] = (_ratio(f["AVG_TICKET_R30D"], float(np.nanmean(reference)))
                                     if np.isfinite(reference).any() else NAN)
        f["OFF_HOURS_SHARE_R30D"] = float(h.off_hours[lo30:hi][own].mean())
    else:
        f["AVG_TICKET_R30D"] = f["TICKET_MULTIPLE_R30D"] = f["OFF_HOURS_SHARE_R30D"] = NAN
    inbound30 = (~h.out[lo30:hi]) & (h.counterparty[lo30:hi] >= 0)
    if h.is_business and inbound30.any():
        by_payer = Counter()
        for code, value in zip(h.counterparty[lo30:hi][inbound30].tolist(), amounts30[inbound30].tolist()):
            by_payer[code] += value
        total = sum(by_payer.values())
        f["PAYER_TOP5_SHARE_R30D"] = sum(v for _, v in by_payer.most_common(5)) / total if total else NAN
    else:
        f["PAYER_TOP5_SHARE_R30D"] = NAN
    f["REVERSAL_RATE_R30D"] = _ratio(float(h.reversed[lo30:hi].sum()), float(hi - lo30))

    # P11
    inbound7 = ~h.out[week]
    cp7 = h.counterparty[week]
    f["FUNNEL_IN_DEGREE_R7D"] = len(set(cp7[inbound7 & (cp7 >= 0)].tolist()))
    f["FUNNEL_OUT_DEGREE_R7D"] = len(set(cp7[(~inbound7) & (cp7 >= 0)].tolist()))
    best_senders = 0
    for j in range(lo7, hi):
        if h.out[j]:
            continue
        start = idx(t[j] - WEEK)
        senders = h.counterparty[start:j + 1][(~h.out[start:j + 1]) & (h.counterparty[start:j + 1] >= 0)]
        best_senders = max(best_senders, len(set(senders.tolist())))
    f["FUNNEL_IN_DEGREE_ROLLING_7D"] = best_senders
    in_total = f["INBOUND_SUM_R7D"]
    if in_total > 0:
        in_times, in_amounts = t[week][inbound7], amounts7[inbound7]
        window = P["funnel_compression_hours"] * HOUR
        f["TIME_COMPRESSION_R7D"] = max(
            float(in_amounts[(in_times >= start) & (in_times <= start + window)].sum()) for start in in_times
        ) / in_total
        f["INBOUND_AMOUNT_CV_R7D"] = (float(in_amounts.std() / in_amounts.mean()) if len(in_amounts) >= 2 else NAN)
    else:
        f["TIME_COMPRESSION_R7D"] = f["INBOUND_AMOUNT_CV_R7D"] = NAN

    # FR-401 baseline deviations
    anomaly_open = lo7 >= P["anomaly_min_history_txns"] and history_before_week >= P["anomaly_min_history_days"]
    count_sd, value_sd, cp_sd = weekly_count.std(), weekly_value.std(), weekly_cp.std()
    this_cp = len(set(cp7[cp7 >= 0].tolist()))
    f["Z_TXN_COUNT_W"] = _gate((f["TXN_COUNT_R7D"] - weekly_count.mean()) / max(count_sd, P["z_sd_floor_count"]),
                               anomaly_open)
    value_floor = max(P["z_sd_floor_value_fraction"] * mean_value, 1.0)
    f["Z_TXN_VALUE_W"] = _gate((f["TXN_VALUE_SUM_R7D"] - mean_value) / max(value_sd, value_floor), anomaly_open)
    f["Z_DISTINCT_COUNTERPARTIES_W"] = _gate((this_cp - weekly_cp.mean()) / max(cp_sd, P["z_sd_floor_count"]),
                                             anomaly_open)
    channels_week, channels_prior = h.channel[week], h.channel[lo_prior90:lo7]
    f["CHANNEL_MIX_JSD"] = _gate(_jsd(channels_week[channels_week >= 0], channels_prior[channels_prior >= 0],
                                      len(CHANNELS)), anomaly_open)
    f["HOUR_PROFILE_JSD"] = _gate(_jsd(h.hour[week] // 4, h.hour[lo_prior90:lo7] // 4, 6), anomaly_open)
    f["BASELINE_CV"] = _gate(_ratio(float(value_sd), mean_value), anomaly_open)

    # Novelty (never seen before), unknown until there is history to have seen it in
    novelty_open = history_before_week >= P["min_history_days"]
    known_cp = h.counterparty[week] >= 0
    f["NEW_COUNTERPARTY_SHARE_R7D"] = _gate(
        float(h.cp_first[week][known_cp].mean()) if known_cp.any() else NAN, novelty_open)
    f["NEW_DEVICE_R7D"] = _gate(float(h.device_first[week].any()), novelty_open)

    # Availability
    f["HISTORY_DAYS"] = (as_of - first) / DAY
    f["HISTORY_TXN_COUNT"] = hi
    f["BASELINE_DAYS_AVAILABLE"] = min(float(P["baseline_days"]), history_before_week)
    f["BASELINE_TXN_COUNT"] = baseline_txns
    f["ACCOUNT_AGE_DAYS"] = (as_of - h.opened_ts) / DAY
    f["ENTITY_IS_BUSINESS"] = float(h.is_business)
    return f


# --- FRAUD: one transaction ------------------------------------------------------------------


def fraud_features(h: EntityHistory, i: int, context: FeatureContext) -> dict[str, float]:
    """Features of the i-th row, as of that row: rows 0..i, nothing after."""
    t = h.t
    now = t[i]
    lo = lambda span: int(np.searchsorted(t, now - span, side="right"))  # noqa: E731
    lo1h, lo24h, lo90 = lo(HOUR), lo(24 * HOUR), lo(P["baseline_days"] * DAY)
    first = t[0]
    history_days = (now - first) / DAY
    prior = h.amount[lo90:i]
    f: dict[str, float] = {}

    amount = h.amount[i]
    f["AMOUNT"] = amount
    f["LOG_AMOUNT"] = math.log10(amount) if amount > 0 else NAN
    f["IS_OUTBOUND"] = float(h.out[i])
    f["CHANNEL_CODE"] = float(h.channel[i])
    f["TXN_TYPE_CODE"] = float(h.txn_type[i])
    f["LOCAL_HOUR"] = float(h.hour[i])
    relative_open = len(prior) >= P["relative_min_history_txns"]
    p95 = _p95(prior)
    f["AMOUNT_OVER_OWN_P95"] = _gate(_ratio(amount, p95), relative_open)
    f["AMOUNT_OVER_OWN_MEDIAN"] = _gate(_ratio(amount, float(np.median(prior)) if len(prior) else NAN),
                                        relative_open)
    f["DAYS_SINCE_PREV_TXN"] = h.gap_days[i]
    f["DAYS_SINCE_PREV_TXN_ACCOUNT"] = h.account_gap_days[i]
    f["COUNT_R1H"] = i + 1 - lo1h
    f["COUNT_R24H"] = i + 1 - lo24h
    f["OUT_SUM_R1H"] = h.cum_out[i + 1] - h.cum_out[lo1h]
    f["OUT_SUM_R24H"] = h.cum_out[i + 1] - h.cum_out[lo24h]
    daily_mean = (i - lo90) / P["baseline_days"]
    f["COUNT_R24H_OVER_DAILY_BASELINE"] = _gate((f["COUNT_R24H"] + 1) / (daily_mean + 1),
                                                history_days >= P["velocity_min_baseline_days"])
    f["OUT_SUM_R24H_OVER_OWN_P95"] = _gate(_ratio(f["OUT_SUM_R24H"], p95), relative_open)
    # Count-based: the previous N rows however old, so a dormancy does not
    # empty the baseline the way a 90-day window does.
    last_n = h.amount[max(0, i - P["count_baseline_txns"]):i]
    p95_n = _p95(last_n)
    count_open = len(last_n) >= P["relative_min_history_txns"]
    f["AMOUNT_OVER_P95_LAST_N"] = _gate(_ratio(amount, p95_n), count_open)
    f["OUT_SUM_R24H_OVER_P95_LAST_N"] = _gate(_ratio(f["OUT_SUM_R24H"], p95_n), count_open)

    device = h.device[i]
    novelty_open = history_days >= P["min_history_days"]
    f["DEVICE_PRESENT"] = float(bool(device))
    f["DEVICE_FIRST_USE"] = _gate(float(h.device_first[i]) if device else NAN, novelty_open)
    f["DEVICE_TENURE_DAYS"] = _gate((now - h.device_first_ts[i]) / DAY if device else NAN, novelty_open)
    f["COUNTERPARTY_FIRST_USE"] = _gate(float(h.cp_first[i]) if h.counterparty[i] >= 0 else NAN, novelty_open)
    f["MERCHANT_FIRST_USE"] = _gate(float(h.merchant_first[i]) if h.merchant[i] >= 0 else NAN, novelty_open)
    f["NEW_COUNTERPARTIES_R24H"] = _gate(float(h.cp_first[lo24h:i + 1].sum()), novelty_open)
    f["DISTINCT_DEVICES_R24H"] = float(len({d for d in h.device[lo24h:i + 1] if d}))
    f["DEVICE_ENTITY_COUNT_R30D"] = float(
        context.device_entities(device, now - P["device_window_days"] * DAY, now)) if device else NAN
    emulator, rooted = context.device_flags(device) if device else (False, False)
    f["DEVICE_IS_EMULATOR"] = float(emulator) if device else NAN
    f["DEVICE_IS_ROOTED"] = float(rooted) if device else NAN
    f["COUNTERPARTY_COUNTRY_LISTED"] = float(h.listed[i])

    f["HISTORY_DAYS"] = history_days
    f["PRIOR_TXN_COUNT_90D"] = float(i - lo90)
    f["ACCOUNT_AGE_DAYS"] = (now - h.opened_ts) / DAY
    f["ENTITY_IS_BUSINESS"] = float(h.is_business)
    return f


# --- building the matrices -------------------------------------------------------------------


def accounts_by_entity(dataset: Dataset) -> dict[str, list[Account]]:
    owned: dict[str, list[Account]] = defaultdict(list)
    for account in dataset.accounts.values():
        owned[account.entity].append(account)
    return owned


def histories(dataset: Dataset, context: FeatureContext) -> dict[str, EntityHistory]:
    owned = accounts_by_entity(dataset)
    return {
        entity: EntityHistory.build(entity, rows, owned[entity], dataset.merchants, context)
        for entity, rows in sorted(dataset.entities.items())
    }


def aml_rows(h: EntityHistory, context: FeatureContext) -> list[dict]:
    """Every calendar week in which the entity transacted."""
    out = []
    for as_of in sorted({week_end(moment) for moment in h.t.tolist()}):
        row = {"entity_id": h.entity, "as_of": as_of}
        row.update(aml_features(h, as_of, context))
        out.append(row)
    return out


def fraud_rows(h: EntityHistory, context: FeatureContext) -> list[dict]:
    out = []
    for i in range(len(h.t)):
        row = {"entity_id": h.entity, "transaction_ref": h.refs[i], "ts": float(h.t[i])}
        row.update(fraud_features(h, i, context))
        out.append(row)
    return out


def build_matrices(dataset: Dataset) -> tuple["pd.DataFrame", "pd.DataFrame"]:  # noqa: F821
    import pandas as pd

    context = InMemoryContext(dataset)
    aml, fraud = [], []
    for h in histories(dataset, context).values():
        aml.extend(aml_rows(h, context))
        fraud.extend(fraud_rows(h, context))
    aml_frame = pd.DataFrame(aml, columns=["entity_id", "as_of", *AML_FEATURES])
    fraud_frame = pd.DataFrame(fraud, columns=["entity_id", "transaction_ref", "ts", *FRAUD_FEATURES])
    return aml_frame, fraud_frame
