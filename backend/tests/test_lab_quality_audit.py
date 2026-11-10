"""Offline quality measurement: immutable final gate and fixed native defaults.

These checks do not fit production candidates, create real experiment data or
touch the application database. The orchestration fixture supplies synthetic
in-memory observations and a fake candidate to test ordering and tampering.
"""

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.ml.behavioral.features import fit_vocabulary
from app.ml.lab.mapping import apply_mapping, read_csv_bytes
from app.ml.lab.trainer import TrainedModel
from app.scripts import audit_current_lab as audit

from lab_helpers import make_dataset


@pytest.fixture
def source_raw():
    raw = read_csv_bytes(make_dataset(600, defects=False)).rename(columns={"pattern": "pattern_code"})
    raw["is_fraud"] = raw.is_fraud.map({"yes": "1", "no": "0"})
    raw["pattern_code"] = np.where(raw.is_fraud.eq("1"), "ATO", "")
    return raw


def fake_model(key="logistic_regression", threshold=100_000.0):
    return TrainedModel(key, "FAKE_TEST_ONLY", "test model text", ["AMOUNT"], [threshold], [], 0)


def fake_results(models, vocabulary):
    summaries = []
    for index, model in enumerate(models):
        summaries.append({"key": model.key, "default_index": 0,
                          "validation_ranking": {"average_precision": 0.8 - index * 0.1},
                          "validation_curve": [{"threshold": model.thresholds[0]}],
                          "test": {"precision": 0.1 if index == 0 else 1.0}})
    return {"best_model": models[0].key, "models": summaries,
            "features": {"vocabulary": vocabulary, "used": ["AMOUNT"]}}


def test_choice_uses_validation_only_and_keeps_native_defaults():
    models = [fake_model("logistic_regression", 0.123), fake_model("lightgbm", 0.456)]
    results = fake_results(models, {})
    choice = audit.choose_defaults(results, models)
    assert choice["selected_model"] == "logistic_regression"
    assert choice["models"]["logistic_regression"]["threshold"] == 0.123
    assert choice["models"]["lightgbm"]["threshold"] == 0.456
    results["models"][0]["test"]["precision"] = 0.0
    results["models"][1]["test"]["precision"] = 1.0
    assert audit.choose_defaults(results, models) == choice
    results["models"][0]["validation_curve"][0]["threshold"] = 0.9
    with pytest.raises(ValueError, match="Native default"):
        audit.choose_defaults(results, models)


def test_original_hash_expectations_cannot_be_reblessed(tmp_path):
    path = tmp_path / "model.txt"
    expected = {}
    audit.write_text(path, "original", expected)
    path.write_text("changed")
    with pytest.raises(ValueError, match="Protected file changed"):
        audit.verify_hashes(expected)
    with pytest.raises(ValueError, match="Cannot replace"):
        audit.seal(expected, path)


def test_immediate_model_bytes_must_match_in_memory_text(tmp_path):
    path = tmp_path / "model.txt"
    path.write_text("wrong")
    with pytest.raises(ValueError, match="original expected bytes"):
        audit.seal({}, path, "right")


def test_original_manifest_hash_is_checked_as_well_as_members(tmp_path):
    member, manifest = tmp_path / "model.txt", tmp_path / "frozen.json"
    expected = {}
    audit.write_text(member, "model", expected)
    frozen = {"files": expected}
    audit.write_json(manifest, frozen)
    original_hash = audit.sha256(manifest)
    audit.verify_frozen(frozen, manifest, original_hash)
    manifest.write_text('{"files": {}}')
    with pytest.raises(ValueError, match="manifest changed"):
        audit.verify_frozen(frozen, manifest, original_hash)


def test_missing_labels_and_other_currencies_are_not_normal_labels(source_raw):
    mapping = audit.fixed_mapping(source_raw)
    raw = source_raw.copy()
    raw.loc[0, "Timestamp"] = "invalid date"
    raw.loc[1, "Currency"] = "USD"
    raw.loc[2, "is_fraud"] = ""
    raw = pd.concat([raw, raw.iloc[[3]]], ignore_index=True)
    population = audit.map_final(raw, mapping, "IDR")
    assert population.report["total_rows"] == 601
    assert population.report["quarantined"] == 2
    assert population.report["observations_skipped_other_currency"] == 1
    assert population.report["observations_scored"] == 598
    assert population.report["known_label_rows"] == 597
    assert population.report["unresolved_label_rows"] == 1
    assert np.isnan(population.labels).sum() == 1
    assert "is_fraud" not in population.frame and "pattern_code" not in population.frame


def test_unexpected_sample_labels_are_rejected_not_relabelled_normal(source_raw):
    mapping = audit.fixed_mapping(source_raw)
    source_raw.loc[0, "is_fraud"] = "UNKNOWN"
    with pytest.raises(ValueError, match="literal 0/1"):
        audit.map_final(source_raw, mapping, "IDR")


def test_empty_positive_slice_has_no_recall_or_ranking_claim():
    value = audit.metric_counts(np.array([0.0, 0.0, np.nan]), np.array([0.9, 0.1, 1.0]), 0.5)
    assert value["rows"] == 2 and value["unresolved_rows"] == 1
    assert value["tp"] == 0 and value["fp"] == 1 and value["tn"] == 1
    assert value["recall"] is None and value["average_precision"] is None
    empty = audit.metric_counts(np.array([]), np.array([]), 0.5)
    assert empty["precision"] is None and empty["recall"] is None and empty["f1"] is None


def test_final_scores_do_not_depend_on_labels_or_refit_preprocessing(source_raw, monkeypatch):
    mapping = audit.fixed_mapping(source_raw)
    mapped = apply_mapping(source_raw, mapping)
    vocabulary = fit_vocabulary(mapped.frame.iloc[:300])
    models = [fake_model()]
    selection = audit.choose_defaults(fake_results(models, vocabulary), models)
    selection["diagnostic_baselines"] = {"amount_only": {"threshold": 200_000.0}}
    cutoff = vocabulary["small_amount_threshold"]
    quartiles = [cutoff, cutoff * 2, cutoff * 3]
    seen = []

    def fixed_score(key, text, features):
        seen.append(list(features.columns))
        return features.AMOUNT.to_numpy()

    monkeypatch.setattr(audit, "score", fixed_score)
    monkeypatch.setattr(audit, "build_matrix", lambda *args, **kwargs: pytest.fail("Final refitted vocabulary"))
    monkeypatch.setattr(audit, "best_f1_threshold", lambda *args, **kwargs: pytest.fail("Final retuned threshold"))
    first = audit.map_final(source_raw, mapping, "IDR")
    changed = source_raw.copy()
    changed["is_fraud"] = changed.is_fraud.map({"0": "1", "1": "0"})
    second = audit.map_final(changed, mapping, "IDR")
    a, cache_a, features_a = audit.evaluate_fixed(first, models, vocabulary, selection, quartiles,
                                                 pd.Series(["background"] * len(first.frame)))
    b, cache_b, features_b = audit.evaluate_fixed(second, models, vocabulary, selection, quartiles,
                                                 pd.Series(["background"] * len(second.frame)))
    pd.testing.assert_frame_equal(features_a, features_b)
    np.testing.assert_array_equal(cache_a.score_logistic_regression, cache_b.score_logistic_regression)
    np.testing.assert_array_equal(cache_a.score_amount_only, cache_b.score_amount_only)
    assert seen == [["AMOUNT"], ["AMOUNT"]]
    assert a["amount_only"]["threshold"] == b["amount_only"]["threshold"] == 200_000.0
    assert a["logistic_regression"]["slices"]["all"]["positive_rows"] != b["logistic_regression"]["slices"]["all"]["positive_rows"]
    models[0].thresholds[0] = 99.0
    with pytest.raises(ValueError, match="differs from frozen"):
        audit.evaluate_fixed(first, models, vocabulary, selection, quartiles,
                             pd.Series(["background"] * len(first.frame)))


def test_current_audit_refuses_a_single_availability_group():
    frame = pd.DataFrame({"transaction_ref": list("abcdef"),
                          "transaction_date": pd.to_datetime(["2026-01-01T00:00:00Z"] * 6)})
    # The old split is retained in the source archive of the original audit.
    # Current code must refuse it instead of splitting an unknown time order.
    with pytest.raises(ValueError, match="tiga waktu"):
        audit.chronological_split(frame, .5)


def test_other_injected_patterns_remain_distinct_from_normal_controls(tmp_path):
    tables = {
        "accounts.csv": "source_account_id,owner_source_id\na,e1\nb,e2\nc,e3\n",
        "labels.csv": "scenario_id,label_type,pattern_code,entity_source_id\ncontrol,CONTROL_CLEAN,,e1\nother,INJECTED_POSITIVE,P01,e2\nedge,EDGE_CASE,P04,e3\n",
        "label_transactions.csv": "scenario_id,source_transaction_reference\nother,t2\nedge,t3\n",
    }
    for name, text in tables.items():
        (tmp_path / name).write_text(text)
    cohorts = audit.scenario_cohorts(tmp_path, pd.Series(["t1", "t2", "t3"]), pd.Series(["a", "b", "c"]))
    assert cohorts.tolist() == ["control_cohort", "other_pattern_injected_reference_negative",
                                "fraud_pattern_lookalike_or_boundary"]


@pytest.fixture
def fake_run(tmp_path, source_raw, monkeypatch):
    code = tmp_path / "source_code.py"
    code.write_text("original production source")
    monkeypatch.setattr(audit, "BACKEND", tmp_path)
    monkeypatch.setattr(audit, "CODE_PATHS", ["source_code.py"])
    monkeypatch.setattr(audit, "scenario_cohorts",
                        lambda raw, refs, accounts: pd.Series(["background"] * len(refs)))
    events = []
    out = tmp_path / "experiment"

    def generate(raw_dir, sample, *, seed, reference):
        if seed == audit.FINAL_SOURCE_SEED:
            frozen = json.loads((out / "frozen.json").read_text())
            assert frozen["final_draw_not_created"] and frozen["selection"]["selected_model"] == "logistic_regression"
            assert (out / "selection.json").exists() and (out / "models/logistic_regression.txt").exists()
            events.append("final_after_freeze")
        else:
            assert not (out / "final_raw").exists()
            events.append("development")
        raw_dir.mkdir()
        (raw_dir / "manifest.json").write_text('{"fixture": true}')
        source_raw.to_csv(sample, index=False)
        return {"synthetic_fixture": True}

    def train(frame, labels, categories, **kwargs):
        assert kwargs == {"feature_groups": audit.GROUPS, "model_keys": audit.MODEL_KEYS,
                          "train_share": .6, "seed": audit.SEED}
        assert not (out / "final_raw").exists()
        parts = audit.chronological_split(frame, .6)
        _, vocabulary = audit.build_matrix(frame, parts == "train")
        model = fake_model()
        events.append("unchanged_train_contract")
        return SimpleNamespace(results=fake_results([model], vocabulary), models=[model])

    monkeypatch.setattr(audit, "generate_draw", generate)
    monkeypatch.setattr(audit, "train_run", train)
    monkeypatch.setattr(audit, "score", lambda key, text, frame: frame.AMOUNT.to_numpy())
    return out, events, generate


def test_fresh_draw_occurs_after_frozen_winner_and_fixed_defaults(fake_run):
    out, events, _ = fake_run
    report = audit.run(out)
    assert events == ["development", "unchanged_train_contract", "final_after_freeze"]
    assert report["freeze_precedes_final_generation"] and not report["final_retuned"]
    assert report["selection"]["selected_model"] == "logistic_regression"
    assert "amount_only" in report["final"] and "amount_only" not in report["selection"]["models"]
    audit.verify_hashes(report["final_artifact_hashes"])
    with pytest.raises(FileExistsError):
        audit.run(out)


@pytest.mark.parametrize("target", ["final.csv", "final_raw/manifest.json"])
def test_tampered_final_input_during_scoring_is_not_reblessed(fake_run, monkeypatch, target):
    out, _, _ = fake_run
    original = audit.evaluate_fixed

    def tamper(*args, **kwargs):
        result = original(*args, **kwargs)
        with (out / target).open("a") as handle:
            handle.write("tampered\n")
        return result

    monkeypatch.setattr(audit, "evaluate_fixed", tamper)
    with pytest.raises(ValueError, match="Protected file changed"):
        audit.run(out)
    assert not (out / "report.json").exists()


@pytest.mark.parametrize("target", ["source.csv", "mapping.json"])
def test_training_input_changed_during_fit_is_not_reblessed(fake_run, monkeypatch, target):
    out, _, _ = fake_run
    original = audit.train_run

    def tamper(*args, **kwargs):
        value = original(*args, **kwargs)
        (out / target).write_text("tampered")
        return value

    monkeypatch.setattr(audit, "train_run", tamper)
    with pytest.raises(ValueError, match="Protected file changed"):
        audit.run(out)
    assert not (out / "final_raw").exists()


@pytest.mark.parametrize("target", ["frozen.json", "models/logistic_regression.txt", "selection.json"])
def test_frozen_artifacts_tampered_during_final_generation_fail(fake_run, monkeypatch, target):
    out, _, original = fake_run

    def tamper(raw_dir, sample, **kwargs):
        value = original(raw_dir, sample, **kwargs)
        if kwargs["seed"] == audit.FINAL_SOURCE_SEED:
            (out / target).write_text("tampered")
        return value

    monkeypatch.setattr(audit, "generate_draw", tamper)
    with pytest.raises(ValueError, match="changed"):
        audit.run(out)
    assert not (out / "report.json").exists()
