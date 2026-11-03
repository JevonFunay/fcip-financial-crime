"""P01 Large transaction (FRD §8.1): one transaction far outside the scale of the
population or of the subject itself.

Two conditions, reported separately so the reviewer sees which one applied:
absolute (amount at or above a threshold for the entity type) and relative
(amount above a multiple of the subject's own 90-day p95). The relative
condition is only judged when the subject has at least MIN_HISTORY_TXNS earlier
transactions in the baseline window; otherwise it is not evaluated and counted
in the run's notes.
"""

from collections import deque
from dataclasses import dataclass
from datetime import timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.rule import RuleVersion
from app.services.detection import common as c

PATTERN_CODE = "P01_LARGE_TXN"
TEMPLATE_VERSION = "p01_large_txn_v1"


@dataclass(frozen=True)
class P01Parameters:
    abs_threshold_individual: Decimal
    abs_threshold_merchant: Decimal
    relative_multiple: Decimal
    min_history_txns: int
    baseline_days: int  # from the rule version's window length
    currency: str

    @classmethod
    def from_rule_version(cls, version: RuleVersion) -> "P01Parameters":
        window = c.require_window(version, TEMPLATE_VERSION, rolling=True, unit=timedelta(days=1))
        v, t = version.parameters, TEMPLATE_VERSION
        return cls(
            abs_threshold_individual=c.as_decimal(v, "abs_threshold_individual", t, minimum=Decimal(1)),
            abs_threshold_merchant=c.as_decimal(v, "abs_threshold_merchant", t, minimum=Decimal(1)),
            relative_multiple=c.as_decimal(v, "relative_multiple", t, minimum=Decimal(1)),
            min_history_txns=c.as_int(v, "min_history_txns", t, minimum=1),
            baseline_days=window.days,
            currency=c.as_text(v, "currency", t),
        )


def evaluate(db: Session, params: P01Parameters, *, period=None, cache: c.RowCache | None = None) -> c.PatternEvaluation:
    rows = (cache or c.RowCache(db)).rows(params.currency)
    baseline = timedelta(days=params.baseline_days)
    candidates: list[c.PatternCandidate] = []
    insufficient = 0
    for customer_rows in c.group_by(rows, lambda r: r.customer_id).values():
        history: deque = deque()  # earlier transactions inside the baseline window
        for row in customer_rows:
            while history and history[0].date < row.date - baseline:
                history.popleft()
            threshold = params.abs_threshold_merchant if row.entity_type == "BUSINESS" else params.abs_threshold_individual
            abs_hit = row.amount >= threshold
            p95 = None
            rel_hit = False
            if len(history) >= params.min_history_txns:
                p95 = c.percentile([h.amount for h in history], 95)
                rel_hit = p95 > 0 and row.amount > params.relative_multiple * p95
            else:
                insufficient += 1
            if (abs_hit or rel_hit) and c.in_period(row.date, period):
                conditions = [name for name, hit in (("ABSOLUTE", abs_hit), ("RELATIVE", rel_hit)) if hit]
                factors = {
                    "AMOUNT_IDR": c.money(row.amount),
                    "ABS_THRESHOLD_APPLIED": c.money(threshold),
                    "ABS_CONDITION_MET": abs_hit,
                    "ENTITY_P95_90D": c.money(p95) if p95 is not None else None,
                    "RELATIVE_MULTIPLE_APPLIED": str(params.relative_multiple),
                    "RELATIVE_MULTIPLE_OBSERVED": str((row.amount / p95).quantize(c.CENTS)) if p95 else None,
                    "REL_CONDITION_MET": rel_hit,
                    "HISTORY_TXN_COUNT": len(history),
                    "COUNTERPARTY_ID": row.counterparty_ref,
                    "CHANNEL": row.channel,
                    "CONDITIONS_MET": conditions,
                    "SUGGESTED_SEVERITY": "HIGH" if abs_hit else "MEDIUM",
                }
                candidates.append(
                    c.PatternCandidate(
                        customer_id=row.customer_id, customer_ref=row.customer_ref, transactions=(row,),
                        window_start=row.date - baseline, window_end=row.date, anchor=row.date, factors=factors,
                        reason=(
                            f"P01 Large transaction: {params.currency} {row.amount:,.0f} "
                            f"({' + '.join(x.lower() for x in conditions)} "
                            f"condition) on {row.date.astimezone(timezone.utc).date()}"
                        ),
                    )
                )
            history.append(row)
    return c.PatternEvaluation(
        transactions_scanned=len(rows),
        entities_scanned=len({r.customer_id for r in rows}),
        candidates=candidates,
        notes={"transaction(s) without enough history for the relative condition": insufficient} if insufficient else {},
    )


TEMPLATE = c.PatternTemplate(PATTERN_CODE, TEMPLATE_VERSION, P01Parameters.from_rule_version, evaluate,
                             "A single transaction above an absolute threshold or a multiple of the subject's own p95")
