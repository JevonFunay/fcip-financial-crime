"""P02 Structuring detection (FRD §8.2): the pattern's template (TRD ADR-006).

Flags an entity whose transactions cluster just below the reporting threshold:
at least MIN_COUNT transactions with BAND_LOWER <= amount < REPORTING_THRESHOLD
inside a rolling window, with an aggregate of at least
AGGREGATE_MULTIPLE x REPORTING_THRESHOLD. All accounts and all channels of the
entity are aggregated together; direction (CREDIT/DEBIT) is not restricted.

The logic lives here; the parameters do not. They are the ACTIVE rule
version(s) of pattern P02_STRUCTURING in the database (seeded as RUL-0001 v1
by migration 0006), so a threshold changes by versioning the rule, not by
deploying code.

TEMPORARY SIMPLIFICATION: the FRD aggregates per *entity*. Entity resolution
isn't built yet, so customer_id stands in for entity_id — each customer is
treated as its own entity. Swap the grouping key once entity resolution exists.
"""

import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.alert import Alert, AlertTransaction
from app.models.customer import Customer
from app.models.enums import AlertStatus, RuleWindowType
from app.models.rule import RuleVersion
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.detection import DetectedAlert, DetectionRunSummary, EvaluatedRule
from app.services.audit import OBJECT_ALERT, OBJECT_DETECTION_RUN, record_transition
from app.services.detection.active_rules import active_rule_versions

PATTERN_CODE = "P02_STRUCTURING"
# Which version of this logic evaluated a rule version. Bump it whenever the
# evaluation changes, so (template_version, parameters) keeps reproducing an
# alert (TRD §7.3, SM-01).
TEMPLATE_VERSION = "p02_structuring_v1"
_CENTS = Decimal("0.01")


@dataclass(frozen=True)
class P02Parameters:
    """The typed form of one P02 rule version. No defaults on purpose: the
    values come from rule_version.parameters, never from code."""

    reporting_threshold: Decimal
    band_lower_ratio: Decimal
    min_count: int
    aggregate_multiple: Decimal
    window_days: int
    # Threshold is denominated in IDR and there is no FX conversion yet, so
    # transactions in other currencies are ignored.
    currency: str

    @classmethod
    def from_rule_version(cls, version: RuleVersion) -> "P02Parameters":
        # The template evaluates rolling windows of whole days; anything else
        # is refused rather than silently approximated.
        if version.window_type is not RuleWindowType.ROLLING or version.window_length % timedelta(days=1):
            raise ValueError(
                f"{TEMPLATE_VERSION} evaluates ROLLING windows of whole days, not "
                f"{version.window_type.value} {version.window_length}"
            )
        values = version.parameters
        return cls(
            reporting_threshold=Decimal(values["reporting_threshold"]),
            band_lower_ratio=Decimal(values["band_lower_ratio"]),
            min_count=int(values["min_count"]),
            aggregate_multiple=Decimal(values["aggregate_multiple"]),
            window_days=version.window_length.days,
            currency=values["currency"],
        )

    @property
    def band_lower(self) -> Decimal:
        return (self.reporting_threshold * self.band_lower_ratio).quantize(_CENTS)

    @property
    def aggregate_minimum(self) -> Decimal:
        return (self.reporting_threshold * self.aggregate_multiple).quantize(_CENTS)

    @property
    def window(self) -> timedelta:
        return timedelta(days=self.window_days)



class _DatedAmount(Protocol):
    transaction_date: datetime
    amount: Decimal


@dataclass(frozen=True)
class Cluster:
    transactions: tuple
    window_start: datetime
    window_end: datetime  # exclusive


def find_clusters(transactions: Sequence[_DatedAmount], params: P02Parameters) -> list[Cluster]:
    """Greedy, non-overlapping windows over one entity's in-band transactions (pre-sorted by date).

    A window opens at each transaction in turn and spans WINDOW_DAYS. When a
    window qualifies, every transaction in it becomes evidence for one alert and
    scanning resumes after the window, so a transaction never backs two alerts.
    """
    clusters: list[Cluster] = []
    i = 0
    while i < len(transactions):
        window_start = transactions[i].transaction_date
        window_end = window_start + params.window
        j = i
        while j < len(transactions) and transactions[j].transaction_date < window_end:
            j += 1
        candidate = transactions[i:j]
        aggregate = sum((t.amount for t in candidate), Decimal(0))
        if len(candidate) >= params.min_count and aggregate >= params.aggregate_minimum:
            clusters.append(Cluster(tuple(candidate), window_start, window_end))
            i = j
        else:
            i += 1
    return clusters


def build_detection_details(cluster: Cluster, params: P02Parameters, run_id: uuid.UUID) -> dict:
    """Alert factors required by FRD §8.2, plus the window bounds and parameters
    applied so the evidence can be reproduced later (FR-307)."""
    txns = cluster.transactions
    amounts = [t.amount for t in txns]
    count = len(amounts)
    aggregate = sum(amounts, Decimal(0))
    mean = aggregate / count
    variance = sum(((a - mean) ** 2 for a in amounts), Decimal(0)) / count
    dispersion_cv = float(variance.sqrt() / mean) if mean else 0.0

    return {
        "TXN_COUNT_IN_BAND": count,
        "AGGREGATE_AMOUNT": str(aggregate.quantize(_CENTS)),
        "THRESHOLD_APPLIED": str(params.reporting_threshold),
        "BAND_LOWER_APPLIED": str(params.band_lower),
        "AMOUNT_DISPERSION_CV": round(dispersion_cv, 4),
        "DISTINCT_ACCOUNTS": len({t.account_id for t in txns}),
        "DISTINCT_COUNTERPARTIES": len({t.counterparty_ref for t in txns if t.counterparty_ref}),
        "CHANNEL_MIX": dict(sorted(Counter(t.channel for t in txns).items())),
        # Stored in UTC so the bounds don't depend on the DB session timezone
        # of whichever machine ran the detection.
        "window_start": cluster.window_start.astimezone(timezone.utc).isoformat(),
        "window_end": cluster.window_end.astimezone(timezone.utc).isoformat(),
        "WINDOW_DAYS_APPLIED": params.window_days,
        "MIN_COUNT_APPLIED": params.min_count,
        "AGGREGATE_MULTIPLE_APPLIED": str(params.aggregate_multiple),
        "CURRENCY": params.currency,
        "TRANSACTION_REFS": sorted(t.transaction_ref for t in txns),
        "DETECTION_RUN_ID": str(run_id),
    }


def _reason(cluster: Cluster, params: P02Parameters, details: dict) -> str:
    first = cluster.transactions[0].transaction_date.astimezone(timezone.utc).date()
    last = cluster.transactions[-1].transaction_date.astimezone(timezone.utc).date()
    return (
        f"P02 Structuring: {details['TXN_COUNT_IN_BAND']} transactions between "
        f"{params.currency} {params.band_lower:,.0f} and {params.currency} {params.reporting_threshold:,.0f} "
        f"within a {params.window_days}-day window ({first} to {last}), "
        f"totalling {params.currency} {Decimal(details['AGGREGATE_AMOUNT']):,.0f} "
        f"across {details['DISTINCT_ACCOUNTS']} account(s)"
    )


@dataclass(frozen=True)
class Candidate:
    """One cluster the template would raise an alert for."""

    customer_id: uuid.UUID
    customer_ref: str
    cluster: Cluster


@dataclass(frozen=True)
class Evaluation:
    transactions_scanned: int
    entities_scanned: int
    candidates: list[Candidate]


def evaluate(
    db: Session, params: P02Parameters, *, period: tuple[datetime, datetime] | None = None
) -> Evaluation:
    """What the template finds with these parameters. Read-only: nothing is
    written. A detection run and a simulation (TRD §9.5) both call this, so a
    simulation evaluates exactly what a run would. `period` is [start, end)."""
    statement = (
        select(Transaction, Account.customer_id, Customer.customer_ref)
        .join(Account, Transaction.account_id == Account.id)
        .join(Customer, Account.customer_id == Customer.id)
        .where(
            Transaction.currency == params.currency,
            Transaction.amount >= params.band_lower,
            Transaction.amount < params.reporting_threshold,
        )
        # Deterministic order (date, then ref as tie-breaker) keeps re-runs reproducible.
        .order_by(Account.customer_id, Transaction.transaction_date, Transaction.transaction_ref)
    )
    if period is not None:
        statement = statement.where(Transaction.transaction_date >= period[0], Transaction.transaction_date < period[1])
    rows = db.execute(statement).all()

    by_customer: dict[uuid.UUID, list[Transaction]] = defaultdict(list)
    customer_refs: dict[uuid.UUID, str] = {}
    for transaction, customer_id, customer_ref in rows:
        by_customer[customer_id].append(transaction)
        customer_refs[customer_id] = customer_ref

    candidates = [
        Candidate(customer_id, customer_refs[customer_id], cluster)
        for customer_id, transactions in by_customer.items()
        for cluster in find_clusters(transactions, params)
    ]
    return Evaluation(transactions_scanned=len(rows), entities_scanned=len(by_customer), candidates=candidates)


def _existing_evidence_sets(db: Session) -> dict[tuple[uuid.UUID, uuid.UUID, frozenset[uuid.UUID]], Alert]:
    """Existing P02 alerts keyed by (rule, customer, exact evidence set), so a
    re-run on the same data recognises alerts it has already raised. Keyed by
    rule rather than version: a new version of the same rule that finds the
    same evidence is the same finding, not a new one."""
    rows = db.execute(
        select(Alert, AlertTransaction.transaction_id, RuleVersion.rule_id)
        .join(AlertTransaction, AlertTransaction.alert_id == Alert.id)
        .join(RuleVersion, Alert.rule_version_id == RuleVersion.id)
        .where(Alert.pattern_code == PATTERN_CODE)
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


def _create_alert(
    db: Session,
    *,
    customer_id: uuid.UUID,
    cluster: Cluster,
    version: RuleVersion,
    params: P02Parameters,
    run_id: uuid.UUID,
    actor: User | None,
) -> Alert:
    details = build_detection_details(cluster, params, run_id)
    alert = Alert(
        pattern_code=PATTERN_CODE,
        rule_version_id=version.id,
        status=AlertStatus.OPEN,
        correlation_id=uuid.uuid4(),
        customer_id=customer_id,
        reason=_reason(cluster, params, details),
        detection_details=details,
    )
    db.add(alert)
    db.flush()
    db.add_all(AlertTransaction(alert_id=alert.id, transaction_id=t.id) for t in cluster.transactions)
    record_transition(
        db,
        correlation_id=alert.correlation_id,
        actor=actor,
        object_type=OBJECT_ALERT,
        object_id=alert.id,
        from_state=None,
        to_state=AlertStatus.OPEN.value,
        reason=alert.reason,
    )
    return alert


def run_p02_structuring(db: Session, *, actor: User | None = None) -> DetectionRunSummary:
    """Evaluate every ACTIVE P02 rule version and raise the alerts it finds."""
    run_id = uuid.uuid4()
    versions = active_rule_versions(db, PATTERN_CODE)
    existing = _existing_evidence_sets(db)
    detected: list[DetectedAlert] = []
    scans: list[str] = []
    created = 0
    for rule, version in versions:
        params = P02Parameters.from_rule_version(version)
        evaluation = evaluate(db, params)
        scans.append(
            f"{rule.rule_ref} v{version.version} scanned {evaluation.transactions_scanned} in-band "
            f"transaction(s) across {evaluation.entities_scanned} entity/entities"
        )
        for candidate in evaluation.candidates:
            cluster = candidate.cluster
            key = (rule.id, candidate.customer_id, frozenset(t.id for t in cluster.transactions))
            alert = existing.get(key)
            is_new = alert is None
            if is_new:
                alert = _create_alert(
                    db,
                    customer_id=candidate.customer_id,
                    cluster=cluster,
                    version=version,
                    params=params,
                    run_id=run_id,
                    actor=actor,
                )
                created += 1
            detected.append(
                DetectedAlert(
                    alert_id=alert.id,
                    correlation_id=alert.correlation_id,
                    customer_ref=candidate.customer_ref,
                    rule_ref=rule.rule_ref,
                    rule_version=version.version,
                    window_start=cluster.window_start.astimezone(timezone.utc),
                    window_end=cluster.window_end.astimezone(timezone.utc),
                    txn_count_in_band=len(cluster.transactions),
                    aggregate_amount=Decimal(alert.detection_details["AGGREGATE_AMOUNT"]),
                    created=is_new,
                )
            )

    # FR-1104: the run itself is a material action, so it is audited even when
    # it raises nothing — "no alerts" has to be distinguishable from "never ran",
    # and "no live rule" from both.
    scanned = "; ".join(scans) if scans else "no ACTIVE rule version, nothing evaluated"
    record_transition(
        db,
        correlation_id=run_id,
        actor=actor,
        object_type=OBJECT_DETECTION_RUN,
        object_id=run_id,
        from_state=None,
        to_state="COMPLETED",
        reason=(
            f"{PATTERN_CODE}: {scanned}; "
            f"{created} alert(s) created, {len(detected) - created} already existing"
        ),
    )
    db.commit()

    return DetectionRunSummary(
        pattern_code=PATTERN_CODE,
        detection_run_id=run_id,
        rules_evaluated=[EvaluatedRule(rule_ref=rule.rule_ref, version=version.version) for rule, version in versions],
        alerts_created=created,
        alerts_already_existing=len(detected) - created,
        alerts=detected,
    )
