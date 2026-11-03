"""P05 Dormant account reactivation (FRD §8.5): an account silent for a long
period that then moves a material amount.

Dormancy is derived from the transaction history (a gap of at least
DORMANCY_DAYS between two consecutive transactions of the account), never from
the account's status field, which the platform does not own. An account with no
earlier transaction is new, not dormant. After the gap, the transactions in the
following REACTIVATION window must reach MIN_REACTIVATION_AMOUNT in total or
MIN_REACTIVATION_COUNT in number.
"""

from dataclasses import dataclass
from datetime import timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.rule import RuleVersion
from app.services.detection import common as c

PATTERN_CODE = "P05_DORMANT_REACTIVATION"
TEMPLATE_VERSION = "p05_dormant_reactivation_v1"


@dataclass(frozen=True)
class P05Parameters:
    dormancy_days: int
    reactivation_window_days: int  # from the rule version's window length
    min_reactivation_amount: Decimal
    min_reactivation_count: int
    high_severity_aggregate: Decimal
    currency: str

    @classmethod
    def from_rule_version(cls, version: RuleVersion) -> "P05Parameters":
        window = c.require_window(version, TEMPLATE_VERSION, rolling=True, unit=timedelta(days=1))
        v, t = version.parameters, TEMPLATE_VERSION
        return cls(
            dormancy_days=c.as_int(v, "dormancy_days", t, minimum=1),
            reactivation_window_days=window.days,
            min_reactivation_amount=c.as_decimal(v, "min_reactivation_amount", t, minimum=Decimal(1)),
            min_reactivation_count=c.as_int(v, "min_reactivation_count", t, minimum=1),
            high_severity_aggregate=c.as_decimal(v, "high_severity_aggregate", t, minimum=Decimal(1)),
            currency=c.as_text(v, "currency", t),
        )


def evaluate(db: Session, params: P05Parameters, *, period=None, cache: c.RowCache | None = None) -> c.PatternEvaluation:
    rows = (cache or c.RowCache(db)).rows(params.currency)
    dormancy = timedelta(days=params.dormancy_days)
    span = timedelta(days=params.reactivation_window_days)
    candidates: list[c.PatternCandidate] = []
    accounts = c.group_by(rows, lambda r: r.account_id)
    for account_rows in accounts.values():
        i = 1
        while i < len(account_rows):
            gap = account_rows[i].date - account_rows[i - 1].date
            if gap < dormancy:
                i += 1
                continue
            start = account_rows[i].date
            j = i
            while j < len(account_rows) and account_rows[j].date < start + span:
                j += 1
            window = tuple(account_rows[i:j])
            total = sum((t.amount for t in window), Decimal(0))
            if (total >= params.min_reactivation_amount or len(window) >= params.min_reactivation_count) and c.in_period(start, period):
                prior = account_rows[:i]
                seen_counterparties = {t.counterparty_ref for t in prior if t.counterparty_ref}
                seen_devices = {t.device_ref for t in prior if t.device_ref}
                new_counterparties = {t.counterparty_ref for t in window if t.counterparty_ref} - seen_counterparties
                new_devices = {t.device_ref for t in window if t.device_ref} - seen_devices
                candidates.append(
                    c.PatternCandidate(
                        customer_id=window[0].customer_id, customer_ref=window[0].customer_ref, transactions=window,
                        window_start=start, window_end=start + span, anchor=start,
                        factors={
                            "DORMANCY_DAYS_OBSERVED": gap.days,
                            "DORMANCY_DAYS_APPLIED": params.dormancy_days,
                            "LAST_ACTIVITY_DATE": account_rows[i - 1].date.astimezone(timezone.utc).date().isoformat(),
                            "REACTIVATION_DATE": start.astimezone(timezone.utc).date().isoformat(),
                            "REACTIVATION_TXN_COUNT": len(window),
                            "REACTIVATION_AGGREGATE": c.money(total),
                            "MIN_REACTIVATION_AMOUNT_APPLIED": c.money(params.min_reactivation_amount),
                            "MIN_REACTIVATION_COUNT_APPLIED": params.min_reactivation_count,
                            "NEW_COUNTERPARTY_COUNT": len(new_counterparties),
                            "NEW_DEVICE_FLAG": bool(new_devices),
                            "SUGGESTED_SEVERITY": "HIGH" if total > params.high_severity_aggregate else "MEDIUM",
                        },
                        reason=(
                            f"P05 Dormant reactivation: account silent {gap.days} days, then {len(window)} transaction(s) "
                            f"totalling {params.currency} {total:,.0f} from {start.astimezone(timezone.utc).date()}"
                        ),
                    )
                )
            i = max(j, i + 1)
    return c.PatternEvaluation(len(rows), len(accounts), candidates)


TEMPLATE = c.PatternTemplate(PATTERN_CODE, TEMPLATE_VERSION, P05Parameters.from_rule_version, evaluate,
                             "An account silent for a long period that becomes active again with a material amount")
