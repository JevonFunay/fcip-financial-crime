"""Portable pipeline contracts: fit isolation, post-fit test and upload records.

These checks build their own 15-column source and never read user datasets or
connect to the database. Synthetic labels assess interventions, not crime.
"""

import csv
import hashlib
import io
import json
from decimal import Decimal
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from app.ml.behavioral import generate as generator
from app.ml.behavioral import pipeline
from app.ml.behavioral.features import feature_names
from app.ml.behavioral.standardize import CANONICAL_COLUMNS


@pytest.fixture
def portable_source(tmp_path):
    records = []
    for account in range(40):
        for transaction in range(12):
            records.append({
                "TransactionID": f"TX{account:03d}{transaction:03d}", "AccountID": f"AC{account:03d}",
                "TransactionAmount": f"{1 + account * 4 + transaction * 3:.2f}",
                "TransactionDate": f"1/{transaction + 1}/2023" + (" 12:30" if transaction % 2 else ""),
                "TransactionType": "Debit" if transaction % 3 else "Credit",
                "Location": "Kota, 日本\nUtara" if account % 3 else "Yogyakarta",
                "DeviceID": f"D{account}", "IP Address": "192.0.2.1", "MerchantID": f"M{transaction % 5}",
                "Channel": ["Online", "ATM", "Branch"][transaction % 3], "CustomerAge": str(25 + account),
                "CustomerOccupation": "Engineer", "TransactionDuration": str(20 + transaction * 5),
                "LoginAttempts": str(1 + (transaction == 11)),
                "AccountBalance": f"{100 + account * 20 + transaction:.2f}",
            })
    path = tmp_path / "portable_source.csv"
    pd.DataFrame(records, columns=generator.SOURCE_COLUMNS).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def test_pipeline_fits_without_answers_and_generates_test_after_frozen_models(portable_source, tmp_path, monkeypatch):
    out, models = tmp_path / "datasets", tmp_path / "models"
    partitions = generator.source_partitions(portable_source)
    source_before = portable_source.read_bytes()
    real_read_csv, real_fit = pd.read_csv, pipeline.fit_model
    real_train, real_generate = pipeline.train_models, generator.generate
    real_vocabulary = pipeline.fit_vocabulary
    fit_calls, truth_reads, vocabulary_accounts, hashes_at_test = [], [], [], {}
    in_training = False

    def guarded_reader(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path).name == "ground_truth.csv":
            assert not in_training, "Training must never read evaluation answers"
            assert len(fit_calls) == 2
            assert all((models / version / "model.txt").is_file() for version in pipeline.MODEL_NAMES.values())
            truth_reads.append(Path(path).parent.name)
        return real_read_csv(path, *args, **kwargs)

    def guarded_fit(frame, names, **kwargs):
        assert in_training and not (out / "test").exists()
        assert names in [feature_names("AML"), feature_names("FRAUD")]
        assert not {"label", "target", "scenario", "scenario_id", "source_ordinal"} & set(frame.columns)
        fit_calls.append(tuple(names))
        return real_fit(frame, names, **kwargs)

    def guarded_vocabulary(frame):
        assert in_training
        assert not {"label", "target", "scenario", "scenario_id"} & set(frame.columns)
        accounts = set(frame.account_number)
        assert not accounts & (set(partitions["validation"]) | set(partitions["test"]))
        assert accounts & set(partitions["train"]) == set(partitions["train"])
        vocabulary_accounts.append(accounts)
        return real_vocabulary(frame)

    def guarded_training(*args, **kwargs):
        nonlocal in_training
        in_training = True
        try:
            return real_train(*args, **kwargs)
        finally:
            in_training = False

    def observed_generation(source, destination, split, *args, **kwargs):
        if split == "test":
            assert len(fit_calls) == 2 and truth_reads == ["validation"]
            for version in pipeline.MODEL_NAMES.values():
                folder = models / version
                card = json.loads((folder / "model_card.json").read_text())
                assert "synthetic_stress" in card["validation"]
                hashes_at_test[version] = {name: pipeline.sha256(folder / name)
                                           for name in ("model.txt", "model_card.json")}
        return real_generate(source, destination, split, *args, **kwargs)

    monkeypatch.setattr(pd, "read_csv", guarded_reader)
    monkeypatch.setattr(pipeline, "fit_model", guarded_fit)
    monkeypatch.setattr(pipeline, "fit_vocabulary", guarded_vocabulary)
    monkeypatch.setattr(pipeline, "train_models", guarded_training)
    monkeypatch.setattr(generator, "generate", observed_generation)
    report = pipeline.run(portable_source, out, models, train_rows=1000, validation_rows=1000, test_rows=1000,
                          seed=20261015, reference_date=date(2026, 10, 15), estimators=2, max_samples=16)

    assert portable_source.read_bytes() == source_before
    assert len(fit_calls) == 2 and len(vocabulary_accounts) == 1
    assert truth_reads == ["validation", "test"]
    assert report["test_generated_after_fit"] is True
    assert datetime.fromisoformat(report["models_frozen_at"]) <= datetime.fromisoformat(report["test_generation_started_at"])
    assert datetime.fromisoformat(report["test_generation_started_at"]) <= datetime.fromisoformat(report["completed_at"])
    assert report["frozen_model_checksums"] == hashes_at_test
    assert report["test_manifest_sha256"] == pipeline.sha256(out / "test" / "manifest.json")
    assert report["fit_uses_targets"] is False and report["threshold_tuned_on_labels"] is False
    assert report["generated_rows"] == {"train": 1000, "validation": 1000, "test": 1000}
    parents, generated_accounts = {}, {}
    for split in ("train", "validation", "test"):
        lineage = real_read_csv(out / split / "lineage.csv")
        parents[split] = set(lineage.source_account_id)
        generated_accounts[split] = set(lineage.account_number)
        assert parents[split] <= set(partitions[split])
        assert len(lineage) == 1000
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        assert parents[left].isdisjoint(parents[right])
        assert generated_accounts[left].isdisjoint(generated_accounts[right])
    assert generated_accounts["train"] < vocabulary_accounts[0]
    for unit, version in pipeline.MODEL_NAMES.items():
        folder = models / version
        assert {name: pipeline.sha256(folder / name) for name in hashes_at_test[version]} == hashes_at_test[version]
        card = json.loads((folder / "model_card.json").read_text())
        assert card["training_data"]["targets_used"] is False
        assert card["training_data"]["fit_input_rows"] == 1000 + 12 * len(partitions["train"])
        assert card["model"]["threshold"] == pipeline.DEFAULT_THRESHOLD
        unlabelled = report["evaluation"]["source_test_unlabelled"]["models"][unit]
        assert unlabelled["precision"] is None and unlabelled["recall"] is None
        assert report["evaluation"]["test"]["models"][unit]["synthetic_stress"]["label_scope"].startswith("Synthetic")
    assert report["web_uploads"]["total_rows"] == 1000
    assert report["web_uploads"]["max_bytes"] == 50 * 1024 * 1024
    assert json.loads((out / "evaluation.json").read_text())["frozen_model_checksums"] == hashes_at_test
    with pytest.raises(FileExistsError, match="existing datasets"):
        pipeline.run(portable_source, out, models, train_rows=1000, validation_rows=1000, test_rows=1000,
                     seed=20261015, reference_date=date(2026, 10, 15), estimators=2, max_samples=16)


def _csv_bytes(records):
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerows(records)
    return stream.getvalue().encode("utf-8")


def test_upload_parts_preserve_complete_unicode_records_and_observation_metadata(tmp_path):
    header = list(CANONICAL_COLUMNS)
    records = []
    for i in range(7):
        row = {column: "" for column in header}
        row.update(transaction_ref=f"TX{i}", account_number="AC1", amount="12.34", currency="USD",
                   direction="DEBIT", channel="ONLINE", transaction_date="2023-04-11T12:30:00+00:00",
                   timestamp_precision="minute", source_timezone_known="False",
                   description='Pembayaran, naïve "kutipan"\nbaris 日本 kedua', location="Yogyakarta")
        records.append([row[column] for column in header])
    source = tmp_path / "canonical.csv"
    source.write_bytes(b"\xef\xbb\xbf" + _csv_bytes([header, *records]))
    cap = len(_csv_bytes([header, records[0], records[1]]))
    destination = tmp_path / "web"
    manifest = pipeline.publish_upload_parts(source, destination, max_bytes=cap)
    assert len(manifest["parts"]) == 4
    assert manifest["total_rows"] == len(records) and manifest["source_sha256"] == pipeline.sha256(source)
    reassembled = []
    for part in manifest["parts"]:
        payload = (destination / part["file"]).read_bytes()
        assert len(payload) == part["bytes"] <= cap
        assert hashlib.sha256(payload).hexdigest() == part["sha256"]
        parsed = list(csv.reader(io.StringIO(payload.decode("utf-8"), newline="")))
        assert parsed[0] == header and len(parsed) - 1 == part["rows"]
        assert payload.count(b"\n") > len(parsed)  # Embedded newlines are kept inside complete records.
        reassembled.extend(parsed[1:])
    assert reassembled == records
    assert json.loads((destination / "upload_manifest.json").read_text()) == manifest
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        pipeline.publish_upload_parts(source, destination, max_bytes=cap)
    assert list(csv.reader(io.StringIO(source.read_text(encoding="utf-8-sig"), newline=""))) == [header, *records]


def test_single_upload_record_larger_than_cap_is_rejected(tmp_path):
    source = tmp_path / "large_record.csv"
    source.write_bytes(_csv_bytes([["transaction_ref", "description"], ["TX1", "日本\n" * 20]]))
    destination = tmp_path / "too_large"
    with pytest.raises(ValueError, match="One CSV record exceeds"):
        pipeline.publish_upload_parts(source, destination, max_bytes=80)
    assert not (destination / "upload_manifest.json").exists()


def test_weekly_labels_follow_sunday_end_and_feature_order():
    observations = pd.DataFrame({
        "transaction_ref": ["sunday", "monday", "other"], "account_number": ["AC1", "AC1", "AC2"],
        "transaction_date": pd.to_datetime(["2023-01-08T23:59:59Z", "2023-01-09T00:00:00Z", "2023-01-08T12:00:00Z"], utc=True),
        "amount": [1.0, 20.0, 4.0],
    })
    truth = pd.DataFrame({"transaction_ref": ["monday", "other", "sunday"], "label": [0, 0, 1]})
    weekly = pd.DataFrame({"account_number": ["AC2", "AC1", "AC1"],
                           "as_of": pd.to_datetime(["2023-01-08T23:59:59Z", "2023-01-15T23:59:59Z", "2023-01-08T23:59:59Z"], utc=True)})
    labels = pipeline._evaluation_labels(observations, weekly, "AML", truth)
    assert labels.account_number.tolist() == weekly.account_number.tolist()
    assert labels.as_of.tolist() == weekly.as_of.tolist()
    assert labels.label.tolist() == [0, 0, 1]
    fraud = pd.DataFrame({"transaction_ref": ["other", "sunday", "monday"]})
    assert pipeline._evaluation_labels(observations, fraud, "FRAUD", truth).label.tolist() == [0, 1, 0]
    with pytest.raises(ValueError, match="align one-to-one"):
        pipeline._evaluation_labels(observations, weekly, "AML", truth.iloc[:-1])


def test_small_amount_report_includes_decimal_amount_exactly_at_float_cutoff(tmp_path, monkeypatch):
    folder = tmp_path / "models" / "fraud_boundary"
    folder.mkdir(parents=True)
    (folder / "model_card.json").write_text(json.dumps({
        "feature_set": {"vocabulary": {"small_amount_threshold": 83.44}},
        "model": {"threshold": 0.5},
    }))
    (folder / "model.txt").write_text("unused mock scorer")
    observations = pd.DataFrame({
        "transaction_ref": ["below", "boundary", "above"], "account_number": ["AC1"] * 3,
        "transaction_date": pd.to_datetime(["2023-01-08T12:00:00Z"] * 3, utc=True),
        "amount": [Decimal("83.43"), Decimal("83.44"), Decimal("83.45")],
    })
    truth = tmp_path / "ground_truth.csv"
    pd.DataFrame({"transaction_ref": observations.transaction_ref, "label": [0, 1, 1],
                  "scenario": ["background", "stress", "stress"], "scenario_id": ["B1", "S1", "S2"]}).to_csv(truth, index=False)
    monkeypatch.setattr(pipeline, "MODEL_NAMES", {"FRAUD": "fraud_boundary"})
    monkeypatch.setattr(pipeline, "build_features", lambda frame, *args: frame[["transaction_ref"]])
    monkeypatch.setattr(pipeline, "predict_model", lambda *args: __import__("numpy").array([0.1, 0.6, 0.9]))
    result = pipeline.evaluate(tmp_path / "models", observations, ground_truth=truth)["models"]["FRAUD"]["small_amount"]
    assert (result["tp"], result["fp"], result["fn"], result["tn"]) == (1, 0, 0, 1)
    assert result["precision"] == result["recall"] == 1.0
