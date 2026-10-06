"""Reading stored model scores for the API: lists, and an alert's model signals."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.customer import Customer
from app.models.ml import UNIT_AML, UNIT_FRAUD, MlScore, ModelVersion
from app.models.transaction import Transaction
from app.schemas.ml import Contribution, ScoreOut
from app.services.ml_scoring import FEATURE_DEFINITIONS, UNIT_FEATURES


def _base() -> Select:
    return (
        select(MlScore, ModelVersion.version, Customer, Transaction.transaction_ref, Transaction.amount)
        .join(ModelVersion, MlScore.model_version_id == ModelVersion.id)
        .join(Customer, MlScore.customer_id == Customer.id)
        .outerjoin(Transaction, MlScore.transaction_id == Transaction.id)
    )


def to_out(row) -> ScoreOut:
    score, version, customer, transaction_ref, amount = row
    return ScoreOut(
        id=score.id, model_version=version, unit=score.unit, customer_id=customer.id,
        customer_ref=customer.customer_ref, customer_name=customer.full_name, as_of=score.as_of,
        transaction_ref=transaction_ref, transaction_amount=float(amount) if amount is not None else None,
        score=score.score, threshold=score.threshold, flagged=score.flagged,
        contributions=[Contribution(feature=c["feature"], value=c["value"], contribution=c["contribution"],
                                    definition=FEATURE_DEFINITIONS.get((UNIT_FEATURES[score.unit], c["feature"]), ""))
                       for c in score.contributions],
    )


def list_scores(db: Session, *, unit: str | None, flagged_only: bool, search: str | None,
                limit: int, offset: int) -> tuple[list[ScoreOut], int]:
    conditions = []
    if unit is not None:
        conditions.append(MlScore.unit == unit)
    if flagged_only:
        conditions.append(MlScore.flagged.is_(True))
    if search:
        pattern = f"%{search}%"
        conditions.append(Customer.customer_ref.ilike(pattern) | Customer.full_name.ilike(pattern))
    total = db.scalar(select(func.count()).select_from(MlScore).join(Customer, MlScore.customer_id == Customer.id)
                      .where(*conditions)) or 0
    rows = db.execute(_base().where(*conditions).order_by(MlScore.score.desc(), MlScore.as_of, MlScore.id)
                      .limit(limit).offset(offset)).all()
    return [to_out(r) for r in rows], total


def _window(alert: Alert) -> tuple[datetime, datetime] | None:
    details = alert.detection_details or {}
    try:
        return datetime.fromisoformat(details["window_start"]), datetime.fromisoformat(details["window_end"])
    except (KeyError, ValueError):
        return None


def alert_signals(db: Session, alert: Alert) -> list[ScoreOut]:
    """The models' view of the alert's entity over the alert's window (CF-05
    option a): every stored weekly score whose week overlaps it, and the
    flagged transaction scores inside it."""
    window = _window(alert)
    if window is None:
        return []
    start, end = window
    rows = db.execute(
        _base().where(
            MlScore.customer_id == alert.customer_id,
            ((MlScore.unit == UNIT_AML) & (MlScore.as_of >= start) & (MlScore.as_of < end + timedelta(days=7)))
            | ((MlScore.unit == UNIT_FRAUD) & MlScore.flagged.is_(True) & (MlScore.as_of >= start)
               & (MlScore.as_of <= end)),
        ).order_by(MlScore.as_of, MlScore.unit)
    ).all()
    return [to_out(r) for r in rows]


def max_signal_by_alert(db: Session, alerts: list[Alert]) -> dict[uuid.UUID, float]:
    """The highest stored score per alert, over the same window, for the queue."""
    windows = {a.id: (a.customer_id, _window(a)) for a in alerts}
    customers = {c for c, w in windows.values() if w is not None}
    if not customers:
        return {}
    by_customer: dict[uuid.UUID, list[MlScore]] = {}
    for score in db.scalars(select(MlScore).where(MlScore.customer_id.in_(customers))):
        by_customer.setdefault(score.customer_id, []).append(score)
    out = {}
    for alert_id, (customer, window) in windows.items():
        if window is None:
            continue
        start, end = window
        relevant = [s.score for s in by_customer.get(customer, [])
                    if (s.unit == UNIT_AML and start <= s.as_of < end + timedelta(days=7))
                    or (s.unit == UNIT_FRAUD and s.flagged and start <= s.as_of <= end)]
        if relevant:
            out[alert_id] = max(relevant)
    return out
