"""Regression checks for source-scoped final disclosure and frozen provenance."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier
import uuid

from alembic import command
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.database import SessionLocal
from app.ml.lab.features import FEATURE_GROUPS
from app.ml.lab.mapping import apply_mapping, read_csv_bytes
from app.ml.lab.trainer import (LabTrainingError, availability_dates, chronological_split, train_run)
from app.models.enums import RuleState, UserRole
from app.models.lab import LabDataset, LabEvaluationProtocol, LabModel, LabRun
from app.models.ml import ModelVersion
from app.models.user import User
from app.services import lab

from lab_helpers import make_dataset, mapping_for

GROUPS = [g["key"] for g in FEATURE_GROUPS]


def test_whole_availability_groups_are_invariant_to_reference_names():
    frame = pd.DataFrame({"transaction_ref": list("abcdefghij"),
                          "transaction_date": pd.to_datetime(["2026-01-01"] * 4 + ["2026-01-02"] * 3
                                                               + ["2026-01-03"] * 3, utc=True)})
    first = chronological_split(frame, .5)
    frame.transaction_ref = list("jihgfedcba")
    np.testing.assert_array_equal(first, chronological_split(frame, .5))
    for date in frame.transaction_date.unique():
        assert len(set(first[frame.transaction_date == date])) == 1
    available = availability_dates(frame)
    assert available[first == "train"].max() < available[first == "validation"].min()
    assert available[first == "validation"].max() < available[first == "test"].min()


def test_one_large_time_tie_is_refused_instead_of_imposing_reference_order():
    frame = pd.DataFrame({"transaction_ref": list("abcdef"),
                          "transaction_date": pd.to_datetime(["2026-01-01"] * 6, utc=True)})
    with pytest.raises(LabTrainingError, match="tiga waktu"):
        chronological_split(frame, .6)


def test_day_only_observation_is_not_available_before_exact_later_same_day():
    frame = pd.DataFrame({"transaction_ref": list("abcdefghij"),
        "transaction_date": pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T12:00:00Z",
            "2026-01-02T00:00:00Z", "2026-01-02T12:00:00Z", "2026-01-03T00:00:00Z",
            "2026-01-03T12:00:00Z", "2026-01-04T00:00:00Z", "2026-01-04T12:00:00Z",
            "2026-01-05T00:00:00Z", "2026-01-05T12:00:00Z"]),
        "timestamp_precision": ["day", "second"] * 5})
    part = chronological_split(frame, .5)
    available = availability_dates(frame)
    assert available.iloc[0] > available.iloc[1]
    assert available[part == "train"].max() < available[part == "validation"].min()
    assert available[part == "validation"].max() < available[part == "test"].min()


def test_test_targets_cannot_change_fit_selection_or_comparison(monkeypatch):
    raw = read_csv_bytes(make_dataset(1200, defects=False))
    mapped = apply_mapping(raw, mapping_for(raw))
    part = chronological_split(mapped.frame, .6)
    original = lab.score
    # Train code must score only train/validation. Final evaluation uses a
    # separate service call after the immutable choice has been flushed.
    from app.ml.lab import trainer
    observed_lengths = []

    def observed_score(key, model, matrix):
        observed_lengths.append(len(matrix))
        return original(key, model, matrix)

    monkeypatch.setattr(trainer, "score", observed_score)
    first = train_run(mapped.frame, mapped.labels, mapped.categories, feature_groups=GROUPS,
                      model_keys=["logistic_regression"], train_share=.6)
    altered = mapped.labels.copy()
    altered[part == "test"] = 0  # Single-class test is not a pre-reveal gate.
    changed_categories = mapped.categories.copy()
    changed_categories.loc[part == "test"] = "HELDOUT_ONLY_SECRET"
    second = train_run(mapped.frame, altered, changed_categories, feature_groups=GROUPS,
                       model_keys=["logistic_regression"], train_share=.6)
    assert first.models[0].model_text == second.models[0].model_text
    assert first.models[0].thresholds == second.models[0].thresholds
    assert first.models[0].test_curve == second.models[0].test_curve == []
    for outcome in (first, second):
        summary = outcome.results["models"][0]
        assert "test" not in summary and "test_by_category" not in summary
        assert "positive_rate_test" not in outcome.results
        assert "positives" not in outcome.results["split"]["test"]
        assert "HELDOUT_ONLY_SECRET" not in json.dumps(outcome.results)
    assert observed_lengths == [int((part == "validation").sum())] * 2


def test_supervised_fit_refuses_validation_pattern_missing_from_train():
    raw = read_csv_bytes(make_dataset(1000, defects=False))
    mapped = apply_mapping(raw, mapping_for(raw))
    part = chronological_split(mapped.frame, .6)
    mapped.categories.loc[(part == "validation") & (mapped.labels == 1)] = "ATO"
    with pytest.raises(LabTrainingError, match="ATO"):
        train_run(mapped.frame, mapped.labels, mapped.categories, feature_groups=GROUPS,
                  model_keys=["logistic_regression"], train_share=.6)


def _upload_and_train(client, headers, *, content=None, model_keys=None):
    payload = content if content is not None else make_dataset(1600)
    upload = client.post("/lab/datasets", headers=headers, files={"file": ("lab.csv", payload, "text/csv")})
    assert upload.status_code == 201, upload.text
    dataset = upload.json()
    suggestion = dataset["suggestion"]
    mapping = {key: suggestion[key] for key in
               ("fields", "label_column", "positive_values", "category_column", "date_format")}
    mapped = client.post(f"/lab/datasets/{dataset['id']}/mapping", headers=headers, json=mapping)
    assert mapped.status_code == 200, mapped.text
    config = {"feature_groups": GROUPS, "models": model_keys or ["logistic_regression"], "train_share": .6}
    trained = client.post(f"/lab/datasets/{dataset['id']}/runs", headers=headers, json=config)
    assert trained.status_code == 201, trained.text
    return dataset, mapped.json()["mapping"], config, trained.json(), payload


def test_source_gate_hides_test_and_refuses_alternative_choices(client, auth_headers, db):
    headers = auth_headers(UserRole.ROLE_ANALYST)
    dataset, mapping, config, run, payload = _upload_and_train(client, headers, model_keys=["logistic_regression", "lightgbm"])
    assert run["evaluation"]["state"] == "SEALED"
    assert "test" not in run["results"]["models"][0]
    assert "positives" not in run["results"]["split"]["test"]
    # A second development run uses exactly the same source/mapping/split.
    another = client.post(f"/lab/datasets/{dataset['id']}/runs", headers=headers, json=config)
    assert another.status_code == 201, another.text
    assert another.json()["evaluation"]["protocol_id"] == run["evaluation"]["protocol_id"]
    model = run["results"]["models"][0]
    chosen = {"model_key": model["key"], "threshold_index": model["default_index"]}
    saved = client.post(f"/lab/runs/{run['id']}/save", headers=headers, json=chosen)
    assert saved.status_code == 200, saved.text
    assert saved.json()["test"]["rows"] > 0
    again = client.post(f"/lab/runs/{run['id']}/save", headers=headers, json=chosen)
    assert again.status_code == 200 and again.json()["already_saved"]
    assert client.post(f"/lab/runs/{run['id']}/save", headers=headers,
                       json={**chosen, "threshold_index": (chosen["threshold_index"] + 1) % len(model["validation_curve"])}).status_code == 409
    assert client.post(f"/lab/runs/{another.json()['id']}/save", headers=headers, json=chosen).status_code == 409
    assert client.post(f"/lab/datasets/{dataset['id']}/runs", headers=headers, json=config).status_code == 409
    shown = client.get(f"/lab/runs/{another.json()['id']}", headers=headers).json()
    assert shown["evaluation"]["state"] == "REVEALED"
    assert shown["evaluation"]["selected_final"]["version"] == saved.json()["version"]
    assert all("test" not in candidate for candidate in shown["results"]["models"])
    version = db.get(ModelVersion, saved.json()["model_version_id"])
    assert version.model_card["training_data"]["mapping_snapshot"] == mapping
    assert version.model_card["training_data"]["mapping_sha256"] == run["config"]["mapping_sha256"]


def test_identical_uploads_cannot_change_mapping_split_or_bypass_final(client, auth_headers):
    headers = auth_headers(UserRole.ROLE_ANALYST)
    first, mapping, config, run, payload = _upload_and_train(client, headers)
    duplicate = client.post("/lab/datasets", headers=headers, files={"file": ("renamed.csv", payload, "text/csv")}).json()
    assert duplicate["evaluation"]["protocol_id"] == run["evaluation"]["protocol_id"]
    adoption = client.post(f"/lab/datasets/{duplicate['id']}/mapping", headers=headers, json=mapping)
    assert adoption.status_code == 200, adoption.text
    changed = deepcopy(mapping)
    changed["fields"].pop("device_ref")
    assert client.post(f"/lab/datasets/{first['id']}/mapping", headers=headers, json=changed).status_code == 409
    assert client.post(f"/lab/datasets/{duplicate['id']}/runs", headers=headers,
                       json={**config, "train_share": .7}).status_code == 409
    model = run["results"]["models"][0]
    assert client.post(f"/lab/runs/{run['id']}/save", headers=headers,
                       json={"model_key": model["key"], "threshold_index": model["default_index"]}).status_code == 200
    assert client.post(f"/lab/datasets/{duplicate['id']}/runs", headers=headers, json=config).status_code == 409


def test_mapping_and_run_contracts_are_database_immutable(client, auth_headers, db):
    headers = auth_headers(UserRole.ROLE_ANALYST)
    dataset, mapping, _, run, _ = _upload_and_train(client, headers)
    for statement, params, error in [
        ("UPDATE lab_dataset SET mapping = '{}'::jsonb WHERE id=:id", {"id": dataset["id"]}, "LAB_MAPPING_LOCKED"),
        ("UPDATE lab_dataset SET raw_file = 'changed'::bytea WHERE id=:id", {"id": dataset["id"]}, "LAB_SOURCE_IMMUTABLE"),
        ("UPDATE lab_run SET config = '{}'::jsonb WHERE id=:id", {"id": run["id"]}, "LAB_RUN_IMMUTABLE"),
        ("UPDATE lab_model SET default_index=0 WHERE run_id=:id", {"id": run["id"]}, "LAB_CANDIDATE_IMMUTABLE"),
    ]:
        with pytest.raises(DBAPIError, match=error):
            db.execute(text(statement), params)
        db.rollback()
    assert client.post(f"/lab/datasets/{dataset['id']}/mapping", headers=headers, json=mapping).status_code == 200


def test_concurrent_saves_reveal_only_one_choice(client, auth_headers, db):
    headers = auth_headers(UserRole.ROLE_ANALYST)
    _, _, _, run, _ = _upload_and_train(client, headers)
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    model = run["results"]["models"][0]
    first = model["default_index"]
    second = (first + 1) % len(model["validation_curve"])
    barrier = Barrier(2)

    def save(index):
        with SessionLocal() as session:
            source_run = session.get(LabRun, run["id"])
            actor = session.get(User, user_id)
            barrier.wait(timeout=10)
            try:
                version, _ = lab.save_draft(session, source_run, model_key=model["key"], threshold_index=index, actor=actor)
                return ("saved", str(version.id))
            except lab.LabConflict:
                session.rollback()
                return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, (first, second)))
    assert sorted(status for status, _ in results) == ["conflict", "saved"]
    db.expire_all()
    protocol = db.scalar(select(LabEvaluationProtocol).where(LabEvaluationProtocol.id == run["evaluation"]["protocol_id"]))
    assert protocol.final_model_version_id is not None
    with pytest.raises(DBAPIError, match="LAB_FINAL_CHOICE_IMMUTABLE"):
        db.execute(text("UPDATE lab_evaluation_protocol SET final_threshold_index=final_threshold_index+1 WHERE id=:id"),
                   {"id": protocol.id})
    db.rollback()


def test_monitoring_uses_approved_snapshot_and_refuses_legacy_mutable_mapping(client, auth_headers, db):
    headers = auth_headers(UserRole.ROLE_ANALYST)
    dataset, mapping, _, run, _ = _upload_and_train(client, headers)
    source = db.get(LabDataset, dataset["id"])
    model = run["results"]["models"][0]
    saved = client.post(f"/lab/runs/{run['id']}/save", headers=headers,
                        json={"model_key": model["key"], "threshold_index": model["default_index"]}).json()
    version = db.get(ModelVersion, saved["model_version_id"])
    version.state = RuleState.ACTIVE
    db.commit()
    assert lab._monitor_mapping(db, source) == mapping
    # The native state test is not bypassed: unrelated dataset layouts cannot
    # be interpreted using a different model's semantics.
    other = client.post("/lab/datasets", headers=headers,
                        files={"file": ("other.csv", make_dataset(1600, seed=88), "text/csv")}).json()
    with pytest.raises(lab.LabConflict, match="bukan sumber"):
        lab._monitor_mapping(db, db.get(LabDataset, other["id"]))
    # An old card with no full date/currency mapping is not silently inferred.
    transient = ModelVersion(model_card={"training_data": {"dataset_id": str(source.id),
                                                           "fields": mapping["fields"]}})
    class LegacySession:
        def scalars(self, query):
            return [transient]
    with pytest.raises(lab.LabConflict, match="snapshot"):
        lab._monitor_mapping(LegacySession(), source)


def test_migration_marks_old_runs_exposed_without_changing_saved_model_state(
    client, auth_headers, db, alembic_config
):
    headers = auth_headers(UserRole.ROLE_ANALYST)
    content = make_dataset(600)
    dataset = client.post("/lab/datasets", headers=headers,
                          files={"file": ("legacy.csv", content, "text/csv")}).json()
    proposed = dataset["suggestion"]
    mapping = {key: proposed[key] for key in
               ("fields", "label_column", "positive_values", "category_column", "date_format")}
    assert client.post(f"/lab/datasets/{dataset['id']}/mapping", headers=headers, json=mapping).status_code == 200
    run_id, correlation = uuid.uuid4(), uuid.uuid4()
    version_name = f"lab_logistic_regression_{run_id.hex[:8]}_t0"
    db.rollback()
    command.downgrade(alembic_config, "0013_seed_pattern_rules")
    try:
        legacy_results = {"models": [{"key": "logistic_regression", "test": {"precision": 1.0},
                                      "test_by_category": [{"category": "old", "caught": 9}]}],
                          "positive_rate_test": .5, "split": {"test": {"rows": 20, "positives": 10}}}
        db.execute(text("INSERT INTO lab_run (id,dataset_id,config,results,seconds,correlation_id) "
                        "VALUES (:id,:dataset,CAST(:config AS jsonb),CAST(:results AS jsonb),0.1,:correlation)"),
                   {"id": run_id, "dataset": dataset["id"], "config": json.dumps({"models": ["logistic_regression"]}),
                    "results": json.dumps(legacy_results), "correlation": correlation})
        version = ModelVersion(artefact_type="LAB_LOGISTIC_JSON", version=version_name,
                               definition_hash="0" * 64, state=RuleState.DRAFT, unit="FRAUD",
                               feature_set_version="legacy", threshold=.5, model_text="legacy text",
                               model_card={"lab_run_id": str(run_id), "lab_model_key": "logistic_regression",
                                           "model": {"algorithm": "Logistic Regression"},
                                           "validation": {"at_threshold": {"precision": .5}},
                                           "test": {"at_threshold": {"precision": 1.0}}},
                               correlation_id=correlation)
        db.add(version)
        db.commit()
        version_id = version.id
    finally:
        db.rollback()
        command.upgrade(alembic_config, "head")
    db.expire_all()
    assert db.get(ModelVersion, version_id).state == RuleState.DRAFT
    protocol = db.scalar(select(LabEvaluationProtocol)
                         .where(LabEvaluationProtocol.source_file_sha256 == dataset["file_sha256"]))
    assert protocol.legacy_exposed and protocol.final_run_id is None
    shown = client.get(f"/lab/runs/{run_id}", headers=headers)
    assert shown.status_code == 200, shown.text
    assert shown.json()["evaluation"]["state"] == "LEGACY_EXPOSED"
    assert "test" not in shown.json()["results"]["models"][0]
    assert "positive_rate_test" not in shown.json()["results"]
    assert "positives" not in shown.json()["results"]["split"]["test"]
    saved = client.post(f"/lab/runs/{run_id}/save", headers=headers,
                        json={"model_key": "logistic_regression", "threshold_index": 0})
    assert saved.status_code == 200 and saved.json()["already_saved"]
    refused = client.post(f"/lab/runs/{run_id}/save", headers=headers,
                          json={"model_key": "logistic_regression", "threshold_index": 1})
    assert refused.status_code == 409
    assert client.post(f"/lab/datasets/{dataset['id']}/runs", headers=headers,
                       json={"feature_groups": GROUPS, "models": ["logistic_regression"], "train_share": .6}).status_code == 409
