"""Model lab, from saved draft to detection: Parquet uploads, per-score reasons,
MLRO approval (FR-405), model alerts without a pattern rule (CF-05) and
monitoring uploads in a training dataset's layout."""

import io
from collections.abc import Callable

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.ml.lab.features import FEATURE_GROUPS, build_matrix
from app.ml.lab.mapping import apply_mapping, read_csv_bytes, read_table_bytes
from app.ml.lab.scoring import load_predictor
from app.ml.lab.trainer import train_run
from app.models.account import Account
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.enums import EntityType, RuleState, UserRole
from app.models.ingestion_batch import IngestionBatch
from app.models.ml import MlScore, ModelVersion
from app.models.rule import Rule, RuleVersion
from app.models.transaction import Transaction

from lab_helpers import make_dataset, mapping_for

ALL_GROUPS = [g["key"] for g in FEATURE_GROUPS]
JUSTIFICATION = "Terbaik di validasi; presisi dan recall seimbang."
NOTE = "Disetujui untuk pemantauan data sintetis."


def _parquet_from_csv(csv_bytes: bytes) -> bytes:
    frame = pd.read_csv(io.BytesIO(csv_bytes), dtype=str, keep_default_na=False)
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def _ml_rule_state(db: Session) -> RuleState:
    db.expire_all()
    return db.scalar(select(RuleVersion.state).join(Rule).where(Rule.pattern_code == "ML_SCORE"))


def _trained_draft(client: TestClient, analyst: dict, *, model: str = "lightgbm", rows: int = 2500) -> dict:
    upload = client.post("/lab/datasets", headers=analyst, files={"file": ("lab.csv", make_dataset(rows), "text/csv")})
    assert upload.status_code == 201, upload.text
    dataset = upload.json()
    s = dataset["suggestion"]
    mapped = client.post(f"/lab/datasets/{dataset['id']}/mapping", headers=analyst, json={
        "fields": s["fields"], "label_column": s["label_column"], "positive_values": s["positive_values"],
        "category_column": s["category_column"], "date_format": s["date_format"]})
    assert mapped.status_code == 200, mapped.text
    run = client.post(f"/lab/datasets/{dataset['id']}/runs", headers=analyst,
                      json={"feature_groups": ALL_GROUPS, "models": [model], "train_share": 0.6})
    assert run.status_code == 201, run.text
    result = next(m for m in run.json()["results"]["models"] if m["key"] == model)
    saved = client.post(f"/lab/runs/{run.json()['id']}/save", headers=analyst,
                        json={"model_key": model, "threshold_index": result["default_index"]})
    assert saved.status_code == 200, saved.text
    return {"dataset": dataset, "version_id": saved.json()["model_version_id"], "version": saved.json()["version"]}


def _approve(client, analyst, mlro, version_id, *, model_alerts=True):
    submitted = client.post(f"/lab/models/{version_id}/submit", headers=analyst, json={"justification": JUSTIFICATION})
    assert submitted.status_code == 200, submitted.text
    approved = client.post(f"/lab/models/{version_id}/approve", headers=mlro,
                           json={"note": NOTE, "model_alerts": model_alerts})
    assert approved.status_code == 200, approved.text
    return approved.json()


def test_parquet_dataset_reads_exactly_like_the_same_csv(
    client: TestClient, auth_headers: Callable[[UserRole], dict[str, str]]
) -> None:
    analyst = auth_headers(UserRole.ROLE_ANALYST)
    csv_bytes = make_dataset(800)
    results = {}
    for name, payload in (("lab.csv", csv_bytes), ("lab.parquet", _parquet_from_csv(csv_bytes))):
        upload = client.post("/lab/datasets", headers=analyst, files={"file": (name, payload, "application/octet-stream")})
        assert upload.status_code == 201, upload.text
        dataset = upload.json()
        s = dataset["suggestion"]
        mapped = client.post(f"/lab/datasets/{dataset['id']}/mapping", headers=analyst, json={
            "fields": s["fields"], "label_column": s["label_column"], "positive_values": s["positive_values"],
            "category_column": s["category_column"], "date_format": s["date_format"]})
        results[dataset["file_format"]] = (dataset["row_count"], s["fields"], s["label_column"],
                                           mapped.json()["validation"])
    assert set(results) == {"csv", "parquet"}
    assert results["csv"] == results["parquet"]


def test_typed_parquet_keeps_its_time_zone_and_precision() -> None:
    frame = pd.read_csv(io.BytesIO(make_dataset(600, defects=False)), dtype=str, keep_default_na=False)
    frame["Amount"] = frame.Amount.astype(float)
    frame["Timestamp"] = pd.to_datetime(frame.Timestamp)
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    raw, file_format = read_table_bytes(buffer.getvalue())
    assert file_format == "parquet"
    mapped = apply_mapping(raw, mapping_for(raw))
    assert set(mapped.frame.timestamp_precision) == {"second"}
    assert mapped.frame.source_timezone_known.all()
    expected = apply_mapping(read_csv_bytes(make_dataset(600, defects=False)),
                             mapping_for(read_csv_bytes(make_dataset(600, defects=False))))
    assert mapped.report["positives"] == expected.report["positives"]


def test_every_lab_model_explains_each_score() -> None:
    raw = read_csv_bytes(make_dataset(2500, defects=False))
    mapped = apply_mapping(raw, mapping_for(raw))
    outcome = train_run(mapped.frame, mapped.labels, None, feature_groups=ALL_GROUPS,
                        model_keys=["logistic_regression", "lightgbm", "isolation_forest"], train_share=0.6)
    X, _ = build_matrix(mapped.frame, np.ones(len(mapped.frame), dtype=bool))
    sample = X.iloc[:6]
    for model in outcome.models:
        predictor = load_predictor(model.artefact_type, model.model_text, model.features)
        explanations = predictor.explain(sample, top_n=len(model.features))
        assert len(explanations) == len(sample)
        for row in explanations:
            assert row and all(np.isfinite(c["contribution"]) for c in row)
            assert [abs(c["contribution"]) for c in row] == sorted((abs(c["contribution"]) for c in row), reverse=True)
        if model.key == "logistic_regression":
            # Additive on the log-odds scale: contributions plus the intercept give the score.
            import json

            intercept = json.loads(model.model_text)["intercept"]
            scores = predictor.scores(sample)
            for score, row in zip(scores, explanations):
                total = intercept + sum(c["contribution"] for c in row)
                assert total == pytest.approx(np.log(score / (1 - score)), abs=0.01)
        if model.key == "lightgbm":
            import lightgbm as lgb

            booster = lgb.Booster(model_str=model.model_text)
            raw_contrib = booster.predict(sample[booster.feature_name()], pred_contrib=True)[:, :-1]
            strongest = booster.feature_name()[int(np.argmax(np.abs(raw_contrib[0])))]
            assert explanations[0][0]["feature"] == strongest


def test_approval_needs_an_analyst_then_a_different_mlro(
    client: TestClient, db: Session, auth_headers: Callable[[UserRole], dict[str, str]]
) -> None:
    analyst, mlro, triage = (auth_headers(r) for r in (UserRole.ROLE_ANALYST, UserRole.ROLE_MLRO, UserRole.ROLE_TRIAGE))
    first = _trained_draft(client, analyst, model="logistic_regression", rows=1500)
    vid = first["version_id"]

    assert client.post(f"/lab/models/{vid}/submit", headers=triage, json={"justification": JUSTIFICATION}).status_code == 403
    assert client.post(f"/lab/models/{vid}/submit", headers=analyst, json={"justification": "short"}).status_code == 409
    assert client.post(f"/lab/models/{vid}/approve", headers=mlro, json={"note": NOTE}).status_code == 409  # not submitted
    submitted = client.post(f"/lab/models/{vid}/submit", headers=analyst, json={"justification": JUSTIFICATION})
    assert submitted.json()["items"][0]["state"] == "PENDING_APPROVAL"
    assert client.post(f"/lab/models/{vid}/approve", headers=analyst, json={"note": NOTE}).status_code == 403
    assert _ml_rule_state(db) == RuleState.DRAFT

    approved = client.post(f"/lab/models/{vid}/approve", headers=mlro, json={"note": NOTE, "model_alerts": True})
    assert approved.status_code == 200, approved.text
    body = approved.json()
    item = next(i for i in body["items"] if i["id"] == vid)
    assert item["state"] == "ACTIVE" and item["approved_by_email"].startswith("role_mlro")
    assert [h["action"] for h in item["history"]][-2:] == ["ANOMALY_MODEL_VERSION_SUBMITTED",
                                                           "ANOMALY_MODEL_VERSION_APPROVED"]
    assert body["model_alerts"]["active"] and _ml_rule_state(db) == RuleState.ACTIVE

    # A second approved model replaces the first for the same unit.
    second = _trained_draft(client, analyst, model="lightgbm", rows=1500)
    replaced = _approve(client, analyst, mlro, second["version_id"])
    states = {i["version"]: (i["state"], i["superseded_by_version"]) for i in replaced["items"]}
    assert states[first["version"]] == ("RETIRED", second["version"])
    assert states[second["version"]] == ("ACTIVE", None)
    assert db.scalar(select(func.count()).select_from(ModelVersion).where(ModelVersion.state == RuleState.ACTIVE)) == 1

    suspended = client.post(f"/lab/models/{second['version_id']}/suspend", headers=mlro, json={"reason": NOTE})
    assert next(i for i in suspended.json()["items"] if i["id"] == second["version_id"])["state"] == "SUSPENDED"
    off = client.post("/lab/model-alerts", headers=mlro, json={"enabled": False, "reason": NOTE})
    assert not off.json()["model_alerts"]["active"] and _ml_rule_state(db) == RuleState.SUSPENDED
    on = client.post("/lab/model-alerts", headers=mlro, json={"enabled": True, "reason": NOTE})
    assert on.json()["model_alerts"]["active"] and _ml_rule_state(db) == RuleState.ACTIVE
    assert client.post("/lab/model-alerts", headers=analyst, json={"enabled": False, "reason": NOTE}).status_code == 403


def test_self_approval_is_refused_and_recorded(
    client: TestClient, db: Session, auth_headers: Callable[[UserRole], dict[str, str]]
) -> None:
    analyst, mlro = auth_headers(UserRole.ROLE_ANALYST), auth_headers(UserRole.ROLE_MLRO)
    draft = _trained_draft(client, analyst, model="logistic_regression", rows=1500)
    client.post(f"/lab/models/{draft['version_id']}/submit", headers=analyst, json={"justification": JUSTIFICATION})
    mlro_id = client.get("/auth/me", headers=mlro).json()["id"]
    # Only approval provenance may change on a model version (migration 0008); make the MLRO the submitter.
    db.execute(text("UPDATE model_version SET submitted_by = :u WHERE id = :v"), {"u": mlro_id, "v": draft["version_id"]})
    db.commit()
    refused = client.post(f"/lab/models/{draft['version_id']}/approve", headers=mlro, json={"note": NOTE})
    assert refused.status_code == 409 and "sendiri" in refused.json()["detail"]
    assert db.scalar(select(func.count()).select_from(AuditLog)
                     .where(AuditLog.action == "ANOMALY_MODEL_SELF_APPROVAL_REFUSED")) == 1
    db.expire_all()
    assert db.get(ModelVersion, draft["version_id"]).state == RuleState.PENDING_APPROVAL


def test_monitoring_upload_is_scored_by_the_approved_model_and_raises_alerts(
    client: TestClient, db: Session, auth_headers: Callable[[UserRole], dict[str, str]]
) -> None:
    analyst, mlro, data_ops = (auth_headers(r) for r in (UserRole.ROLE_ANALYST, UserRole.ROLE_MLRO, UserRole.ROLE_DATA_OPS))
    draft = _trained_draft(client, analyst)
    _approve(client, analyst, mlro, draft["version_id"], model_alerts=True)

    # One account already exists as master data: it is used as is, not replaced by a proxy.
    customer = Customer(customer_ref="CUST-REAL", full_name="Real Master Data", entity_type=EntityType.INDIVIDUAL)
    db.add(customer)
    db.flush()
    db.add(Account(account_number="ACC-0001", customer_id=customer.id, account_type="WALLET", currency="IDR"))
    db.commit()

    new_file = make_dataset(1500, seed=8)  # includes a bad date, a duplicate ID and a row without a label
    parquet = _parquet_from_csv(new_file)
    dataset_id = draft["dataset"]["id"]
    assert client.post(f"/lab/datasets/{dataset_id}/monitor", headers=analyst,
                       files={"file": ("new.parquet", parquet, "application/octet-stream")}).status_code == 403
    response = client.post(f"/lab/datasets/{dataset_id}/monitor", headers=data_ops,
                           files={"file": ("new.parquet", parquet, "application/octet-stream")})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["file_format"] == "parquet"
    assert result["total_rows"] == 1503 and result["accepted"] + result["quarantined"] == 1503
    assert result["quarantined"] == 2  # the bad date and the duplicate; the missing label does not matter here
    assert any("diabaikan" in note for note in result["mapping_report"]["notes"])

    frame = pd.read_csv(io.BytesIO(new_file), dtype=str, keep_default_na=False)
    accounts = set(frame.AccountID)
    assert result["account_proxies_created"] == len(accounts - {"ACC-0001"})
    assert db.scalar(select(Account.account_type).where(Account.account_number == "ACC-0001")) == "WALLET"
    assert db.scalar(select(func.count()).select_from(Customer).where(Customer.customer_ref.like("OBS-%"))) == \
        len(accounts - {"ACC-0001"})
    batch = db.scalar(select(IngestionBatch).where(IngestionBatch.batch_ref == result["batch_ref"]))
    assert batch.source_system == "MODEL_LAB" and batch.expected_records == 1503
    assert db.scalar(select(func.count()).select_from(Transaction)) == result["accepted"]

    run = result["models"][0]
    assert run["model_version"] == draft["version"] and run["units_scored"] == result["accepted"]
    assert run["units_flagged"] > 0 and result["model_alerts_active"] and result["model_alerts_created"] > 0
    scores = db.scalars(select(MlScore).where(MlScore.flagged.is_(True))).all()
    assert scores and all(s.contributions for s in scores)
    alert = db.scalar(select(Alert).where(Alert.pattern_code == "ML_SCORE").limit(1))
    assert draft["version"] in alert.reason and "Strongest features" in alert.reason

    listed = client.get("/ml/scores", headers=data_ops, params={"unit": "FRAUD"}).json()
    assert listed["items"][0]["explanation_method"] == "tree_shap_log_odds"
    assert all(c["definition"] for c in listed["items"][0]["contributions"])

    again = client.post(f"/lab/datasets/{dataset_id}/monitor", headers=data_ops,
                        files={"file": ("new.parquet", parquet, "application/octet-stream")})
    assert again.status_code == 409


def test_with_model_alerts_off_scores_are_stored_but_raise_nothing(
    client: TestClient, db: Session, auth_headers: Callable[[UserRole], dict[str, str]]
) -> None:
    analyst, mlro, data_ops = (auth_headers(r) for r in (UserRole.ROLE_ANALYST, UserRole.ROLE_MLRO, UserRole.ROLE_DATA_OPS))
    draft = _trained_draft(client, analyst)
    _approve(client, analyst, mlro, draft["version_id"], model_alerts=False)
    assert _ml_rule_state(db) == RuleState.DRAFT
    response = client.post(f"/lab/datasets/{draft['dataset']['id']}/monitor", headers=data_ops,
                           files={"file": ("new.csv", make_dataset(1500, seed=8), "text/csv")})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["models"][0]["units_flagged"] > 0 and not result["model_alerts_active"]
    assert result["model_alerts_created"] == 0
    assert db.scalar(select(func.count()).select_from(Alert).where(Alert.pattern_code == "ML_SCORE")) == 0
