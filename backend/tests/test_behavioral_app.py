"""Behavioral fit/upload/runtime parity, unknown metadata and version semantics."""

import csv
import io
import json
import shutil
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from app.ml.behavioral.features import (
    FEATURE_DEFINITIONS, FEATURE_SET_VERSION, build_features, feature_names, fit_vocabulary,
)
from app.ml.behavioral.model import ARTEFACT_TYPE, EXPLANATION_METHOD, fit_model, predict_model
from app.ml.behavioral.standardize import CANONICAL_COLUMNS, normalize_frame
from app.ml.models import canonical, definition_hash
from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.enums import RuleState, UserRole
from app.models.ml import MlScore, ModelVersion
from app.models.transaction import Transaction
from app.scripts.load_behavioral_dataset import canonical_csv, load_account_proxies
from app.scripts.register_models import RegistrationRefused, register
from app.services.ingestion import ingest_transactions_csv
from app.services.ml_scoring import ModelIntegrityError, active_models, behavioral_frame_from_db, run_ml_scoring, verify


def _csv(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=[*CANONICAL_COLUMNS, "source_ordinal"])
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


@pytest.fixture(scope="module")
def behavioral_artifacts(tmp_path_factory):
    folder = tmp_path_factory.mktemp("behavioral_app_models")
    observations = []
    for account in ("B-A", "B-B"):
        for day in range(80):
            day_only = account == "B-A"
            observations.append({
                "transaction_ref": f"{account}-{day}", "account_number": account,
                "transaction_date": (pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=day)).isoformat(),
                "amount": f"{1 + (day % 7) * 3}.50", "currency": "USD",
                "direction": "DEBIT" if day % 3 else "CREDIT", "channel": "ONLINE",
                "device_ref": "DEVICE-A" if day < 75 else "DEVICE-NEW",
                "merchant_ref": f"M-{day % 3}", "location": "Source location",
                "ip_address": "192.0.2.1", "login_attempts": "1" if day < 75 else "8",
                "transaction_duration": "20", "account_balance": "500.00",
                "customer_age": "" if day_only else "52", "customer_occupation": "" if day_only else "Teacher",
                "timestamp_precision": "day" if day_only else "second",
                "source_timezone_known": "false" if day_only else "true",
            })
    frame = normalize_frame(pd.DataFrame(observations), "fcip_canonical_v1").frame
    vocabulary = fit_vocabulary(frame)
    matrices = {}
    for unit in ("AML", "FRAUD"):
        matrix = build_features(frame, unit, vocabulary)
        matrices[unit] = matrix
        names = feature_names(unit)
        model_text, _ = fit_model(matrix, names, seed=20261014)
        card = {
            "version": f"{unit.lower()}_if_v1", "unit": unit, "artefact_type": ARTEFACT_TYPE,
            "feature_set": {"version": FEATURE_SET_VERSION, "features": names, "vocabulary": vocabulary,
                            "normalization_profile": "fcip_canonical_v1",
                            "definitions": {code: FEATURE_DEFINITIONS[(unit, code)] for code in names}},
            "model": {"unit": unit, "threshold": 0.5, "score_kind": "anomaly_score",
                      "explanation_method": EXPLANATION_METHOD},
            "training_data": {"profile": "test_observations", "observations": len(frame), "labels_used": False},
            "validation": {"unlabelled": {"units": len(matrix), "labels_available": False},
                           "synthetic_stress": {}},
        }
        card["definition_hash"] = definition_hash(model_text, canonical(names), FEATURE_SET_VERSION,
                                                   canonical(vocabulary), canonical(0.5))
        target = folder / card["version"]
        target.mkdir()
        (target / "model.txt").write_text(model_text)
        (target / "model_card.json").write_text(json.dumps(card))
    return folder, frame, matrices


@pytest.mark.parametrize(("precision", "source_date"), [("day", "2026-01-01"), ("minute", "2026-01-01T10:15")])
def test_upload_reuses_canonical_validation_and_preserves_unknown_context(db, client, auth_headers, precision, source_date):
    good = {"transaction_ref": "T-UNKNOWN", "account_number": "B-1", "transaction_date": source_date,
            "amount": "0.50", "currency": "USD", "direction": "DEBIT", "channel": "ONLINE",
            "timestamp_precision": precision, "source_timezone_known": "false", "source_ordinal": "8765"}
    accepted = normalize_frame(pd.DataFrame([good]), "fcip_canonical_v1").frame
    load_account_proxies(db, accepted)
    bad_ip = {**good, "transaction_ref": "BAD-IP", "ip_address": "999.0.0.1"}
    bad_login = {**good, "transaction_ref": "BAD-LOGIN", "login_attempts": "-1"}
    response = client.post("/ingestion/transactions", headers=auth_headers(UserRole.ROLE_DATA_OPS),
                           files={"file": ("canonical.csv", _csv([good, bad_ip, bad_login]), "text/csv")},
                           data={"source_system": "BEHAVIORAL_TEST", "business_date": "2026-01-01", "expected_records": "3"})
    assert response.status_code == 200, response.text
    assert (response.json()["accepted"], response.json()["quarantined"]) == (1, 2)
    transaction = db.scalar(select(Transaction))
    assert transaction.currency == "USD"
    assert transaction.timestamp_precision == precision and transaction.source_timezone_known is False
    assert transaction.login_attempts is transaction.account_balance is transaction.customer_age is None
    assert transaction.ip_address is transaction.transaction_duration is transaction.customer_occupation is None
    customer = db.scalar(select(Customer))
    assert customer.full_name == "B-1"
    assert customer.id_number is customer.country is customer.date_of_birth is None
    headers = auth_headers(UserRole.ROLE_ANALYST)
    row = client.get("/transactions", headers=headers).json()["items"][0]
    assert row["timestamp_precision"] == precision and row["source_timezone_known"] is False
    overview = client.get("/overview", headers=headers).json()
    assert overview["latest_transaction_date"] == row["transaction_date"]
    assert overview["latest_transaction_timestamp_precision"] == precision
    assert overview["latest_transaction_source_timezone_known"] is False


def test_proxy_preparation_is_idempotent_and_does_not_guess_identity_from_account_collisions(db, behavioral_artifacts):
    _, frame, _ = behavioral_artifacts
    one_account = frame.loc[frame.account_number.eq("B-A")]
    assert load_account_proxies(db, one_account) == 1
    assert load_account_proxies(db, one_account) == 0
    conflicting_currency = one_account.assign(currency="IDR")
    with pytest.raises(ValueError, match="currency differs"):
        load_account_proxies(db, conflicting_currency)
    account = db.scalar(select(Account))
    account.account_type = "SAVINGS"
    db.commit()
    with pytest.raises(ValueError, match="non-proxy"):
        load_account_proxies(db, one_account)


def test_runtime_scores_match_offline_and_explanations_keep_their_own_semantics(
    db, client, auth_headers, behavioral_artifacts,
):
    folder, frame, matrices = behavioral_artifacts
    load_account_proxies(db, frame)
    result = ingest_transactions_csv(db, file_name="behavioral.csv", content=canonical_csv(frame))
    assert result.accepted == len(frame) and result.quarantined == 0
    versions = {unit: register(db, folder / f"{unit.lower()}_if_v1") for unit in ("AML", "FRAUD")}
    served, _, _ = behavioral_frame_from_db(db, "USD")
    metadata = ["transaction_date", "location", "ip_address", "login_attempts", "transaction_duration",
                "account_balance", "customer_age", "customer_occupation", "timestamp_precision", "source_timezone_known"]
    pd.testing.assert_frame_equal(
        served.set_index("transaction_ref")[metadata].sort_index(),
        frame.set_index("transaction_ref")[metadata].sort_index(), check_dtype=False,
    )
    vocabulary = versions["AML"].model_card["feature_set"]["vocabulary"]
    for unit in versions:
        pd.testing.assert_frame_equal(build_features(served, unit, vocabulary), matrices[unit])
    run = run_ml_scoring(db)
    assert run.ml_rule_versions_evaluated == [] and run.alerts_created == 0
    assert {r.unit for r in run.models} == {"AML", "FRAUD"}
    customer_accounts = {c.id: c.full_name for c in db.scalars(select(Customer))}
    refs = {t.id: t.transaction_ref for t in db.scalars(select(Transaction))}
    for unit, version in versions.items():
        matrix = matrices[unit]
        scores = predict_model(version.model_text, matrix)
        expected = {((row.account_number, pd.Timestamp(row.as_of)) if unit == "AML" else row.transaction_ref): value
                    for row, value in zip(matrix.itertuples(), scores) if value >= version.threshold}
        stored = db.scalars(select(MlScore).where(MlScore.model_version_id == version.id)).all()
        actual = {((customer_accounts[s.customer_id], pd.Timestamp(s.as_of)) if unit == "AML" else refs[s.transaction_id]): s.score
                  for s in stored}
        assert actual.keys() == expected.keys()
        assert all(np.isclose(actual[key], expected[key], atol=1e-12, rtol=0) for key in actual)
    assert sum(r.scores_stored for r in run.models) > 0
    assert sum(r.scores_stored for r in run_ml_scoring(db).models) == 0
    headers = auth_headers(UserRole.ROLE_ANALYST)
    models = client.get("/ml/models", headers=headers).json()
    assert all(m["training_kind"] == "unsupervised" and m["score_kind"] == "anomaly_score" for m in models)
    assert all("precision" not in m["validation"] and m["currency"] == "USD" for m in models)
    scores = client.get("/ml/scores", headers=headers).json()
    assert scores["items"]
    for score in scores["items"]:
        assert score["explanation_method"] == EXPLANATION_METHOD and score["score_kind"] == "anomaly_score"
        assert score["currency"] == "USD"
        for contribution in score["contributions"]:
            assert contribution["definition"] == FEATURE_DEFINITIONS[(score["unit"], contribution["feature"])]


def test_registry_rejects_wrong_feature_definition_even_with_same_model_hash(db, behavioral_artifacts):
    folder, _, _ = behavioral_artifacts
    version = register(db, folder / "fraud_if_v1")
    tampered = deepcopy(version.model_card)
    feature = tampered["feature_set"]["features"][0]
    tampered["feature_set"]["definitions"][feature] = "Invented TreeSHAP probability interpretation"
    candidate = ModelVersion(artefact_type=version.artefact_type, version=version.version,
                             definition_hash=version.definition_hash, unit=version.unit,
                             feature_set_version=version.feature_set_version, threshold=version.threshold,
                             model_text=version.model_text, model_card=tampered)
    with pytest.raises(ModelIntegrityError, match="definitions"):
        verify(candidate)


def test_registry_uses_card_unit_and_refuses_changed_provenance_on_repeat(db, behavioral_artifacts, tmp_path):
    folder, _, _ = behavioral_artifacts
    target = tmp_path / "portable_version"
    shutil.copytree(folder / "fraud_if_v1", target)
    path = target / "model_card.json"
    card = json.loads(path.read_text())
    card["version"] = "any_bank_fraud_v2"
    path.write_text(json.dumps(card))
    version = register(db, target)
    assert version.unit == "FRAUD" and version.version == "any_bank_fraud_v2"
    assert register(db, target) is None
    card["training_data"]["observations"] += 1
    path.write_text(json.dumps(card))
    with pytest.raises(RegistrationRefused, match="different immutable content"):
        register(db, target)


def test_draft_registration_preserves_active_model_and_is_visible_without_being_scored(
    db, client, auth_headers, behavioral_artifacts, tmp_path,
):
    folder, _, _ = behavioral_artifacts
    older = tmp_path / "older_active"
    shutil.copytree(folder / "fraud_if_v1", older)
    path = older / "model_card.json"
    card = json.loads(path.read_text())
    card["version"] = "fraud_if_older_active"
    path.write_text(json.dumps(card))
    active = register(db, older)
    draft = register(db, folder / "fraud_if_v1", state=RuleState.DRAFT)
    assert active.state is RuleState.ACTIVE and draft.state is RuleState.DRAFT
    assert register(db, folder / "fraud_if_v1", state=RuleState.DRAFT) is None
    assert active_models(db)["FRAUD"].version.id == active.id
    assert [run.model_version for run in run_ml_scoring(db).models] == [active.version]
    assert db.scalar(select(MlScore).where(MlScore.model_version_id == draft.id)) is None
    response = client.get("/ml/models", headers=auth_headers(UserRole.ROLE_ANALYST))
    assert response.status_code == 200
    assert {row["version"]: row["state"] for row in response.json()} == {
        active.version: "ACTIVE", draft.version: "DRAFT",
    }
    events = db.scalars(select(AuditLog).where(AuditLog.object_id == draft.id)).all()
    assert len(events) == 1
    event = events[0]
    assert event.action == "MODEL_REGISTERED_DRAFT" and event.from_state is None and event.to_state == "DRAFT"
    assert event.details["activation_requested"] is False
    assert "maker_checker" not in event.details and "bypass" not in event.reason.lower()
    with pytest.raises(RegistrationRefused, match="registration cannot change its lifecycle"):
        register(db, folder / "fraud_if_v1", state=RuleState.ACTIVE)


def test_malformed_card_fails_as_integrity_error(db, behavioral_artifacts):
    folder, _, _ = behavioral_artifacts
    version = register(db, folder / "fraud_if_v1")
    candidate = ModelVersion(version=version.version, artefact_type=ARTEFACT_TYPE, model_card={})
    with pytest.raises(ModelIntegrityError, match="malformed"):
        verify(candidate)


def test_runtime_reports_unknown_currency_as_unscored_without_conversion(db, behavioral_artifacts):
    folder, frame, _ = behavioral_artifacts
    other = frame.iloc[[0]].copy()
    other["transaction_ref"] = "T-UNKNOWN-CURRENCY"
    other["account_number"] = "B-UNKNOWN-CURRENCY"
    other["currency"] = "XXX"
    combined = pd.concat([frame, other], ignore_index=True)
    load_account_proxies(db, combined)
    result = ingest_transactions_csv(db, file_name="mixed.csv", content=canonical_csv(combined))
    assert result.accepted == len(combined)
    register(db, folder / "fraud_if_v1")
    [run] = run_ml_scoring(db).models
    assert run.units_scored == len(frame) and run.observations_skipped_currency == 1
    unknown = db.scalar(select(Transaction).where(Transaction.transaction_ref == "T-UNKNOWN-CURRENCY"))
    assert unknown.currency == "XXX"
    assert db.scalar(select(MlScore).where(MlScore.transaction_id == unknown.id)) is None
