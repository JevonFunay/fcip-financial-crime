"""ML stage 4: the trained models inside the app.

What must hold:
- the app scores exactly what the models scored offline, unit for unit
  (training/serving parity), from data that went through ingestion
- a model version is stored once, checked against its definition hash, and
  never edited; a stored score is never rewritten (FR-405, BR-405.1)
- a score raises no alert of its own while the ML_SCORE rule is DRAFT (CF-05,
  BR-401.2, FR-401 AC3); activated, it raises one alert per finding
- every surface that shows a score carries FR-406's notice and the synthetic
  data notice, and describes rather than accuses (BR-406.2)
"""

import json
import re
import shutil
from datetime import datetime

import lightgbm as lgb
import pytest
from sqlalchemy import select, text, update

from app.ml.build_features import build
from app.ml.feature_set import JAKARTA
from app.ml.train import prepare, train_model
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.device import Device
from app.models.enums import RuleState, UserRole
from app.models.merchant import Merchant
from app.models.ml import MlScore, ModelVersion
from app.models.rule import Rule, RuleVersion
from app.models.transaction import Transaction
from app.models.user import User
from app.scripts.load_raw_dataset import (
    ingest_projected,
    load_master_data,
    load_merchants_and_devices,
    project_transactions,
)
from app.scripts.register_models import RegistrationRefused, register
from app.services.ingestion import ingest_transactions_csv
from app.services.ml_scoring import SIGNAL_NOTICE, ModelIntegrityError, run_ml_scoring, verify
from app.services.detection.p02_structuring import run_p02_structuring

ACCUSING = re.compile(r"\b(suspicious|fraudulent|laundering|mencurigakan|penipuan|pencucian uang)\b", re.I)


@pytest.fixture(scope="module")
def trained(tmp_path_factory, legacy_small_raw):
    """Both test-only models and offline features use private generated data."""
    root = tmp_path_factory.mktemp("ml_app")
    folder = root / "Small"
    shutil.copytree(legacy_small_raw, folder)
    build(folder)
    frames, labels, truth, located, report = prepare(folder)
    for model in ("aml", "fraud"):
        train_model(model, folder, frames, labels, truth, located, report, root / "models")
    return {"folder": folder, "models": root / "models", "frames": frames}


@pytest.fixture()
def loaded(db, trained, tmp_path):
    """Small in the app the way the full profile goes in: master data, then a
    registered batch through the ingestion service, then the models registered."""
    db.add(User(email="dataops@fcip.internal", hashed_password="x", full_name="Data Ops", role=UserRole.ROLE_DATA_OPS))
    db.commit()
    folder = trained["folder"]
    load_master_data(db, folder)
    load_merchants_and_devices(db, folder)
    projected = tmp_path / "transactions_app_format.csv"
    project_transactions(folder, projected)
    manifest = json.loads((folder / "manifest.json").read_text())
    summary = ingest_projected(db, projected, manifest, "dataops@fcip.internal")
    for name in ("aml_gbm_v1", "fraud_gbm_v1"):
        register(db, trained["models"] / name)
    return summary


def _offline(trained, model: str):
    booster = lgb.Booster(model_file=str(trained["models"] / f"{model}_gbm_v1" / "model.txt"))
    card = json.loads((trained["models"] / f"{model}_gbm_v1" / "model_card.json").read_text())
    frame = trained["frames"][model]
    return frame, booster.predict(frame[card["feature_set"]["features"]]), card["model"]["threshold"]


# --- data in ----------------------------------------------------------------------------------


def test_ingestion_keeps_the_transaction_context(db):
    from app.scripts.seed import seed_master_data

    seed_master_data(db)
    content = (
        "transaction_ref,account_number,transaction_date,amount,currency,direction,channel,"
        "transaction_type,counterparty_country,merchant_ref,device_ref,source_status\n"
        "T-1,ACC-1001,2026-08-01T10:00:00+07:00,150000.00,IDR,DEBIT,QRIS,merchant_payment,sg,MER-1,DEV-1,reversed\n"
        "T-2,ACC-1001,2026-08-01T11:00:00+07:00,150000.00,IDR,DEBIT,QRIS,MERCHANT_PAYMENT,S1,MER-1,DEV-1,SETTLED\n"
    )
    summary = ingest_transactions_csv(db, file_name="ctx.csv", content=content)
    stored = db.scalar(select(Transaction).where(Transaction.transaction_ref == "T-1"))

    assert summary.accepted == 1 and summary.quarantined == 1
    assert "counterparty_country" in summary.quarantine_preview[0].error_reason
    assert (stored.transaction_type, stored.counterparty_country, stored.merchant_ref, stored.device_ref,
            stored.source_status) == ("MERCHANT_PAYMENT", "SG", "MER-1", "DEV-1", "REVERSED")


def test_the_bridge_loads_merchants_and_devices_and_carries_the_context(db, loaded):
    assert db.scalar(select(text("count(*)")).select_from(Merchant)) == 75
    assert db.scalar(select(text("count(*)")).select_from(Device)) == 599
    with_device = db.scalar(select(text("count(*)")).select_from(Transaction).where(Transaction.device_ref.is_not(None)))
    assert with_device > 0.9 * loaded.accepted
    assert loaded.accepted == 20_458  # exactly the rows the offline loader accepts


# --- parity, storage, alerts ----------------------------------------------------------------


def test_the_app_scores_exactly_what_the_models_scored_offline(db, trained, loaded):
    run = run_ml_scoring(db)
    assert {r.unit for r in run.models} == {"AML", "FRAUD"}

    fraud, fraud_scores, fraud_t = _offline(trained, "fraud")
    stored = {ref: s for s, ref in db.execute(
        select(MlScore, Transaction.transaction_ref).join(Transaction, MlScore.transaction_id == Transaction.id))}
    offline_flagged = set(fraud.transaction_ref[fraud_scores >= fraud_t])
    assert offline_flagged and {ref for ref, s in stored.items() if s.flagged} == offline_flagged
    by_ref = dict(zip(fraud.transaction_ref, fraud_scores))
    assert all(abs(s.score - by_ref[ref]) < 1e-9 for ref, s in stored.items())

    aml, aml_scores, aml_t = _offline(trained, "aml")
    weekly = {(ref, s.as_of.timestamp()): s for s, ref in db.execute(
        select(MlScore, Customer.customer_ref).join(Customer, MlScore.customer_id == Customer.id)
        .where(MlScore.transaction_id.is_(None)))}
    offline_weekly = {(e, a): s for e, a, s in zip(aml.entity_id, aml.as_of, aml_scores)}
    assert {k for k, s in weekly.items() if s.flagged} == {k for k, s in offline_weekly.items() if s >= aml_t}
    assert all(abs(s.score - offline_weekly[k]) < 1e-9 for k, s in weekly.items())

    # Every stored score names its strongest features, and adds nothing on a re-run.
    assert all(len(s.contributions) == 8 for s in stored.values())
    again = run_ml_scoring(db)
    assert sum(r.scores_stored for r in again.models) == 0


def test_a_draft_ml_score_rule_raises_nothing_and_an_active_one_raises_each_finding_once(db, loaded):
    run = run_ml_scoring(db)
    assert run.ml_rule_versions_evaluated == [] and run.alerts_created == 0
    assert db.scalar(select(text("count(*)")).select_from(Alert).where(Alert.pattern_code == "ML_SCORE")) == 0

    # What FR-304 will do once built: the DRAFT version becomes ACTIVE.
    version_id = db.scalar(select(RuleVersion.id).join(Rule).where(Rule.pattern_code == "ML_SCORE"))
    db.execute(update(RuleVersion).where(RuleVersion.id == version_id).values(state=RuleState.ACTIVE))
    db.commit()

    first = run_ml_scoring(db)
    assert first.ml_rule_versions_evaluated == ["RUL-0002 v1"] and first.alerts_created > 0
    alerts = db.scalars(select(Alert).where(Alert.pattern_code == "ML_SCORE")).all()
    assert len(alerts) == first.alerts_created
    assert all(a.rule_version_id == version_id and a.detection_details["SIGNAL_NOTICE"] == SIGNAL_NOTICE
               for a in alerts)
    # BR-406.2: the reason describes; the model's own name aside, it accuses no one.
    for alert in alerts:
        assert not ACCUSING.search(alert.reason.replace("fraud_gbm_v1", "")), alert.reason
    # A burst is one finding: no two alerts for one entity on one day from the transaction model.
    days = [(a.customer_id, datetime.fromisoformat(a.detection_details["window_start"]).astimezone(JAKARTA).date())
            for a in alerts if a.detection_details["UNIT"] == "FRAUD"]
    assert len(days) == len(set(days))

    second = run_ml_scoring(db)
    assert second.alerts_created == 0 and second.alerts_already_existing == first.alerts_created


# --- integrity ------------------------------------------------------------------------------


def test_a_tampered_model_is_refused(db, trained, tmp_path):
    folder = tmp_path / "aml_gbm_v1"
    shutil.copytree(trained["models"] / "aml_gbm_v1", folder)
    model = (folder / "model.txt").read_text()
    (folder / "model.txt").write_text(model.replace("shrinkage=0.05", "shrinkage=0.06", 1))
    with pytest.raises(RegistrationRefused, match="definition_hash"):
        register(db, folder)


def test_registration_is_audited_once_and_a_version_is_never_edited(db, trained):
    version = register(db, trained["models"] / "aml_gbm_v1")
    assert register(db, trained["models"] / "aml_gbm_v1") is None  # already registered
    event = db.scalar(select(AuditLog).where(AuditLog.action == "MODEL_REGISTERED_ACTIVE"))
    assert version.state is RuleState.ACTIVE and event.details["maker_checker"] == "BYPASSED"

    with pytest.raises(Exception, match="MODEL_VERSION_IMMUTABLE"):
        db.execute(update(ModelVersion).where(ModelVersion.id == version.id).values(threshold=0.01))
        db.commit()
    db.rollback()


def test_a_stored_score_is_never_rewritten(db, loaded):
    run_ml_scoring(db)
    score_id = db.scalar(select(MlScore.id).limit(1))
    with pytest.raises(Exception, match="APPEND_ONLY"):
        db.execute(update(MlScore).where(MlScore.id == score_id).values(score=0.0))
        db.commit()
    db.rollback()


def test_verify_rejects_a_model_built_for_another_feature_set(db, trained):
    version = register(db, trained["models"] / "fraud_gbm_v1")
    db.refresh(version)
    db.expunge(version)  # a detached copy: the stored row itself can never be edited
    version.feature_set_version = "fs_v1"
    with pytest.raises(ModelIntegrityError):
        verify(version)


# --- API ------------------------------------------------------------------------------------


def test_one_run_for_rules_and_models_and_the_scores_api(client, db, auth_headers, loaded):
    assert client.post("/detection/run", headers=auth_headers(UserRole.ROLE_TRIAGE)).status_code == 403
    response = client.post("/detection/run", headers=auth_headers(UserRole.ROLE_ANALYST))
    assert response.status_code == 200
    body = response.json()
    assert body["rules"][0]["pattern_code"] == "P02_STRUCTURING"
    assert {m["unit"] for m in body["ml"]["models"]} == {"AML", "FRAUD"}
    assert body["ml"]["ml_rule_versions_evaluated"] == []

    scores = client.get("/ml/scores", params={"unit": "FRAUD"}, headers=auth_headers(UserRole.ROLE_TRIAGE)).json()
    assert scores["total"] > 0 and scores["signal_notice"] == SIGNAL_NOTICE and "Synthetic" in scores["synthetic_notice"]
    first = scores["items"][0]
    assert first["flagged"] and first["transaction_ref"] and first["contributions"][0]["definition"]
    models = client.get("/ml/models", headers=auth_headers(UserRole.ROLE_INVESTIGATOR)).json()
    assert {m["version"] for m in models} == {"aml_gbm_v1", "fraud_gbm_v1"} and all(m["state"] == "ACTIVE" for m in models)


def test_a_rule_alert_shows_the_models_view_of_its_entity(client, db, auth_headers, loaded):
    run_p02_structuring(db)
    run_ml_scoring(db)
    alert = db.scalar(select(Alert).where(Alert.pattern_code == "P02_STRUCTURING"))
    assert alert is not None
    body = client.get(f"/alerts/{alert.id}", headers=auth_headers(UserRole.ROLE_TRIAGE)).json()

    assert body["signal_notice"] == SIGNAL_NOTICE
    assert body["model_signals"] and all(s["customer_id"] == str(alert.customer_id) for s in body["model_signals"])
    assert body["model_score"] == max(s["score"] for s in body["model_signals"])
    listed = client.get("/alerts", headers=auth_headers(UserRole.ROLE_TRIAGE)).json()["items"]
    assert next(a for a in listed if a["id"] == str(alert.id))["model_score"] == body["model_score"]
