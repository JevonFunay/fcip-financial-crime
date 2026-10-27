"""Model lab prototype: mapping, training protocol, API flow, roles and the DRAFT registry entry."""

import csv
import io
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.ml.behavioral.features import feature_names
from app.ml.lab.features import FEATURE_GROUPS
from app.ml.lab.mapping import LabDataError, apply_mapping, read_csv_bytes, suggest
from app.ml.lab.trainer import chronological_split, train_run
from app.models.audit_log import AuditLog
from app.models.enums import RuleState, UserRole
from app.models.ml import ModelVersion

ALL_GROUPS = [g["key"] for g in FEATURE_GROUPS]
ALL_MODELS = ["logistic_regression", "lightgbm", "isolation_forest"]
WIB = timezone(timedelta(hours=7))


def make_dataset(rows: int = 3000, *, seed: int = 7, defects: bool = True) -> bytes:
    """A small labelled dataset with a learnable signal, in a bank-like column layout."""
    rng = np.random.default_rng(seed)
    start = datetime(2026, 4, 1, tzinfo=WIB)
    records = []
    for i in range(rows):
        account = f"ACC-{rng.integers(1, 150):04d}"
        positive = rng.random() < 0.05
        amount = float(np.round(rng.lognormal(11, 0.6) * (25 if positive else 1), 2))
        moment = start + timedelta(days=float(rng.uniform(0, 150)))
        records.append({
            "TxnID": f"T{i:06d}", "AccountID": account, "Timestamp": moment.isoformat(timespec="seconds"),
            "Amount": f"{amount:.2f}", "Direction": "OUT" if positive or rng.random() < 0.5 else "IN",
            "Channel": "WEB" if positive else rng.choice(["APP", "APP", "WEB"]),
            "DeviceID": f"DEV-NEW-{i}" if positive else f"DEV-{account}", "Currency": "IDR",
            "is_fraud": "yes" if positive else "no", "pattern": "X1" if positive else "",
        })
    if defects:
        records.append({**records[0], "TxnID": "T-BAD-DATE", "Timestamp": "2026-13-45"})
        records.append({**records[1]})  # duplicate transaction id: quarantined
        records.append({**records[2], "TxnID": "T-NO-LABEL", "is_fraud": ""})
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(records[0]))
    writer.writeheader()
    writer.writerows(records)
    return out.getvalue().encode()


def mapping_for(raw: pd.DataFrame) -> dict:
    s = suggest(raw)
    return {"fields": s["fields"], "label_column": s["label_column"], "positive_values": s["positive_values"],
            "category_column": s["category_column"], "date_format": s["date_format"]}


def test_every_fraud_feature_belongs_to_exactly_one_group() -> None:
    grouped = [f for g in FEATURE_GROUPS for f in g["features"]]
    assert sorted(grouped) == sorted(feature_names("FRAUD"))
    assert len(grouped) == len(set(grouped))


def test_suggestions_map_columns_label_and_positive_values() -> None:
    raw = read_csv_bytes(make_dataset(200, defects=False))
    s = suggest(raw)
    assert s["fields"]["transaction_ref"] == "TxnID"
    assert s["fields"]["account_number"] == "AccountID"
    assert s["fields"]["transaction_date"] == "Timestamp"
    assert s["fields"]["amount"] == "Amount"
    assert s["fields"]["direction"] == "Direction"
    assert s["label_column"] == "is_fraud"
    assert s["positive_values"] == ["yes"]
    assert s["category_column"] == "pattern"
    assert "is_fraud" not in s["fields"].values()


def test_a_debit_credit_type_column_is_suggested_as_direction() -> None:
    raw = pd.DataFrame({"TransactionID": ["a", "b"], "AccountID": ["1", "2"], "TransactionDate": ["2026-01-01"] * 2,
                        "TransactionAmount": ["1.00", "2.00"], "TransactionType": ["Debit", "Credit"]})
    s = suggest(raw)
    assert s["fields"]["direction"] == "TransactionType"
    assert "transaction_type" not in s["fields"]


def test_mapping_quarantines_like_ingestion_and_reads_labels() -> None:
    raw = read_csv_bytes(make_dataset(400))
    mapped = apply_mapping(raw, mapping_for(raw))
    report = mapped.report
    assert report["total_rows"] == 403
    assert report["quarantined"] == 2  # invalid date + duplicate id
    assert {r["reason"] for r in report["quarantine_reasons"]} == {
        "transaction_date is invalid for the schema profile", "duplicate transaction_ref within source"}
    assert report["missing_label"] == 1
    assert report["usable_rows"] == 400
    assert report["positives"] == int((raw.is_fraud.iloc[:400] == "yes").sum())
    assert set(mapped.labels) <= {0.0, 1.0}
    assert set(mapped.frame.direction) <= {"CREDIT", "DEBIT"}
    assert report["category_counts"] == {"X1": report["positives"]}


def test_missing_optional_fields_get_recorded_neutral_values() -> None:
    raw = read_csv_bytes(make_dataset(1500, defects=False))
    mapping = mapping_for(raw)
    for key in ("transaction_ref", "direction", "channel", "currency"):
        mapping["fields"].pop(key)
    mapping["default_currency"] = "USD"
    mapped = apply_mapping(raw, mapping)
    assert set(mapped.frame.direction) == {"DEBIT"} and set(mapped.frame.channel) == {"UNKNOWN"}
    assert set(mapped.frame.currency) == {"USD"}
    assert mapped.frame.transaction_ref.str.startswith("ROW-").all()
    assert mapped.report["ready"]
    assert [note.split(" ")[0] for note in mapped.report["notes"]] == ["ID", "Arah", "Kanal", "Mata"]


@pytest.mark.parametrize("change, message", [
    (lambda m: m["fields"].update(amount="is_fraud"), "kebocoran"),
    (lambda m: m["fields"].pop("amount"), "wajib"),
    (lambda m: m.update(positive_values=[]), "positif"),
    (lambda m: m["fields"].update(channel="NoSuchColumn"), "tidak ada"),
])
def test_invalid_mappings_are_refused_with_a_reason(change, message) -> None:
    raw = read_csv_bytes(make_dataset(100, defects=False))
    mapping = mapping_for(raw)
    change(mapping)
    with pytest.raises(LabDataError, match=message):
        apply_mapping(raw, mapping)


def test_chronological_split_keeps_test_after_validation_after_train() -> None:
    raw = read_csv_bytes(make_dataset(600, defects=False))
    mapped = apply_mapping(raw, mapping_for(raw))
    part = chronological_split(mapped.frame, 0.6)
    dates = mapped.frame.transaction_date
    assert dates[part == "train"].max() <= dates[part == "validation"].min()
    assert dates[part == "validation"].max() <= dates[part == "test"].min()
    assert abs((part == "train").mean() - 0.6) < 0.01


def test_training_compares_models_and_keeps_test_out_of_threshold_choice() -> None:
    raw = read_csv_bytes(make_dataset(3000, defects=False))
    mapped = apply_mapping(raw, mapping_for(raw))
    outcome = train_run(mapped.frame, mapped.labels, mapped.categories, feature_groups=ALL_GROUPS,
                        model_keys=ALL_MODELS, train_share=0.6)
    results = outcome.results
    assert [m["key"] for m in results["models"]] == ALL_MODELS
    assert results["best_model"] in ALL_MODELS
    for summary, model in zip(results["models"], outcome.models):
        curve = summary["validation_curve"]
        assert len(curve) == len(model.thresholds) == len(model.test_curve)
        assert [p["threshold"] for p in curve] == sorted(p["threshold"] for p in curve)
        # The default threshold is the validation max-F1 point.
        assert curve[summary["default_index"]]["f1"] == pytest.approx(max(p["f1"] for p in curve))
        assert summary["test"]["threshold"] == model.thresholds[summary["default_index"]]
        assert "test_curve" not in summary  # never shipped with the comparison
    lightgbm = next(m for m in results["models"] if m["key"] == "lightgbm")
    assert lightgbm["test"]["average_precision"] > 0.5  # the planted signal is learnable
    assert results["features"]["unusable"]  # e.g. login attempts are absent from this dataset


def test_a_split_without_positives_is_refused() -> None:
    raw = read_csv_bytes(make_dataset(600, defects=False))
    raw.loc[raw.Timestamp > "2026-07-15", "is_fraud"] = "no"  # no positives in the latest period
    mapped = apply_mapping(raw, mapping_for(raw))
    with pytest.raises(ValueError, match="positif dan negatif"):
        train_run(mapped.frame, mapped.labels, None, feature_groups=ALL_GROUPS, model_keys=["lightgbm"],
                  train_share=0.6)


def test_lab_api_flow_saves_a_draft_model_version(
    client: TestClient, auth_headers: Callable[[UserRole], dict[str, str]], db: Session
) -> None:
    analyst = auth_headers(UserRole.ROLE_ANALYST)
    options = client.get("/lab/options", headers=analyst).json()
    assert {m["key"] for m in options["models"]} == set(ALL_MODELS)

    upload = client.post("/lab/datasets", headers=analyst, data={"name": "uji lab"},
                         files={"file": ("lab.csv", make_dataset(2500), "text/csv")})
    assert upload.status_code == 201, upload.text
    dataset = upload.json()
    assert dataset["status"] == "UPLOADED" and dataset["row_count"] == 2503
    assert dataset["suggestion"]["label_column"] == "is_fraud"

    early = client.post(f"/lab/datasets/{dataset['id']}/runs", headers=analyst,
                        json={"feature_groups": ALL_GROUPS, "models": ["lightgbm"]})
    assert early.status_code == 409  # mapping first

    s = dataset["suggestion"]
    mapped = client.post(f"/lab/datasets/{dataset['id']}/mapping", headers=analyst, json={
        "fields": s["fields"], "label_column": s["label_column"], "positive_values": s["positive_values"],
        "category_column": s["category_column"], "date_format": s["date_format"]})
    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["status"] == "MAPPED" and mapped.json()["validation"]["ready"]

    trained = client.post(f"/lab/datasets/{dataset['id']}/runs", headers=analyst,
                          json={"feature_groups": ALL_GROUPS, "models": ALL_MODELS, "train_share": 0.6})
    assert trained.status_code == 201, trained.text
    run = trained.json()
    assert run["saved_versions"] == []
    model = next(m for m in run["results"]["models"] if m["key"] == "lightgbm")

    out_of_range = client.post(f"/lab/runs/{run['id']}/save", headers=analyst,
                               json={"model_key": "lightgbm", "threshold_index": 10_000})
    assert out_of_range.status_code == 422
    saved = client.post(f"/lab/runs/{run['id']}/save", headers=analyst,
                        json={"model_key": "lightgbm", "threshold_index": model["default_index"]})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["state"] == "DRAFT" and not body["already_saved"]
    assert body["validation"] == model["validation_curve"][model["default_index"]]
    again = client.post(f"/lab/runs/{run['id']}/save", headers=analyst,
                        json={"model_key": "lightgbm", "threshold_index": model["default_index"]})
    assert again.json()["already_saved"] and again.json()["version"] == body["version"]

    version = db.scalar(select(ModelVersion).where(ModelVersion.version == body["version"]))
    assert version.state == RuleState.DRAFT and version.artefact_type == "LAB_LIGHTGBM_TEXT"
    assert version.model_card["training_data"]["file_sha256"] == dataset["file_sha256"]
    listed = next(m for m in client.get("/ml/models", headers=analyst).json() if m["version"] == body["version"])
    assert listed["training_kind"] == "supervised" and listed["currency"] == "IDR"
    assert listed["validation"]["precision"] == model["validation_curve"][model["default_index"]]["precision"]
    assert client.get(f"/lab/runs/{run['id']}", headers=analyst).json()["saved_versions"][0]["model_key"] == "lightgbm"

    # The registry's own guard still applies: a saved version cannot be edited.
    with pytest.raises(DBAPIError, match="MODEL_VERSION_IMMUTABLE"):
        db.execute(text("UPDATE model_version SET threshold = 0.5 WHERE id = :id"), {"id": version.id})
    db.rollback()

    events = db.scalars(select(AuditLog.object_type).where(AuditLog.correlation_id == version.correlation_id)).all()
    assert {"LAB_DATASET", "LAB_RUN", "MODEL_VERSION"} <= set(events)


def test_lab_roles_and_bad_files(client: TestClient, auth_headers: Callable[[UserRole], dict[str, str]]) -> None:
    triage = auth_headers(UserRole.ROLE_TRIAGE)
    assert client.post("/lab/datasets", headers=triage,
                       files={"file": ("x.csv", make_dataset(50), "text/csv")}).status_code == 403
    assert client.get("/lab/datasets", headers=triage).status_code == 200
    data_ops = auth_headers(UserRole.ROLE_DATA_OPS)
    bad = client.post("/lab/datasets", headers=data_ops, files={"file": ("x.csv", b"\xff\xfe\x00bad", "text/csv")})
    assert bad.status_code == 422
    assert client.get("/lab/datasets/00000000-0000-0000-0000-000000000000", headers=triage).status_code == 404
