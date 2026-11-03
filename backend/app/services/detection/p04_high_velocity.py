"""P04 High velocity (FRD §8.4): a burst in the number of transactions, measured
against the subject's own history.

A rolling window (default 24 h) opens at each transaction. The rule fires when
the count in the window reaches MIN_COUNT_FLOOR *and* is far above the subject's
daily baseline: at least DEVIATION_SIGMA standard deviations above the mean, or
at least ALT_MULTIPLE times the mean. The absolute floor means a subject that
moves from 1 to 4 transactions never fires. The baseline is the BASELINE_DAYS
local days (Asia/Jakarta) before the window's day, zero-activity days included;
with fewer than MIN_BASELINE_DAYS days of history the relative condition is not
judged (counted in the run's notes, never silently passed).
"""

from dataclasses import dataclass
from datetime import timedelta, timezone
from decimal import Decimal
from math import sqrt

from sqlalchemy.orm import Session

from app.models.rule import RuleVersion
from app.services.detection import common as c

PATTERN_CODE = "P04_HIGH_VELOCITY"
TEMPLATE_VERSION = "p04_high_velocity_v1"


@dataclass(frozen=True)
class P04Parameters:
    window_hours: int  # from the rule version's window length
    baseline_days: int
    min_count_floor: int
    deviation_sigma: Decimal
    alt_multiple: Decimal
    min_baseline_days: int
    currency: str

    @classmethod
    def from_rule_version(cls, version: RuleVersion) -> "P04Parameters":
        window = c.require_window(version, TEMPLATE_VERSION, rolling=True, unit=timedelta(hours=1))
        v, t = version.parameters, TEMPLATE_VERSION
        return cls(
            window_hours=int(window.total_seconds() // 3600),
            baseline_days=c.as_int(v, "baseline_days", t, minimum=1),
            min_count_floor=c.as_int(v, "min_count_floor", t, minimum=1),
            deviation_sigma=c.as_decimal(v, "deviation_sigma", t, minimum=Decimal(0)),
            alt_multiple=c.as_decimal(v, "alt_multiple", t, minimum=Decimal(1)),
            min_baseline_days=c.as_int(v, "min_baseline_days", t, minimum=1),
            currency=c.as_text(v, "currency", t),
        )


def evaluate(db: Session, params: P04Parameters, *, period=None, cache: c.RowCache | None = None) -> c.PatternEvaluation:
    rows = (cache or c.RowCache(db)).rows(params.currency)
    span = timedelta(hours=params.window_hours)
    candidates: list[c.PatternCandidate] = []
    skipped = 0
    customers = c.group_by(rows, lambda r: r.customer_id)
    for customer_rows in customers.values():
        daily_count: dict = {}
        daily_value: dict = {}
        for r in customer_rows:
            day = c.local_date(r.date)
            daily_count[day] = daily_count.get(day, 0) + 1
            daily_value[day] = daily_value.get(day, Decimal(0)) + r.amount
        first_day = c.local_date(customer_rows[0].date)
        i = 0
        while i < len(customer_rows):
            start = customer_rows[i].date
            end = start + span
            j = i
            while j < len(customer_rows) and customer_rows[j].date < end:
                j += 1
            count = j - i
            if count < params.min_count_floor or not c.in_period(start, period):
                i += 1
                continue
            window_day = c.local_date(start)
            available = min(params.baseline_days, (window_day - first_day).days)
            if available < params.min_baseline_days:
                skipped += 1
                i += 1
                continue
            days = [window_day - timedelta(days=k) for k in range(1, available + 1)]
            counts = [daily_count.get(d, 0) for d in days]
            values = [daily_value.get(d, Decimal(0)) for d in days]
            mean = sum(counts) / available
            sd = sqrt(sum((x - mean) ** 2 for x in counts) / available)
            sigma_hit = count >= mean + float(params.deviation_sigma) * sd
            multiple_hit = count >= float(params.alt_multiple) * mean
            if not (sigma_hit or multiple_hit):
                i += 1
                continue
            window = tuple(customer_rows[i:j])
            sigma_obs = (count - mean) / sd if sd > 0 else None
            candidates.append(
                c.PatternCandidate(
                    customer_id=window[0].customer_id, customer_ref=window[0].customer_ref, transactions=window,
                    window_start=start, window_end=end, anchor=start,
                    factors={
                        "TXN_COUNT_WINDOW": count,
                        "BASELINE_MEAN_COUNT": round(mean, 4),
                        "BASELINE_SD_COUNT": round(sd, 4),
                        "DEVIATION_SIGMA": round(sigma_obs, 2) if sigma_obs is not None else None,
                        "DEVIATION_SIGMA_APPLIED": str(params.deviation_sigma),
                        "ALT_MULTIPLE_APPLIED": str(params.alt_multiple),
                        "MIN_COUNT_FLOOR_APPLIED": params.min_count_floor,
                        "VALUE_WINDOW": c.money(sum((t.amount for t in window), Decimal(0))),
                        "BASELINE_MEAN_VALUE": c.money(sum(values, Decimal(0)) / available),
                        "BASELINE_DAYS_AVAILABLE": available,
                        "CONDITIONS_MET": [n for n, hit in (("SIGMA", sigma_hit), ("MULTIPLE", multiple_hit)) if hit],
                        "CHANNEL_MIX": {ch: sum(1 for t in window if t.channel == ch) for ch in sorted({t.channel for t in window})},
                    },
                    reason=(
                        f"P04 High velocity: {count} transactions within {params.window_hours} hours from "
                        f"{start.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC against a daily baseline of "
                        f"{mean:.2f} over {available} days"
                    ),
                )
            )
            i = j
    return c.PatternEvaluation(
        len(rows), len(customers), candidates,
        {"window(s) without enough baseline history": skipped} if skipped else {},
    )


TEMPLATE = c.PatternTemplate(PATTERN_CODE, TEMPLATE_VERSION, P04Parameters.from_rule_version, evaluate,
                             "A burst of transactions well above the subject's own daily baseline")
