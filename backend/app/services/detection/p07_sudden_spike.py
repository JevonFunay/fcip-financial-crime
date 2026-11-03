"""P07 Sudden spike in value (FRD §8.7): a step change in how much value a
subject moves, compared with its own recent weeks. Distinct from P04, which
counts transactions.

Weeks are calendar weeks, Monday to Sunday in Asia/Jakarta. For each week the
total value (both directions) is compared with the mean of the previous
BASELINE_WEEKS weeks (weeks without activity count as zero once the subject has
started transacting). The rule fires when the rise is at least
MIN_ABSOLUTE_INCREASE and the week is at least SPIKE_MULTIPLE times the baseline
mean. With fewer than MIN_BASELINE_WEEKS weeks of history the week is not judged
(counted in the run's notes). The alert cites the largest contributing
transactions, at most MAX_EVIDENCE_TRANSACTIONS of them.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.rule import RuleVersion
from app.services.detection import common as c

PATTERN_CODE = "P07_SUDDEN_SPIKE"
TEMPLATE_VERSION = "p07_sudden_spike_v1"


@dataclass(frozen=True)
class P07Parameters:
    baseline_weeks: int
    min_absolute_increase: Decimal
    spike_multiple: Decimal
    min_baseline_weeks: int
    high_severity_multiple: Decimal
    max_evidence_transactions: int
    currency: str

    @classmethod
    def from_rule_version(cls, version: RuleVersion) -> "P07Parameters":
        c.require_window(version, TEMPLATE_VERSION, rolling=False, unit=timedelta(days=7))
        if version.window_length != timedelta(days=7):
            raise ValueError(f"{TEMPLATE_VERSION} evaluates one calendar week, not {version.window_length}")
        v, t = version.parameters, TEMPLATE_VERSION
        return cls(
            baseline_weeks=c.as_int(v, "baseline_weeks", t, minimum=1),
            min_absolute_increase=c.as_decimal(v, "min_absolute_increase", t, minimum=Decimal(1)),
            spike_multiple=c.as_decimal(v, "spike_multiple", t, minimum=Decimal(1)),
            min_baseline_weeks=c.as_int(v, "min_baseline_weeks", t, minimum=1),
            high_severity_multiple=c.as_decimal(v, "high_severity_multiple", t, minimum=Decimal(1)),
            max_evidence_transactions=c.as_int(v, "max_evidence_transactions", t, minimum=1),
            currency=c.as_text(v, "currency", t),
        )


def _week_start(moment):
    day = c.local_date(moment)
    return day - timedelta(days=day.weekday())


def evaluate(db: Session, params: P07Parameters, *, period=None, cache: c.RowCache | None = None) -> c.PatternEvaluation:
    rows = (cache or c.RowCache(db)).rows(params.currency)
    candidates: list[c.PatternCandidate] = []
    skipped = 0
    customers = c.group_by(rows, lambda r: r.customer_id)
    for customer_rows in customers.values():
        weeks = c.group_by(customer_rows, lambda r: _week_start(r.date))
        totals = {w: sum((t.amount for t in txns), Decimal(0)) for w, txns in weeks.items()}
        first_week = min(totals)
        for week in sorted(totals):
            available = min(params.baseline_weeks, (week - first_week).days // 7)
            if available < params.min_baseline_weeks:
                skipped += 1
                continue
            baseline = [totals.get(week - timedelta(weeks=k), Decimal(0)) for k in range(1, available + 1)]
            mean = sum(baseline, Decimal(0)) / available
            value = totals[week]
            increase = value - mean
            multiple_hit = value >= params.spike_multiple * mean
            if increase < params.min_absolute_increase or not multiple_hit:
                continue
            start = c.local_midnight(week)
            if not c.in_period(start, period):
                continue
            top = sorted(weeks[week], key=lambda t: (-t.amount, t.ref))[: params.max_evidence_transactions]
            top = tuple(sorted(top, key=lambda t: (t.date, t.ref)))
            observed = (value / mean) if mean else None
            candidates.append(
                c.PatternCandidate(
                    customer_id=top[0].customer_id, customer_ref=top[0].customer_ref, transactions=top,
                    window_start=start, window_end=start + timedelta(days=7), anchor=start,
                    factors={
                        "PERIOD_VALUE": c.money(value),
                        "BASELINE_MEAN_WEEKLY": c.money(mean),
                        "BASELINE_P95_WEEKLY": c.money(c.percentile(baseline, 95)),
                        "ABSOLUTE_INCREASE": c.money(increase),
                        "MIN_ABSOLUTE_INCREASE_APPLIED": c.money(params.min_absolute_increase),
                        "SPIKE_MULTIPLE": str(observed.quantize(c.CENTS)) if observed is not None else None,
                        "SPIKE_MULTIPLE_APPLIED": str(params.spike_multiple),
                        "BASELINE_WEEKS_AVAILABLE": available,
                        "TOP_CONTRIBUTING_TXN_IDS": [t.ref for t in sorted(top, key=lambda t: (-t.amount, t.ref))],
                        "WEEK_TXN_COUNT": len(weeks[week]),
                        "SUGGESTED_SEVERITY": "HIGH" if observed is None or observed > params.high_severity_multiple else "MEDIUM",
                    },
                    reason=(
                        f"P07 Sudden spike: {params.currency} {value:,.0f} in the week of {week} against a "
                        f"{available}-week average of {params.currency} {mean:,.0f}"
                    ),
                )
            )
    return c.PatternEvaluation(
        len(rows), len(customers), candidates,
        {"week(s) without enough baseline history": skipped} if skipped else {},
    )


TEMPLATE = c.PatternTemplate(PATTERN_CODE, TEMPLATE_VERSION, P07Parameters.from_rule_version, evaluate,
                             "A calendar week whose total value is far above the subject's recent weekly average")
