"""P06 Round amounts (FRD §8.6): a concentration of exactly-round transaction
values.

Round amounts are common and harmless (top-ups, instalments), so the rule needs
both a minimum count and a minimum proportion before it fires. Among the
subject's transactions of at least MIN_AMOUNT in a rolling window, those that
are an exact multiple of ROUNDING_UNIT are "round"; the rule fires when there are
at least MIN_COUNT of them and they are at least MIN_PROPORTION of all
qualifying transactions. Exactness is decimal: 5,000,001 is not round.
"""

from dataclasses import dataclass
from datetime import timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.rule import RuleVersion
from app.services.detection import common as c

PATTERN_CODE = "P06_ROUND_AMOUNTS"
TEMPLATE_VERSION = "p06_round_amounts_v1"


@dataclass(frozen=True)
class P06Parameters:
    rounding_unit: Decimal
    min_amount: Decimal
    min_count: int
    min_proportion: Decimal
    window_days: int  # from the rule version's window length
    currency: str

    @classmethod
    def from_rule_version(cls, version: RuleVersion) -> "P06Parameters":
        window = c.require_window(version, TEMPLATE_VERSION, rolling=True, unit=timedelta(days=1))
        v, t = version.parameters, TEMPLATE_VERSION
        return cls(
            rounding_unit=c.as_decimal(v, "rounding_unit", t, minimum=Decimal(1)),
            min_amount=c.as_decimal(v, "min_amount", t, minimum=Decimal(0)),
            min_count=c.as_int(v, "min_count", t, minimum=1),
            min_proportion=c.as_ratio(v, "min_proportion", t),
            window_days=window.days,
            currency=c.as_text(v, "currency", t),
        )


def evaluate(db: Session, params: P06Parameters, *, period=None, cache: c.RowCache | None = None) -> c.PatternEvaluation:
    rows = (cache or c.RowCache(db)).rows(params.currency)
    span = timedelta(days=params.window_days)
    candidates: list[c.PatternCandidate] = []
    customers = c.group_by(rows, lambda r: r.customer_id)
    for customer_rows in customers.values():
        qualifying = [r for r in customer_rows if r.amount >= params.min_amount]
        i = 0
        while i < len(qualifying):
            if qualifying[i].amount % params.rounding_unit != 0:
                i += 1
                continue
            start = qualifying[i].date
            end = start + span
            j = i
            while j < len(qualifying) and qualifying[j].date < end:
                j += 1
            window = qualifying[i:j]
            round_txns = tuple(t for t in window if t.amount % params.rounding_unit == 0)
            proportion = Decimal(len(round_txns)) / Decimal(len(window))
            if len(round_txns) >= params.min_count and proportion >= params.min_proportion and c.in_period(start, period):
                candidates.append(
                    c.PatternCandidate(
                        customer_id=round_txns[0].customer_id, customer_ref=round_txns[0].customer_ref,
                        transactions=round_txns, window_start=start, window_end=end, anchor=start,
                        factors={
                            "ROUND_TXN_COUNT": len(round_txns),
                            "TOTAL_QUALIFYING_TXN_COUNT": len(window),
                            "ROUND_PROPORTION": str(proportion.quantize(Decimal("0.0001"))),
                            "MIN_PROPORTION_APPLIED": str(params.min_proportion),
                            "MIN_COUNT_APPLIED": params.min_count,
                            "ROUNDING_UNIT_APPLIED": c.money(params.rounding_unit),
                            "DISTINCT_COUNTERPARTIES": len({t.counterparty_ref for t in round_txns if t.counterparty_ref}),
                            "AMOUNT_SET": sorted({c.money(t.amount) for t in round_txns}, key=Decimal),
                            "SUGGESTED_SEVERITY": "LOW",
                        },
                        reason=(
                            f"P06 Round amounts: {len(round_txns)} of {len(window)} transactions of at least "
                            f"{params.currency} {params.min_amount:,.0f} are exact multiples of {params.rounding_unit:,.0f} "
                            f"({proportion:.0%}) from {start.astimezone(timezone.utc).date()}"
                        ),
                    )
                )
                i = j
            else:
                i += 1
    return c.PatternEvaluation(len(rows), len(customers), candidates)


TEMPLATE = c.PatternTemplate(PATTERN_CODE, TEMPLATE_VERSION, P06Parameters.from_rule_version, evaluate,
                             "A concentration of exact round-number transaction values")
