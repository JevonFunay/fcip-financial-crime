"""P09 Shared device (FRD §8.9): one device used to operate an unusual number of
different entities or accounts.

Per device, within a rolling window, the rule counts the distinct entities and
the distinct accounts that transacted through it and fires when either reaches
its minimum. This is a device-scope rule: one alert per device and window,
attached to the entity with the most transactions on the device (ties: lowest
customer reference); every entity involved is listed in the alert's factors.

Not yet available, and reported as such instead of guessed: the household and
low-specificity context that FRD §8.9 uses to weigh known shared-use settings
(families, agent kiosks). That needs the relationship graph (FR-402).
"""

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta, timezone

from sqlalchemy.orm import Session

from app.models.rule import RuleVersion
from app.services.detection import common as c

PATTERN_CODE = "P09_SHARED_DEVICE"
TEMPLATE_VERSION = "p09_shared_device_v1"


@dataclass(frozen=True)
class P09Parameters:
    min_distinct_entities: int
    min_distinct_accounts: int
    window_days: int  # from the rule version's window length
    currency: str

    @classmethod
    def from_rule_version(cls, version: RuleVersion) -> "P09Parameters":
        window = c.require_window(version, TEMPLATE_VERSION, rolling=True, unit=timedelta(days=1))
        v, t = version.parameters, TEMPLATE_VERSION
        return cls(
            min_distinct_entities=c.as_int(v, "min_distinct_entities", t, minimum=2),
            min_distinct_accounts=c.as_int(v, "min_distinct_accounts", t, minimum=2),
            window_days=window.days,
            currency=c.as_text(v, "currency", t),
        )


def evaluate(db: Session, params: P09Parameters, *, period=None, cache: c.RowCache | None = None) -> c.PatternEvaluation:
    rows = (cache or c.RowCache(db)).rows(params.currency)
    span = timedelta(days=params.window_days)
    candidates: list[c.PatternCandidate] = []
    devices = c.group_by((r for r in rows if r.device_ref), lambda r: r.device_ref)
    for device_ref, device_rows in devices.items():
        device_rows.sort(key=lambda r: (r.date, r.ref))
        i = 0
        while i < len(device_rows):
            start = device_rows[i].date
            end = start + span
            j = i
            while j < len(device_rows) and device_rows[j].date < end:
                j += 1
            window = tuple(device_rows[i:j])
            entities = {t.customer_id for t in window}
            accounts = {t.account_id for t in window}
            met = [
                name for name, hit in (
                    ("ENTITIES", len(entities) >= params.min_distinct_entities),
                    ("ACCOUNTS", len(accounts) >= params.min_distinct_accounts),
                ) if hit
            ]
            if met and c.in_period(start, period):
                per_entity = Counter(t.customer_ref for t in window)
                subject_ref = sorted(per_entity, key=lambda ref: (-per_entity[ref], ref))[0]
                subject = next(t for t in window if t.customer_ref == subject_ref)
                days_by_entity: dict = {}
                for t in window:
                    days_by_entity.setdefault(c.local_date(t.date), set()).add(t.customer_id)
                candidates.append(
                    c.PatternCandidate(
                        customer_id=subject.customer_id, customer_ref=subject_ref, transactions=window,
                        window_start=start, window_end=end, anchor=start,
                        factors={
                            "DEVICE_ID": device_ref,
                            "DISTINCT_ENTITY_COUNT": len(entities),
                            "DISTINCT_ACCOUNT_COUNT": len(accounts),
                            "ENTITY_IDS": sorted(per_entity),
                            "FIRST_SEEN": window[0].date.astimezone(timezone.utc).isoformat(),
                            "LAST_SEEN": window[-1].date.astimezone(timezone.utc).isoformat(),
                            "CONCURRENT_USE_FLAG": any(len(ids) >= 2 for ids in days_by_entity.values()),
                            "SPECIFICITY_FLAG": "UNASSESSED",
                            "HOUSEHOLD_RELATION_COUNT": "DATA_UNAVAILABLE",
                            "MIN_DISTINCT_ENTITIES_APPLIED": params.min_distinct_entities,
                            "MIN_DISTINCT_ACCOUNTS_APPLIED": params.min_distinct_accounts,
                            "CONDITIONS_MET": met,
                        },
                        reason=(
                            f"P09 Shared device: device {device_ref} used by {len(entities)} entities and "
                            f"{len(accounts)} accounts within {params.window_days} days from "
                            f"{start.astimezone(timezone.utc).date()}"
                        ),
                    )
                )
                i = j
            else:
                i += 1
    return c.PatternEvaluation(len(rows), len(devices), candidates)


TEMPLATE = c.PatternTemplate(PATTERN_CODE, TEMPLATE_VERSION, P09Parameters.from_rule_version, evaluate,
                             "One device used to operate an unusual number of entities or accounts")
