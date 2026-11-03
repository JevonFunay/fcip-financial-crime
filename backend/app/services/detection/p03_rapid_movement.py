"""P03 Rapid movement / pass-through (FRD §8.3): value that comes into an account
and leaves again quickly, leaving little behind.

For each qualifying credit (at least MIN_INBOUND_AMOUNT), the debits of the same
account inside the following MAX_ELAPSED_HOURS are summed. The rule fires when
there are at least MIN_OUTBOUND_COUNT of them and they total at least
PASSTHROUGH_RATIO of the credit. The window is rolling from the credit, so a
credit at 23:50 and a debit at 00:10 the next day still count together. A debit
is evidence for at most one alert.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.rule import RuleVersion
from app.services.detection import common as c

PATTERN_CODE = "P03_RAPID_MOVEMENT"
TEMPLATE_VERSION = "p03_rapid_movement_v1"


@dataclass(frozen=True)
class P03Parameters:
    max_elapsed_hours: int  # from the rule version's window length
    passthrough_ratio: Decimal
    min_inbound_amount: Decimal
    min_outbound_count: int
    currency: str

    @classmethod
    def from_rule_version(cls, version: RuleVersion) -> "P03Parameters":
        window = c.require_window(version, TEMPLATE_VERSION, rolling=True, unit=timedelta(hours=1))
        v, t = version.parameters, TEMPLATE_VERSION
        return cls(
            max_elapsed_hours=int(window.total_seconds() // 3600),
            passthrough_ratio=c.as_ratio(v, "passthrough_ratio", t),
            min_inbound_amount=c.as_decimal(v, "min_inbound_amount", t, minimum=Decimal(1)),
            min_outbound_count=c.as_int(v, "min_outbound_count", t, minimum=1),
            currency=c.as_text(v, "currency", t),
        )


def evaluate(db: Session, params: P03Parameters, *, period=None, cache: c.RowCache | None = None) -> c.PatternEvaluation:
    rows = (cache or c.RowCache(db)).rows(params.currency)
    span = timedelta(hours=params.max_elapsed_hours)
    candidates: list[c.PatternCandidate] = []
    accounts = c.group_by(rows, lambda r: r.account_id)
    for account_rows in accounts.values():
        debits = [r for r in account_rows if r.direction == "DEBIT"]
        debit_dates = [r.date for r in debits]
        used: set = set()
        for credit in account_rows:
            if credit.direction != "CREDIT" or credit.amount < params.min_inbound_amount:
                continue
            lo = bisect_right(debit_dates, credit.date)  # strictly after the credit
            hi = bisect_right(debit_dates, credit.date + span)  # window end inclusive: "within 24 hours"
            outbound = [d for d in debits[lo:hi] if d.id not in used]
            total = sum((d.amount for d in outbound), Decimal(0))
            if len(outbound) < params.min_outbound_count or total < params.passthrough_ratio * credit.amount:
                continue
            if not c.in_period(credit.date, period):
                continue
            used.update(d.id for d in outbound)
            elapsed = (outbound[-1].date - credit.date).total_seconds() / 3600
            ratio = total / credit.amount
            candidates.append(
                c.PatternCandidate(
                    customer_id=credit.customer_id, customer_ref=credit.customer_ref,
                    transactions=(credit, *outbound), window_start=credit.date, window_end=credit.date + span,
                    anchor=credit.date,
                    factors={
                        "INBOUND_AMOUNT": c.money(credit.amount),
                        "INBOUND_TXN_ID": credit.ref,
                        "OUTBOUND_AGGREGATE": c.money(total),
                        "PASSTHROUGH_RATIO": str(ratio.quantize(Decimal("0.0001"))),
                        "PASSTHROUGH_RATIO_APPLIED": str(params.passthrough_ratio),
                        "ELAPSED_HOURS": round(elapsed, 2),
                        "MAX_ELAPSED_HOURS_APPLIED": params.max_elapsed_hours,
                        "RESIDUAL_BALANCE_RATIO": str(max(Decimal(0), 1 - ratio).quantize(Decimal("0.0001"))),
                        "OUTBOUND_TXN_COUNT": len(outbound),
                        "OUTBOUND_COUNTERPARTY_COUNT": len({d.counterparty_ref for d in outbound if d.counterparty_ref}),
                    },
                    reason=(
                        f"P03 Rapid movement: {params.currency} {credit.amount:,.0f} came in on "
                        f"{credit.date.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC and {ratio:.0%} left within "
                        f"{elapsed:.1f} hours across {len(outbound)} debit(s)"
                    ),
                )
            )
    return c.PatternEvaluation(len(rows), len(accounts), candidates)


TEMPLATE = c.PatternTemplate(PATTERN_CODE, TEMPLATE_VERSION, P03Parameters.from_rule_version, evaluate,
                             "A credit that is moved out again, almost in full, within a short interval")
