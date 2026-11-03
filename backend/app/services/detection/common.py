"""Shared machinery for the pattern templates P01 and P03-P10 (FRD §8, TRD §9).

P02 was the first template and keeps its own module; every pattern after it
shares what is here: reading the transactions once, the typed candidate a
template returns, and the run that turns candidates into alerts. The logic of
each pattern lives in its own module; its parameters live in the ACTIVE rule
version in the database (TRD ADR-006), never in code.

TEMPORARY SIMPLIFICATION (same as P02): the FRD aggregates per *entity*. Entity
resolution is not built, so customer_id stands in for entity_id.
"""

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.alert import Alert, AlertTransaction
from app.models.customer import Customer
from app.models.enums import AlertStatus
from app.models.rule import RuleVersion
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.detection import DetectedAlert, DetectionRunSummary, EvaluatedRule
from app.services.audit import OBJECT_ALERT, OBJECT_DETECTION_RUN, record_transition
from app.services.detection.active_rules import active_rule_versions

# ADR-010 / AS-09: timestamps are stored in UTC, business windows (days, weeks,
# opening hours) are evaluated in Asia/Jakarta. Never assume a fixed offset.
LOCAL_TZ = ZoneInfo("Asia/Jakarta")
CENTS = Decimal("0.01")


class TxRow(NamedTuple):
    """One transaction with the entity it belongs to; a plain tuple so a
    500,000-row run does not build 500,000 ORM objects."""

    id: uuid.UUID
    ref: str
    account_id: uuid.UUID
    customer_id: uuid.UUID
    customer_ref: str
    entity_type: str
    date: datetime
    amount: Decimal
    currency: str
    direction: str
    channel: str
    counterparty_ref: str | None
    counterparty_country: str | None
    merchant_ref: str | None
    device_ref: str | None
    status: str | None


@dataclass(frozen=True)
class PatternCandidate:
    """One finding of a template: the evidence, the window it was found in and
    the factors (observed value next to the threshold applied, FRD §8.0)."""

    customer_id: uuid.UUID
    customer_ref: str
    transactions: tuple[TxRow, ...]
    window_start: datetime
    window_end: datetime
    # The moment the finding is "about"; a simulation period filters on it.
    anchor: datetime
    factors: dict[str, Any]
    reason: str


@dataclass
class PatternEvaluation:
    transactions_scanned: int
    entities_scanned: int
    candidates: list[PatternCandidate]
    # Things the template deliberately did not evaluate (e.g. history too short
    # for the relative branch), so "no alert" can be told apart from "not judged".
    notes: dict[str, int] = field(default_factory=dict)


class RowCache:
    """Loads a currency's transactions once per run; every template reads the same list."""

    def __init__(self, db: Session):
        self._db = db
        self._by_currency: dict[str, list[TxRow]] = {}

    def rows(self, currency: str) -> list[TxRow]:
        if currency not in self._by_currency:
            statement = (
                select(
                    Transaction.id, Transaction.transaction_ref, Transaction.account_id, Account.customer_id,
                    Customer.customer_ref, Customer.entity_type, Transaction.transaction_date, Transaction.amount,
                    Transaction.currency, Transaction.direction, Transaction.channel, Transaction.counterparty_ref,
                    Transaction.counterparty_country, Transaction.merchant_ref, Transaction.device_ref,
                    Transaction.source_status,
                )
                .join(Account, Transaction.account_id == Account.id)
                .join(Customer, Account.customer_id == Customer.id)
                .where(Transaction.currency == currency)
                # Deterministic order keeps re-runs reproducible (SM-01).
                .order_by(Account.customer_id, Transaction.transaction_date, Transaction.transaction_ref)
            )
            self._by_currency[currency] = [
                TxRow(r[0], r[1], r[2], r[3], r[4], r[5].value, r[6], r[7], r[8], r[9].value, r[10], r[11], r[12],
                      r[13], r[14], r[15])
                for r in self._db.execute(statement)
            ]
        return self._by_currency[currency]


# ---- parameter parsing: a rule version that does not fit its template is refused, not approximated

def require(values: dict, key: str, template: str) -> Any:
    if key not in values:
        raise ValueError(f"{template}: parameter '{key}' is missing")
    return values[key]


def as_decimal(values: dict, key: str, template: str, *, minimum: Decimal | None = None) -> Decimal:
    try:
        value = Decimal(str(require(values, key, template)))
    except ArithmeticError as exc:
        raise ValueError(f"{template}: parameter '{key}' is not a number") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{template}: parameter '{key}' must be at least {minimum}")
    return value


def as_int(values: dict, key: str, template: str, *, minimum: int = 0) -> int:
    raw = require(values, key, template)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < minimum:
        raise ValueError(f"{template}: parameter '{key}' must be an integer of at least {minimum}")
    return raw


def as_ratio(values: dict, key: str, template: str) -> Decimal:
    value = as_decimal(values, key, template)
    if not Decimal(0) < value <= Decimal(1):
        raise ValueError(f"{template}: parameter '{key}' must be in (0, 1]")
    return value


def as_text(values: dict, key: str, template: str) -> str:
    raw = require(values, key, template)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{template}: parameter '{key}' must be a non-empty string")
    return raw


def require_window(version: RuleVersion, template: str, *, rolling: bool, unit: timedelta) -> timedelta:
    from app.models.enums import RuleWindowType

    expected = RuleWindowType.ROLLING if rolling else RuleWindowType.CALENDAR
    if version.window_type is not expected or version.window_length % unit:
        raise ValueError(
            f"{template} evaluates {expected.value} windows of whole {'days' if unit == timedelta(days=1) else 'hours'}, "
            f"not {version.window_type.value} {version.window_length}"
        )
    return version.window_length


# ---- small helpers shared by templates

def local_date(moment: datetime) -> date:
    return moment.astimezone(LOCAL_TZ).date()


def local_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=LOCAL_TZ).astimezone(timezone.utc)


def percentile(values: Sequence[Decimal | float], pct: float) -> Decimal:
    """Linear-interpolated percentile (the definition numpy uses by default)."""
    if not values:
        raise ValueError("percentile of nothing")
    ordered = sorted(Decimal(str(v)) for v in values)
    position = Decimal(str(pct)) / 100 * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def money(value: Decimal) -> str:
    return str(value.quantize(CENTS))


def group_by(rows: Iterable[TxRow], key: Callable[[TxRow], Any]) -> dict[Any, list[TxRow]]:
    groups: dict[Any, list[TxRow]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return groups


def in_period(anchor: datetime, period: tuple[datetime, datetime] | None) -> bool:
    return period is None or period[0] <= anchor < period[1]


@dataclass(frozen=True)
class PatternTemplate:
    """What a pattern module registers (see registry.py)."""

    pattern_code: str
    template_version: str
    parameters_from: Callable[[RuleVersion], Any]
    evaluate: Callable[..., PatternEvaluation]
    summary: str


# ---- the run

def _existing_evidence(db: Session, pattern_code: str) -> dict[tuple, Alert]:
    """Existing alerts of the pattern keyed by (rule, customer, exact evidence set), so a re-run recognises them."""
    rows = db.execute(
        select(Alert, AlertTransaction.transaction_id, RuleVersion.rule_id)
        .join(AlertTransaction, AlertTransaction.alert_id == Alert.id)
        .join(RuleVersion, Alert.rule_version_id == RuleVersion.id)
        .where(Alert.pattern_code == pattern_code)
    ).all()
    evidence: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    alerts: dict[uuid.UUID, tuple[Alert, uuid.UUID]] = {}
    for alert, transaction_id, rule_id in rows:
        alerts[alert.id] = (alert, rule_id)
        evidence[alert.id].add(transaction_id)
    return {
        (rule_id, alert.customer_id, frozenset(evidence[alert_id])): alert
        for alert_id, (alert, rule_id) in alerts.items()
    }


def candidate_details(template: PatternTemplate, candidate: PatternCandidate, run_id: uuid.UUID, currency: str) -> dict:
    return {
        **candidate.factors,
        "window_start": candidate.window_start.astimezone(timezone.utc).isoformat(),
        "window_end": candidate.window_end.astimezone(timezone.utc).isoformat(),
        "CURRENCY": currency,
        "TRANSACTION_REFS": sorted(t.ref for t in candidate.transactions),
        "TEMPLATE_VERSION": template.template_version,
        "DETECTION_RUN_ID": str(run_id),
    }


def run_pattern(
    db: Session, template: PatternTemplate, *, actor: User | None = None, cache: RowCache | None = None,
    commit: bool = True,
) -> DetectionRunSummary:
    """Evaluate every ACTIVE rule version of the pattern and raise the alerts it finds.

    Same guarantees as the P02 run: alerts are only created, never closed; a
    re-run on the same data raises nothing new; the run is audited even when it
    finds nothing.
    """
    cache = cache or RowCache(db)
    run_id = uuid.uuid4()
    versions = active_rule_versions(db, template.pattern_code)
    existing = _existing_evidence(db, template.pattern_code)
    detected: list[DetectedAlert] = []
    scans: list[str] = []
    created = 0
    for rule, version in versions:
        params = template.parameters_from(version)
        evaluation = template.evaluate(db, params, cache=cache)
        notes = "".join(f", {count} {name}" for name, count in sorted(evaluation.notes.items()))
        scans.append(
            f"{rule.rule_ref} v{version.version} scanned {evaluation.transactions_scanned} transaction(s) "
            f"across {evaluation.entities_scanned} entity/entities{notes}"
        )
        for candidate in evaluation.candidates:
            key = (rule.id, candidate.customer_id, frozenset(t.id for t in candidate.transactions))
            alert = existing.get(key)
            is_new = alert is None
            if is_new:
                alert = Alert(
                    pattern_code=template.pattern_code,
                    rule_version_id=version.id,
                    status=AlertStatus.OPEN,
                    correlation_id=uuid.uuid4(),
                    customer_id=candidate.customer_id,
                    reason=candidate.reason,
                    detection_details=candidate_details(template, candidate, run_id, params.currency),
                )
                db.add(alert)
                db.flush()
                db.add_all(AlertTransaction(alert_id=alert.id, transaction_id=t.id) for t in candidate.transactions)
                record_transition(
                    db, correlation_id=alert.correlation_id, actor=actor, object_type=OBJECT_ALERT,
                    object_id=alert.id, from_state=None, to_state=AlertStatus.OPEN.value, reason=alert.reason,
                )
                existing[key] = alert
                created += 1
            detected.append(
                DetectedAlert(
                    alert_id=alert.id, correlation_id=alert.correlation_id, customer_ref=candidate.customer_ref,
                    rule_ref=rule.rule_ref, rule_version=version.version,
                    window_start=candidate.window_start.astimezone(timezone.utc),
                    window_end=candidate.window_end.astimezone(timezone.utc),
                    txn_count_in_band=len(candidate.transactions),
                    aggregate_amount=sum((t.amount for t in candidate.transactions), Decimal(0)),
                    created=is_new,
                )
            )

    scanned = "; ".join(scans) if scans else "no ACTIVE rule version, nothing evaluated"
    record_transition(
        db, correlation_id=run_id, actor=actor, object_type=OBJECT_DETECTION_RUN, object_id=run_id,
        from_state=None, to_state="COMPLETED",
        reason=f"{template.pattern_code}: {scanned}; {created} alert(s) created, {len(detected) - created} already existing",
    )
    if commit:
        db.commit()
    return DetectionRunSummary(
        pattern_code=template.pattern_code, detection_run_id=run_id,
        rules_evaluated=[EvaluatedRule(rule_ref=r.rule_ref, version=v.version) for r, v in versions],
        alerts_created=created, alerts_already_existing=len(detected) - created, alerts=detected,
    )
