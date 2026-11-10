"""Pure guards for the offline team-review exercise; no fitting or application DB."""

import csv
import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.ml.lab.features import build_matrix
from app.ml.lab.mapping import apply_mapping, read_csv_bytes, suggest
from app.ml.lab.trainer import TrainedModel, chronological_split
from app.scripts import train_team_review as study


@pytest.fixture
def prepared(tmp_path):
    source, imported = tmp_path / "source", tmp_path / "prepared"
    source.mkdir(); imported.mkdir()
    columns = ["transaction_ref", "account_number", "transaction_date", "amount", "currency",
               "direction", "channel", "is_fraud", "catatan_reviewer"]
    raw, keys, labelled = [], [], []
    start = pd.Timestamp("2026-07-01T00:00:00+07:00")
    for index in range(180):
        label = str(int(index % 3 == 0))
        scenario = "ATO-1" if label == "1" else "NORMAL"
        item = {"transaction_ref": f"T{index}", "account_number": f"A{index % 4}",
                "transaction_date": (start + pd.Timedelta(hours=index)).isoformat(),
                "amount": str(1_000 + index * 20), "currency": "IDR", "direction": "OUT",
                "channel": "WEB", "is_fraud": "", "catatan_reviewer": ""}
        raw.append(item)
        keys.append({"transaction_ref": item["transaction_ref"], "account_number": item["account_number"],
                     "label": label, "skenario": scenario, "penjelasan": "Excluded private reviewer explanation"})
        labelled.append({**item, "is_fraud": label, "pattern_code": re.sub(r"-\d+$", "", scenario)})

    def write(path, cols, rows):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=cols)
            writer.writeheader(); writer.writerows(rows)

    write(source / "transaksi_untuk_diflag.csv", columns, raw)
    write(source / "kunci_jawaban.csv", list(keys[0]), keys)
    (source / "make_flag_dataset.py").write_text("raise AssertionError('Supplied scripts must never execute')")
    data = imported / "training_labeled.csv"
    write(data, columns + ["pattern_code"], labelled)
    frame = read_csv_bytes(data.read_bytes())
    hint = suggest(frame)
    mapping = {key: hint[key] for key in
               ("fields", "label_column", "positive_values", "category_column", "date_format")}
    mapping["default_currency"] = "XXX"
    manifest = {"version": "team_review_import_v1", "input_directory": str(source),
                "source_hashes": {p.name: study.evidence.sha256(p) for p in source.iterdir()},
                "output_sha256": study.evidence.sha256(data), "source_rows": len(raw),
                "positive_rows": 60, "negative_rows": 120, "mapping": mapping,
                "label_provenance": "Team manual labels per user; independent review history not supplied."}
    path = imported / "manifest.json"
    path.write_text(json.dumps(manifest))
    return SimpleNamespace(data=data, manifest_path=path, manifest=manifest, source=source,
                           write=write, columns=columns, raw=raw, keys=keys, labelled=labelled,
                           pin=study.evidence.sha256(path))


def update_prepared_hash(packet):
    packet.manifest["output_sha256"] = study.evidence.sha256(packet.data)
    packet.manifest_path.write_text(json.dumps(packet.manifest))
    packet.pin = study.evidence.sha256(packet.manifest_path)


def update_key_hash(packet):
    key = packet.source / "kunci_jawaban.csv"
    packet.manifest["source_hashes"][key.name] = study.evidence.sha256(key)
    packet.manifest_path.write_text(json.dumps(packet.manifest))
    packet.pin = study.evidence.sha256(packet.manifest_path)


def test_original_join_and_declared_provenance_are_preserved(prepared):
    manifest, expected = study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)
    assert manifest["label_provenance"].startswith("Team manual labels per user")
    assert expected[str(prepared.data)] == prepared.manifest["output_sha256"]
    assert str(prepared.manifest_path) in expected
    assert str(prepared.source / "make_flag_dataset.py") in expected


def test_changed_prepared_bytes_fail_original_hash(prepared):
    prepared.data.write_text(prepared.data.read_text() + "\n")
    with pytest.raises(ValueError, match="original expected hash"):
        study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)


def test_changed_original_script_is_not_silently_rebased(prepared):
    (prepared.source / "make_flag_dataset.py").write_text("new source")
    with pytest.raises(ValueError, match="Original source differs"):
        study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)


def test_preparation_manifest_cannot_rebase_its_original_expectations(prepared):
    old_pin = prepared.pin
    (prepared.source / "make_flag_dataset.py").write_text("replacement")
    prepared.manifest["source_hashes"]["make_flag_dataset.py"] = study.evidence.sha256(prepared.source / "make_flag_dataset.py")
    prepared.manifest_path.write_text(json.dumps(prepared.manifest))
    with pytest.raises(ValueError, match="externally pinned"):
        study.verify_prepared(prepared.data, prepared.manifest_path, old_pin)


@pytest.mark.parametrize("mutation", ["inverted_labels", "channel_is_category", "wrong_label_column", "relabel_currency"])
def test_unapproved_mapping_cannot_change_the_learning_task(prepared, mutation):
    mapping = prepared.manifest["mapping"]
    if mutation == "inverted_labels":
        mapping["positive_values"] = ["0"]
    elif mutation == "channel_is_category":
        mapping["fields"]["channel"] = "pattern_code"
    elif mutation == "wrong_label_column":
        mapping["label_column"] = "catatan_reviewer"
    else:
        mapping["default_currency"] = "IDR"
    prepared.manifest_path.write_text(json.dumps(prepared.manifest))
    prepared.pin = study.evidence.sha256(prepared.manifest_path)
    with pytest.raises(ValueError, match="approved literal labels"):
        study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)


@pytest.mark.parametrize("bad", ["", "unknown", "2", "1.0"])
def test_unresolved_or_nonbinary_key_labels_are_refused(prepared, bad):
    prepared.keys[0]["label"] = bad
    prepared.write(prepared.source / "kunci_jawaban.csv", list(prepared.keys[0]), prepared.keys)
    update_key_hash(prepared)
    with pytest.raises(ValueError, match="literal 0 or 1"):
        study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)


def test_duplicate_review_ids_are_refused_even_with_a_declared_new_key_hash(prepared):
    prepared.keys[1]["transaction_ref"] = prepared.keys[0]["transaction_ref"]
    prepared.write(prepared.source / "kunci_jawaban.csv", list(prepared.keys[0]), prepared.keys)
    update_key_hash(prepared)
    with pytest.raises(ValueError, match="not unique"):
        study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)


def test_mismatched_key_account_is_refused(prepared):
    prepared.keys[0]["account_number"] = "ANOTHER_ACCOUNT"
    prepared.write(prepared.source / "kunci_jawaban.csv", list(prepared.keys[0]), prepared.keys)
    update_key_hash(prepared)
    with pytest.raises(ValueError, match="account_number differs"):
        study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)


def test_preparation_cannot_change_original_amount_or_supplied_labels(prepared):
    prepared.labelled[0]["amount"] = "99999999"
    prepared.write(prepared.data, prepared.columns + ["pattern_code"], prepared.labelled)
    update_prepared_hash(prepared)
    with pytest.raises(ValueError, match="changed a transaction field"):
        study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)


def test_categories_cannot_silently_claim_dormant_is_p05(prepared):
    prepared.labelled[0]["pattern_code"] = "P05"
    prepared.write(prepared.data, prepared.columns + ["pattern_code"], prepared.labelled)
    update_prepared_hash(prepared)
    with pytest.raises(ValueError, match="scenario family"):
        study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)


def test_missing_review_or_extra_transaction_is_refused(prepared):
    prepared.keys.pop()
    prepared.write(prepared.source / "kunci_jawaban.csv", list(prepared.keys[0]), prepared.keys)
    update_key_hash(prepared)
    with pytest.raises(ValueError, match="populations must match"):
        study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)


def test_key_explanations_cannot_be_joined_into_prepared_features(prepared):
    columns = prepared.columns + ["pattern_code", "penjelasan"]
    prepared.write(prepared.data, columns, [{**r, "penjelasan": "label encoded in prose"} for r in prepared.labelled])
    update_prepared_hash(prepared)
    with pytest.raises(ValueError, match="category metadata only"):
        study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)


def test_manifest_requires_provenance_and_safe_source_names(prepared):
    prepared.manifest["label_provenance"] = ""
    prepared.manifest_path.write_text(json.dumps(prepared.manifest))
    prepared.pin = study.evidence.sha256(prepared.manifest_path)
    with pytest.raises(ValueError, match="label provenance"):
        study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)
    prepared.manifest["label_provenance"] = "User supplied"
    prepared.manifest["source_hashes"]["../other.py"] = "not-a-valid-source"
    prepared.manifest_path.write_text(json.dumps(prepared.manifest))
    prepared.pin = study.evidence.sha256(prepared.manifest_path)
    with pytest.raises(ValueError, match="filenames, not paths"):
        study.verify_prepared(prepared.data, prepared.manifest_path, prepared.pin)


def test_preselection_test_outputs_are_rejected():
    candidate = SimpleNamespace(test_curve=[])
    outcome = SimpleNamespace(results={"models": [{"key": "lightgbm"}], "split": {"test": {}}}, models=[candidate])
    study.assert_validation_only(outcome)
    outcome.results["models"][0]["test"] = {}
    with pytest.raises(ValueError, match="validation results only"):
        study.assert_validation_only(outcome)
    outcome.results["models"][0].pop("test")
    candidate.test_curve = [{}]
    with pytest.raises(ValueError, match="before selection"):
        study.assert_validation_only(outcome)


@pytest.fixture
def fake_study(prepared, monkeypatch, tmp_path):
    code = tmp_path / "code.py"; code.write_text("fixed fake code")
    monkeypatch.setattr(study, "BACKEND", tmp_path)
    monkeypatch.setattr(study, "CODE_PATHS", ["code.py"])
    mapped = apply_mapping(read_csv_bytes(prepared.data.read_bytes()), prepared.manifest["mapping"])
    parts = chronological_split(mapped.frame, study.TRAIN_SHARE)
    _, vocab = build_matrix(mapped.frame, parts == "train")
    models = [TrainedModel(name, "FAKE_TEST_ONLY", f"fixed {name}", ["AMOUNT"], [.5], [], 0)
              for name in study.MODEL_KEYS]
    results = {"models": [{"key": name, "default_index": 0,
                            "validation_ranking": {"average_precision": ap},
                            "validation_curve": [{"threshold": .5, "f1": ap}]}
                           for name, ap in zip(study.MODEL_KEYS, [.7, .9, .2])],
               "best_model": "lightgbm", "split": {"test": {"rows": int((parts == "test").sum())}},
               "features": {"vocabulary": vocab, "used": ["AMOUNT"]}}
    events = []
    def fake_train(*args, **kwargs):
        events.append("train")
        assert kwargs["train_share"] == .7
        assert kwargs["model_keys"] == study.MODEL_KEYS
        return SimpleNamespace(results=results, models=models)
    monkeypatch.setattr(study, "train_run", fake_train)
    out = tmp_path / "study"
    def fake_score(key, text, matrix):
        events.append(f"score_{key}")
        assert key == "lightgbm" and text == "fixed lightgbm"
        assert len(matrix) == int((parts == "test").sum())
        frozen = json.loads((out / "frozen.json").read_text())
        assert frozen["selection"]["selected_model"] == key
        assert frozen["test_predictions_computed"] is False
        return matrix.AMOUNT.to_numpy(dtype=float) / 5_000
    monkeypatch.setattr(study, "score", fake_score)
    return SimpleNamespace(packet=prepared, out=out, events=events, code=code, results=results, models=models)


def test_only_frozen_validation_winner_scores_test_once(fake_study):
    f = fake_study
    report = study.run(f.packet.data, f.out, expected_manifest_sha256=f.packet.pin)
    assert f.events == ["train", "score_lightgbm"]
    assert report["test_scored_model_keys"] == ["lightgbm"]
    assert report["freeze_precedes_test_scoring"]
    assert not report["registered_or_activated"] and not report["application_database_accessed"]
    assert report["protocol"]["test_metadata_already_inspected"]
    study.evidence.verify_frozen(report["frozen"], f.out / "frozen.json", report["frozen_manifest_sha256"])
    cache = pd.read_parquet(f.out / "selected_test_scores.parquet")
    assert set(cache.columns) == {"transaction_ref", "account_number", "transaction_date", "amount", "channel",
                                  "label", "category", "score", "flagged"}
    assert len(cache) == report["selected_test"]["all"]["rows"]


def test_existing_study_is_never_overwritten(fake_study):
    f = fake_study; f.out.mkdir(); (f.out / "keep").write_text("unchanged")
    with pytest.raises(FileExistsError, match="immutable"):
        study.run(f.packet.data, f.out, expected_manifest_sha256=f.packet.pin)
    assert f.events == [] and (f.out / "keep").read_text() == "unchanged"


def test_protected_code_change_during_training_stops_before_test(fake_study, monkeypatch):
    f = fake_study
    def changed(*args, **kwargs):
        f.events.append("train")
        f.code.write_text("changed during fitting")
        return SimpleNamespace(results=f.results, models=f.models)
    monkeypatch.setattr(study, "train_run", changed)
    with pytest.raises(ValueError, match="Protected file changed"):
        study.run(f.packet.data, f.out, expected_manifest_sha256=f.packet.pin)
    assert f.events == ["train"]
    assert not (f.out / "selected_test_scores.parquet").exists()


def test_reblessing_manifest_after_freeze_is_detected(fake_study, monkeypatch):
    f = fake_study; original = study.evidence.verify_frozen; checked = []
    def tamper(frozen, path, expected):
        checked.append(True)
        if len(checked) == 2:
            replacement = json.loads(path.read_text()); replacement["selection"]["selected_model"] = "logistic_regression"
            path.write_text(json.dumps(replacement))
        return original(frozen, path, expected)
    monkeypatch.setattr(study.evidence, "verify_frozen", tamper)
    with pytest.raises(ValueError, match="Frozen manifest changed"):
        study.run(f.packet.data, f.out, expected_manifest_sha256=f.packet.pin)
    assert f.events == ["train"]


def test_nonfinite_selected_score_is_never_saved(fake_study, monkeypatch):
    f = fake_study
    monkeypatch.setattr(study, "score", lambda key, text, matrix: np.full(len(matrix), np.nan))
    with pytest.raises(ValueError, match="present and finite"):
        study.run(f.packet.data, f.out, expected_manifest_sha256=f.packet.pin)
    assert not (f.out / "selected_test_scores.parquet").exists()
