"""P02 Structuring detection (FRD §8.2).

Flags an entity whose transactions cluster just below the reporting threshold:
at least MIN_COUNT transactions with BAND_LOWER <= amount < REPORTING_THRESHOLD
inside a rolling WINDOW_DAYS window, with an aggregate of at least
AGGREGATE_MULTIPLE x REPORTING_THRESHOLD. All accounts and all channels of the
entity are aggregated together; direction (CREDIT/DEBIT) is not restricted.

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
from app.models.enums import AlertStatus
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.detection import DetectedAlert, DetectionRunSummary
from app.services.audit import OBJECT_ALERT, record_transition

PATTERN_CODE = "P02_STRUCTURING"
_CENTS = Decimal("0.01")


@dataclass(frozen=True)
class P02Parameters:
    reporting_threshold: Decimal = Decimal("500000000.00")
    band_lower_ratio: Decimal = Decimal("0.70")
    min_count: int = 3
    aggregate_multiple: Decimal = Decimal("1.0")
    window_days: int = 7
    # Threshold is denominated in IDR and there is no FX conversion yet, so
    # transactions in other currencies are ignored.
    currency: str = "IDR"

    @property
    def band_lower(self) -> Decimal:
        return (self.reporting_threshold * self.band_lower_ratio).quantize(_CENTS)

    @property
    def aggregate_minimum(self) -> Decimal:
        return (self.reporting_threshold * self.aggregate_multiple).quantize(_CENTS)

    @property
    def window(self) -> timedelta:
        return timedelta(days=self.window_days)


DEFAULT_PARAMETERS = P02Parameters()


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


def _existing_evidence_sets(db: Session) -> dict[tuple[uuid.UUID, frozenset[uuid.UUID]], Alert]:
    """Existing P02 alerts keyed by (customer, exact evidence set), so a re-run
    on the same data recognises alerts it has already raised."""
    rows = db.execute(
        select(Alert, AlertTransaction.transaction_id)
        .join(AlertTransaction, AlertTransaction.alert_id == Alert.id)
        .where(Alert.pattern_code == PATTERN_CODE)
    ).all()
    evidence: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    alerts: dict[uuid.UUID, Alert] = {}
    for alert, transaction_id in rows:
        alerts[alert.id] = alert
        evidence[alert.id].add(transaction_id)
    return {(alerts[alert_id].customer_id, frozenset(ids)): alerts[alert_id] for alert_id, ids in evidence.items()}


def _create_alert(
    db: Session,
    *,
    customer_id: uuid.UUID,
    cluster: Cluster,
    params: P02Parameters,
    run_id: uuid.UUID,
    actor: User | None,
) -> Alert:
    details = build_detection_details(cluster, params, run_id)
    alert = Alert(
        pattern_code=PATTERN_CODE,
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


def run_p02_structuring(
    db: Session, *, actor: User | None = None, params: P02Parameters = DEFAULT_PARAMETERS
) -> DetectionRunSummary:
    run_id = uuid.uuid4()
    rows = db.execute(
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
    ).all()

    by_customer: dict[uuid.UUID, list[Transaction]] = defaultdict(list)
    customer_refs: dict[uuid.UUID, str] = {}
    for transaction, customer_id, customer_ref in rows:
        by_customer[customer_id].append(transaction)
        customer_refs[customer_id] = customer_ref

    existing = _existing_evidence_sets(db)
    detected: list[DetectedAlert] = []
    created = 0
    for customer_id, transactions in by_customer.items():
        for cluster in find_clusters(transactions, params):
            key = (customer_id, frozenset(t.id for t in cluster.transactions))
            alert = existing.get(key)
            is_new = alert is None
            if is_new:
                alert = _create_alert(
                    db, customer_id=customer_id, cluster=cluster, params=params, run_id=run_id, actor=actor
                )
                created += 1
            detected.append(
                DetectedAlert(
                    alert_id=alert.id,
                    correlation_id=alert.correlation_id,
                    customer_ref=customer_refs[customer_id],
                    window_start=cluster.window_start.astimezone(timezone.utc),
                    window_end=cluster.window_end.astimezone(timezone.utc),
                    txn_count_in_band=len(cluster.transactions),
                    aggregate_amount=Decimal(alert.detection_details["AGGREGATE_AMOUNT"]),
                    created=is_new,
                )
            )
    db.commit()

    return DetectionRunSummary(
        pattern_code=PATTERN_CODE,
        detection_run_id=run_id,
        alerts_created=created,
        alerts_already_existing=len(detected) - created,
        alerts=detected,
    )
