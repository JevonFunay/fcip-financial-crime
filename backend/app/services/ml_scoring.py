"""The trained models, inside the app (ML stage 4; FR-401, FR-405, FR-406; CF-05).

A detection run scores what the database holds with every ACTIVE model
version. It rebuilds the feature library's `Dataset` from the database and
calls the very functions training used (`build_matrices`, the same feature
code, the stored MCC reference), so a unit scores in the app exactly as it
scored offline. Which rows exist is decided by ingestion's validation, which
the offline loader also applies.

Stored per run (insert-only, BR-405.1): every unit at or above its model
version's threshold, and every AML week of an entity that has a rule alert,
so the alert can show the model's view of it as a supporting factor (CF-05
option a, BR-401.2). Each score keeps its strongest named features (BR-401.3).

A score raises an alert of its own only through an ACTIVE `ML_SCORE` rule
version (CF-05 option b, BR-401.2). That rule is seeded DRAFT (migration
0008), so until it is activated nothing here writes an alert.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.ml.feature_set import FEATURE_SET_VERSION, FEATURES, JAKARTA
from app.ml.features import build_matrices
from app.ml.loader import Account as MlAccount
from app.ml.loader import Dataset, Merchant as MlMerchant, Txn, _hours
from app.ml.models import canonical, definition_hash
from app.ml.reference import REFERENCE_PATH
from app.models.account import Account
from app.models.alert import Alert, AlertTransaction
from app.models.customer import Customer
from app.models.device import Device
from app.models.enums import AlertStatus, EntityType, RuleState, TransactionDirection
from app.models.merchant import Merchant
from app.models.ml import UNIT_AML, UNIT_FRAUD, MlScore, ModelVersion
from app.models.transaction import Transaction
from app.models.user import User
from app.services.audit import OBJECT_ALERT, OBJECT_DETECTION_RUN, record_transition
from app.services.detection.active_rules import active_rule_versions

ML_PATTERN_CODE = "ML_SCORE"
# FR-406, verbatim in meaning: shown with every model signal, never dismissible (BR-406.1).
SIGNAL_NOTICE = (
    "Sinyal ini merupakan observasi yang mendukung investigasi dan bukan bukti adanya perbuatan salah. "
    "This signal is an observation that supports an investigation; it is not evidence of wrongdoing."
)
# TRD §10.6 / FRD L-01: every metric and score surface carries it as a field.
SYNTHETIC_NOTICE = (
    "Synthetic scenarios and/or public-source observations are used for demonstration; model scores "
    "are not proof of fraud or production detection performance (TRD §10.6, FRD L-01)."
)
TOP_CONTRIBUTIONS = 8
UNIT_FEATURES = {UNIT_AML: "AML", UNIT_FRAUD: "FRAUD"}
FEATURE_DEFINITIONS = {(f.unit, f.code): f.definition for f in FEATURES}
UNIT_LABEL = {UNIT_AML: "weekly entity model", UNIT_FRAUD: "transaction model"}


class ModelIntegrityError(Exception):
    """A stored model version does not match its definition hash, or was built
    for another feature set or MCC reference than this checkout has."""


@dataclass
class LoadedModel:
    version: ModelVersion
    booster: object
    features: list[str]


def _reference_hash() -> str:
    import json

    return json.loads(REFERENCE_PATH.read_text())["definition_hash"]


def verify(version: ModelVersion) -> list[str]:
    try:
        return _verify(version)
    except ModelIntegrityError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ModelIntegrityError(f"{version.version}: malformed model artefact or card: {exc}") from exc


def _verify(version: ModelVersion) -> list[str]:
    """The definition hash recomputed from what is stored, and the feature set
    and MCC reference it needs, compared with this checkout's."""
    card = version.model_card
    features = card["feature_set"]["features"]
    if version.artefact_type == "ISOLATION_FOREST_JSON":
        from app.ml.behavioral.features import FEATURE_SET_VERSION as BEHAVIORAL_VERSION
        from app.ml.behavioral.features import FEATURE_DEFINITIONS as DEFINITIONS, feature_names
        from app.ml.behavioral.model import EXPLANATION_METHOD, load_model

        context = card["feature_set"]
        vocabulary = context["vocabulary"]
        expected = definition_hash(version.model_text, canonical(features), version.feature_set_version,
                                   canonical(vocabulary), canonical(version.threshold))
        if expected != version.definition_hash or card.get("definition_hash") != version.definition_hash:
            raise ModelIntegrityError(f"{version.version}: stored content does not match its definition_hash")
        if (version.feature_set_version != BEHAVIORAL_VERSION or context.get("version") != BEHAVIORAL_VERSION
                or features != feature_names(version.unit)
                or context.get("normalization_profile") != "fcip_canonical_v1"
                or vocabulary.get("feature_set_version") != BEHAVIORAL_VERSION):
            raise ModelIntegrityError(f"{version.version}: incompatible behavioral feature registry or profile")
        if (card.get("version") != version.version
                or card.get("artefact_type") != version.artefact_type
                or (card.get("unit") or card.get("model", {}).get("unit")) != version.unit
                or card["model"].get("threshold") != version.threshold
                or card["model"].get("explanation_method") != EXPLANATION_METHOD
                or card["model"].get("score_kind") != "anomaly_score"
                or not 0 <= version.threshold <= 1):
            raise ModelIntegrityError(f"{version.version}: inconsistent model-card semantics")
        definitions = context.get("definitions", {})
        if any(definitions.get(f) != DEFINITIONS[(version.unit, f)] for f in features):
            raise ModelIntegrityError(f"{version.version}: incompatible stored feature definitions")
        try:
            predictor = load_model(version.model_text)
        except (ValueError, TypeError, KeyError) as exc:
            raise ModelIntegrityError(f"{version.version}: invalid JSON model: {exc}") from exc
        if predictor.features != features:
            raise ModelIntegrityError(f"{version.version}: model feature order differs from its card")
        return features
    if version.artefact_type != "ANOMALY_MODEL":
        raise ModelIntegrityError(f"{version.version}: unsupported artefact_type {version.artefact_type}")
    reference = card["feature_set"]["mcc_ticket_reference"]
    recomputed = definition_hash(version.model_text, canonical(features), version.feature_set_version, reference,
                                 canonical(version.threshold))
    if recomputed != version.definition_hash:
        raise ModelIntegrityError(f"{version.version}: stored content does not match its definition_hash")
    if version.feature_set_version != FEATURE_SET_VERSION:
        raise ModelIntegrityError(f"{version.version} was trained on {version.feature_set_version}, "
                                  f"this checkout computes {FEATURE_SET_VERSION}")
    if reference != _reference_hash():
        raise ModelIntegrityError(f"{version.version} was trained against another MCC ticket reference")
    return features


def active_models(db: Session) -> dict[str, LoadedModel]:
    loaded = {}
    for version in db.scalars(select(ModelVersion).where(ModelVersion.state == RuleState.ACTIVE)):
        features = verify(version)
        if version.unit in loaded:
            raise ModelIntegrityError(f"More than one model version is ACTIVE for {version.unit}")
        if version.artefact_type == "ISOLATION_FOREST_JSON":
            from app.ml.behavioral.model import load_model
            predictor = load_model(version.model_text)
        else:
            import lightgbm as lgb
            predictor = lgb.Booster(model_str=version.model_text)
        loaded[version.unit] = LoadedModel(version, predictor, features)
    return loaded


# --- the database as the feature library's Dataset -------------------------------------------


def dataset_from_db(db: Session) -> tuple[Dataset, dict[str, uuid.UUID], dict[str, uuid.UUID]]:
    """Everything the features read, as the offline loader would have read it.
    Only IDR rows: the models know no other currency (the bridge drops the
    rest before ingestion, as the offline loader does)."""
    accounts, customer_ids = {}, {}
    for number, ref, customer_id, entity_type, opened in db.execute(
        select(Account.account_number, Customer.customer_ref, Customer.id, Customer.entity_type, Account.opened_date)
        .join(Customer, Account.customer_id == Customer.id)
    ):
        opened_ts = (datetime.combine(opened, datetime.min.time(), JAKARTA).timestamp()
                     if opened is not None else math.nan)
        accounts[number] = MlAccount(ref, entity_type is EntityType.BUSINESS, opened_ts)
        customer_ids[ref] = customer_id
    merchants = {
        ref: MlMerchant(owner, mcc, _hours(hours or ""))
        for ref, owner, mcc, hours in db.execute(
            select(Merchant.merchant_ref, Customer.customer_ref, Merchant.mcc, Merchant.operating_hours)
            .join(Customer, Merchant.customer_id == Customer.id)
        )
    }
    devices = {ref: (emulator, rooted) for ref, emulator, rooted in
               db.execute(select(Device.device_ref, Device.is_emulator, Device.is_rooted))}
    transactions, transaction_ids = [], {}
    for row in db.execute(
        select(Transaction.id, Transaction.transaction_ref, Account.account_number, Transaction.transaction_date,
               Transaction.amount, Transaction.direction, Transaction.channel, Transaction.transaction_type,
               Transaction.counterparty_ref, Transaction.counterparty_country, Transaction.merchant_ref,
               Transaction.device_ref, Transaction.source_status)
        .join(Account, Transaction.account_id == Account.id)
        .where(Transaction.currency == "IDR")
    ):
        account = accounts[row.account_number]
        transaction_ids[row.transaction_ref] = row.id
        transactions.append(Txn(
            ref=row.transaction_ref, account=row.account_number, entity=account.entity,
            ts=row.transaction_date.timestamp(), amount=float(row.amount),
            out=row.direction is TransactionDirection.DEBIT, channel=row.channel,
            txn_type=row.transaction_type or "", counterparty=row.counterparty_ref or "",
            counterparty_country=row.counterparty_country or "", merchant=row.merchant_ref or "",
            device=row.device_ref or "", reversed=row.source_status == "REVERSED",
        ))
    return Dataset(transactions, accounts, merchants, devices), customer_ids, transaction_ids


# --- scoring ---------------------------------------------------------------------------------


@dataclass
class UnitRun:
    model_version: str
    unit: str
    threshold: float
    units_scored: int
    units_flagged: int
    scores_stored: int
    observations_skipped_currency: int = 0


def _top(features: list[str], values: np.ndarray, contributions: np.ndarray) -> list[dict]:
    order = np.argsort(-np.abs(contributions))[:TOP_CONTRIBUTIONS]
    return [{"feature": features[i],
             "value": None if not np.isfinite(values[i]) else float(values[i]),
             "contribution": round(float(contributions[i]), 4)} for i in order]


def _entities_with_rule_alerts(db: Session) -> set[uuid.UUID]:
    return set(db.scalars(select(Alert.customer_id).where(Alert.pattern_code != ML_PATTERN_CODE)))


def _score_legacy(db: Session, run_id: uuid.UUID, models: dict[str, LoadedModel]) -> list[UnitRun]:
    dataset, customer_ids, transaction_ids = dataset_from_db(db)
    if not dataset.transactions:
        return [UnitRun(m.version.version, unit, m.version.threshold, 0, 0, 0) for unit, m in models.items()]
    aml, fraud = build_matrices(dataset)
    alerted = _entities_with_rule_alerts(db)
    runs = []
    for unit, model in sorted(models.items()):
        frame = aml if unit == UNIT_AML else fraud
        X = frame[model.features]
        scores = model.booster.predict(X)
        threshold = model.version.threshold
        flagged = scores >= threshold
        keep = flagged.copy()
        if unit == UNIT_AML:
            keep |= frame.entity_id.map(lambda ref: customer_ids.get(ref) in alerted).to_numpy(dtype=bool)
        rows = np.flatnonzero(keep)
        contributions = model.booster.predict(X.iloc[rows], pred_contrib=True) if len(rows) else np.zeros((0, 1))
        values = X.iloc[rows].to_numpy(dtype=float)
        records = []
        for k, i in enumerate(rows):
            as_of = datetime.fromtimestamp(float(frame.as_of.iat[i] if unit == UNIT_AML else frame.ts.iat[i]),
                                           timezone.utc)
            records.append({
                "model_version_id": model.version.id,
                "unit": unit,
                "customer_id": customer_ids[frame.entity_id.iat[i]],
                "transaction_id": transaction_ids[frame.transaction_ref.iat[i]] if unit == UNIT_FRAUD else None,
                "as_of": as_of,
                "score": float(scores[i]),
                "threshold": threshold,
                "flagged": bool(flagged[i]),
                "contributions": _top(model.features, values[k], contributions[k][:-1]),
                "detection_run_id": run_id,
            })
        stored = 0
        if records:
            # A unit already scored by this version keeps its first score (BR-405.1).
            for start in range(0, len(records), 2000):
                result = db.execute(pg_insert(MlScore.__table__).on_conflict_do_nothing()
                                    .returning(MlScore.__table__.c.id), records[start:start + 2000])
                stored += len(result.scalars().all())
        runs.append(UnitRun(model.version.version, unit, threshold, len(frame), int(flagged.sum()), stored))
    return runs


def behavioral_frame_from_db(db: Session, currency: str):
    """Canonical observed fields, never customer attributes inferred from IDs.

    The UTC timestamp is a storage carrier; its separate precision/timezone
    flags determine which temporal features can honestly be computed.
    """
    import pandas as pd
    from app.ml.behavioral.standardize import CANONICAL_COLUMNS

    fields = [c for c in CANONICAL_COLUMNS if c != "account_number"]
    rows = db.execute(select(
        *[getattr(Transaction, name) for name in fields],
        Transaction.id.label("transaction_id"), Account.account_number, Account.customer_id,
    ).join(Account, Transaction.account_id == Account.id).where(Transaction.currency == currency)).mappings()
    observations, customer_ids, transaction_ids = [], {}, {}
    for row in rows:
        record = {name: row[name] for name in fields}
        record.update(account_number=row["account_number"], amount=float(row["amount"]),
                      direction=row["direction"].value,
                      timestamp_precision=row["timestamp_precision"] or "day",
                      source_timezone_known=row["source_timezone_known"] is True)
        observations.append(record)
        customer_ids[row["account_number"]] = row["customer_id"]
        transaction_ids[row["transaction_ref"]] = row["transaction_id"]
    return pd.DataFrame(observations, columns=CANONICAL_COLUMNS), customer_ids, transaction_ids


def score_all(db: Session, run_id: uuid.UUID, models: dict[str, LoadedModel]) -> list[UnitRun]:
    from app.ml.behavioral.features import build_features
    from app.ml.behavioral.model import explain_model

    legacy = {unit: model for unit, model in models.items()
              if model.version.artefact_type != "ISOLATION_FOREST_JSON"}
    runs = _score_legacy(db, run_id, legacy) if legacy else []
    frames = {}
    alerted = _entities_with_rule_alerts(db)
    for unit, model in sorted(models.items()):
        if model.version.artefact_type != "ISOLATION_FOREST_JSON":
            continue
        vocabulary = model.version.model_card["feature_set"]["vocabulary"]
        currency = vocabulary["currency"]
        if currency not in frames:
            frames[currency] = behavioral_frame_from_db(db, currency)
        source, customer_ids, transaction_ids = frames[currency]
        skipped = db.query(Transaction).filter(Transaction.currency != currency).count()
        if source.empty:
            runs.append(UnitRun(model.version.version, unit, model.version.threshold, 0, 0, 0, skipped))
            continue
        if unit == UNIT_AML and len(set(customer_ids.values())) != len(customer_ids):
            raise ModelIntegrityError("behavioral_v1 account-week storage requires one observation proxy per account")
        frame = build_features(source, unit, vocabulary)
        scores = model.booster.scores(frame)
        flagged = scores >= model.version.threshold
        keep = flagged.copy()
        if unit == UNIT_AML:
            keep |= frame.account_number.map(lambda account: customer_ids[account] in alerted).to_numpy(dtype=bool)
        positions = np.flatnonzero(keep)
        explanations = explain_model(model.version.model_text, frame.iloc[positions], TOP_CONTRIBUTIONS)
        records = []
        for i, contributions in zip(positions, explanations):
            records.append({
                "model_version_id": model.version.id, "unit": unit,
                "customer_id": customer_ids[frame.account_number.iat[i]],
                "transaction_id": transaction_ids[frame.transaction_ref.iat[i]] if unit == UNIT_FRAUD else None,
                "as_of": frame.as_of.iat[i].to_pydatetime(), "score": float(scores[i]),
                "threshold": model.version.threshold, "flagged": bool(flagged[i]),
                "contributions": contributions, "detection_run_id": run_id,
            })
        stored = 0
        for start in range(0, len(records), 2000):
            result = db.execute(pg_insert(MlScore.__table__).on_conflict_do_nothing()
                                .returning(MlScore.__table__.c.id), records[start:start + 2000])
            stored += len(result.scalars().all())
        runs.append(UnitRun(model.version.version, unit, model.version.threshold,
                            len(frame), int(flagged.sum()), stored, skipped))
    return sorted(runs, key=lambda run: run.unit)


# --- ML_SCORE alerts (CF-05 option b), only through an ACTIVE rule version -----------------------


def _describe(unit: str, contributions: list[dict], n: int = 3, definitions: dict | None = None) -> str:
    parts = []
    for c in contributions[:n]:
        definition = (definitions.get(c["feature"], c["feature"]) if definitions is not None else
                      FEATURE_DEFINITIONS.get((UNIT_FEATURES[unit], c["feature"]), c["feature"]))
        value = "unknown" if c["value"] is None else f"{c['value']:,.4g}"
        parts.append(f"{c['feature']} = {value} ({definition.lower()})")
    return "; ".join(parts)


def raise_ml_alerts(db: Session, run_id: uuid.UUID, actor: User | None) -> tuple[list[str], int, int]:
    """One alert per flagged entity-week (AML) and per entity and local day of
    flagged transactions (Fraud), for every ACTIVE ML_SCORE rule version.
    Returns (rule versions evaluated, created, already existing)."""
    versions = active_rule_versions(db, ML_PATTERN_CODE)
    if not versions:
        return [], 0, 0
    existing = {a.detection_details.get("ML_UNIT_KEY") for a in
                db.scalars(select(Alert).where(Alert.pattern_code == ML_PATTERN_CODE))}
    flagged = db.execute(
        select(MlScore, ModelVersion, Transaction.transaction_date)
        .join(ModelVersion, MlScore.model_version_id == ModelVersion.id)
        .outerjoin(Transaction, MlScore.transaction_id == Transaction.id)
        .where(MlScore.flagged.is_(True))
        .order_by(MlScore.as_of)
    ).all()
    groups: dict[tuple, list] = defaultdict(list)
    model_cards = {}
    for score, version, _ in flagged:
        model_version = version.version
        model_cards[model_version] = version
        if score.unit == UNIT_AML:
            key = (model_version, score.unit, score.customer_id, score.as_of.isoformat())
        else:
            day = (score.as_of.date() if version.artefact_type == "ISOLATION_FOREST_JSON"
                   else score.as_of.astimezone(JAKARTA).date())
            key = (model_version, score.unit, score.customer_id, day.isoformat())
        groups[key].append(score)
    created = already = 0
    evaluated = [f"{rule.rule_ref} v{version.version}" for rule, version in versions]
    for rule, version in versions:
        for (model_version, unit, customer_id, period), scores in groups.items():
            unit_key = f"{rule.rule_ref}:{model_version}:{customer_id}:{period}"
            if unit_key in existing:
                already += 1
                continue
            best = max(scores, key=lambda s: s.score)
            if unit == UNIT_AML:
                window_end = best.as_of
                window_start = window_end - timedelta(days=7) + timedelta(seconds=1)
                evidence = db.scalars(
                    select(Transaction.id).join(Account, Transaction.account_id == Account.id)
                    .where(Account.customer_id == customer_id, Transaction.transaction_date >= window_start,
                           Transaction.transaction_date <= window_end)).all()
                what = f"the week ending {window_end.astimezone(JAKARTA).date()}"
            else:
                evidence = [s.transaction_id for s in scores]
                window_start = min(s.as_of for s in scores)
                window_end = max(s.as_of for s in scores)
                what = f"{len(scores)} transaction(s) on {period}"
            model = model_cards[model_version]
            definitions = (model.model_card["feature_set"]["definitions"]
                           if model.artefact_type == "ISOLATION_FOREST_JSON" else None)
            reason = (f"{UNIT_LABEL[unit].capitalize()} {model_version} scored {what} at {best.score:.2f} "
                      f"(threshold {best.threshold:.2f}). Strongest features: "
                      f"{_describe(unit, best.contributions, definitions=definitions)}")
            alert = Alert(
                pattern_code=ML_PATTERN_CODE,
                rule_version_id=version.id,
                status=AlertStatus.OPEN,
                correlation_id=uuid.uuid4(),
                customer_id=customer_id,
                reason=reason,
                detection_details={
                    "ML_UNIT_KEY": unit_key,
                    "MODEL_VERSION": model_version,
                    "UNIT": unit,
                    "SCORE": round(best.score, 4),
                    "THRESHOLD": best.threshold,
                    "CONTRIBUTIONS": best.contributions,
                    "SCORE_IDS": [str(s.id) for s in scores],
                    "window_start": window_start.astimezone(timezone.utc).isoformat(),
                    "window_end": window_end.astimezone(timezone.utc).isoformat(),
                    "SIGNAL_NOTICE": SIGNAL_NOTICE,
                    "SYNTHETIC_NOTICE": SYNTHETIC_NOTICE,
                    "DETECTION_RUN_ID": str(run_id),
                },
            )
            db.add(alert)
            db.flush()
            db.add_all(AlertTransaction(alert_id=alert.id, transaction_id=t) for t in set(evidence))
            record_transition(db, correlation_id=alert.correlation_id, actor=actor, object_type=OBJECT_ALERT,
                              object_id=alert.id, from_state=None, to_state=AlertStatus.OPEN.value, reason=reason)
            existing.add(unit_key)
            created += 1
    return evaluated, created, already


@dataclass
class MlRun:
    detection_run_id: uuid.UUID
    models: list[UnitRun]
    ml_rule_versions_evaluated: list[str]
    alerts_created: int
    alerts_already_existing: int
    seconds: float


def run_ml_scoring(db: Session, *, actor: User | None = None) -> MlRun:
    import time

    started = time.perf_counter()
    run_id = uuid.uuid4()
    models = active_models(db)
    runs = score_all(db, run_id, models) if models else []
    evaluated, created, already = raise_ml_alerts(db, run_id, actor)
    seconds = round(time.perf_counter() - started, 1)
    summary = "; ".join(f"{r.model_version}: {r.units_scored} unit(s) scored, {r.units_flagged} at or above "
                        f"{r.threshold:.3f}, {r.scores_stored} score(s) stored" for r in runs) \
        or "no ACTIVE model version, nothing scored"
    alerts = (f"ML_SCORE rule {', '.join(evaluated)}: {created} alert(s) created, {already} already existing"
              if evaluated else "no ACTIVE ML_SCORE rule version, no alert raised from a score (CF-05)")
    # FR-401's audit event: the run is recorded even when it scores nothing.
    record_transition(
        db, correlation_id=run_id, actor=actor, object_type=OBJECT_DETECTION_RUN, object_id=run_id,
        from_state=None, to_state="COMPLETED", reason=f"ML scoring: {summary}; {alerts}",
        action="ANOMALY_SCORE_COMPUTED",
        details={"models": [r.__dict__ for r in runs], "ml_rule_versions": evaluated,
                 "alerts_created": created, "seconds": seconds},
    )
    db.commit()
    return MlRun(run_id, runs, evaluated, created, already, seconds)
