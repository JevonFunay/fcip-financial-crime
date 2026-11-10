"""Preflight checks for the repaired-lab frozen synthetic evaluation.

Only small in-memory fixtures and fake candidates are used. These checks never
fit an actual model, generate the large study or access an application DB.
"""

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.ml.behavioral.features import fit_vocabulary
from app.ml.lab.mapping import apply_mapping, read_csv_bytes
from app.ml.lab.trainer import TrainedModel
from app.scripts import audit_lab_repair as audit

from lab_helpers import make_dataset


@pytest.fixture
def source_raw():
    raw = read_csv_bytes(make_dataset(600, defects=False)).rename(columns={"pattern": "pattern_code"})
    raw["is_fraud"] = raw.is_fraud.map({"yes": "1", "no": "0"})
    indices = raw.index[raw.is_fraud.eq("1")]
    for number, index in enumerate(indices):
        raw.loc[index, "pattern_code"] = audit.data.PATTERNS[number % 3]
    return raw


def test_comparison_rejects_any_preselection_test_outputs():
    outcome = SimpleNamespace(results={"models": [{"key": "lightgbm"}], "split": {"test": {"positives": None}}},
                              models=[SimpleNamespace(test_curve=[])])
    audit.assert_validation_only(outcome)
    outcome.results["models"][0]["test"] = {"precision": 1}
    with pytest.raises(ValueError, match="validation results only"):
        audit.assert_validation_only(outcome)
    outcome.results["models"][0].pop("test")
    outcome.models[0].test_curve = [{"threshold": .5}]
    with pytest.raises(ValueError, match="TEST predictions"):
        audit.assert_validation_only(outcome)
    outcome.models[0].test_curve = []
    outcome.results["positive_rate_test"] = .1
    with pytest.raises(ValueError, match="prevalence"):
        audit.assert_validation_only(outcome)


def test_episode_hits_delays_and_unsupported_patterns_are_honest():
    cache = pd.DataFrame({"transaction_ref": ["a", "b", "c"],
                          "transaction_date": pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T00:03:00Z", "2026-01-01T00:00:00Z"]),
                          "amount": [10., 20., 30.], "label": [1., 1., 0.]})
    lineage = pd.DataFrame({"transaction_ref": ["a", "b", "c"], "scenario_id": ["positive", "positive", "benign"],
                            "pattern": ["ATO"] * 3, "role": ["event"] * 3,
                            "case_label": ["simulated_intervention", "simulated_intervention", "benign_lookalike"],
                            "exact_counterfactual": [0] * 3})
    scenarios = [{"scenario_id": "positive", "pattern": "ATO", "case_label": "simulated_intervention", "event_rows": 2},
                 {"scenario_id": "benign", "pattern": "ATO", "case_label": "benign_lookalike", "event_rows": 1},
                 {"scenario_id": "unscored", "pattern": "P05", "case_label": "simulated_intervention", "event_rows": 2}]
    result = audit.episode_results(cache, lineage, scenarios, np.array([.1, .9, .8]), .5, 15.)
    ato = result["positive_episodes"]["ATO"]
    assert ato["episodes_with_any_event_flag"] == 1
    assert ato["episodes_with_all_declared_event_rows_flagged"] == 0
    assert ato["detected_episode_first_flag_delay_seconds_median"] == 180.
    assert result["benign_episodes"]["ATO"]["episodes_with_any_event_flag"] == 1
    assert result["positive_episodes"]["P05"]["unassessed_episodes"] == 1
    assert result["positive_episodes"]["P05"]["any_event_flag_rate"] is None
    assert result["small_positive_pattern_recall"]["ATO"]["recall"] == 0.
    assert result["small_positive_pattern_recall"]["P04"]["recall"] is None


def test_matched_gain_cannot_hide_pattern_true_positive_loss():
    def report(tp, fp, patterns):
        return {"slices": {"all": {"rows": 100, "positive_rows": 10, "tp": tp, "fp": fp,
                                    "precision": tp / (tp + fp)},
                           "small_amount_train_q25": {"tp": 1}},
                "pattern_recall": {p: {"positive_rows": 3, "caught": n} for p, n in zip(audit.data.PATTERNS, patterns)}}
    reference = report(7, 20, (3, 2, 2))
    selected = report(8, 1, (2, 3, 3))
    comparison = audit.matched_comparison(selected, reference)
    assert comparison["precision_delta_percentage_points"] > 0
    assert comparison["reference_tp_count_guards"]["ATO"] is False
    assert not comparison["matched_gain_with_reference_coverage"]


def test_previous_report_identity_is_not_replaced_by_a_new_hash(tmp_path, monkeypatch):
    (tmp_path / "report.json").write_text("{}")
    monkeypatch.setattr(audit, "PREVIOUS", tmp_path)
    with pytest.raises(ValueError, match="original previous report"):
        audit.pin_previous({})


def test_previous_snapshot_cannot_drop_original_code_expectations(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    backend = root / "backend"
    old = backend / "ml_data/old"
    (old / "models").mkdir(parents=True)
    paths = [old / "models/lightgbm.txt", old / "models/lightgbm_contract.json", old / "preprocessing.json"]
    expected = {}
    for path in paths:
        path.write_text("{}"); expected[str(path)] = audit.shared.sha256(path)
    for name in ("first.py", "second.py"):
        path = backend / "app" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(name)
        expected[str(path)] = audit.shared.sha256(path)
    frozen = old / "frozen.json"
    frozen.write_text("{}")
    report = old / "report.json"
    report.write_text(json.dumps({"frozen_manifest_sha256": audit.shared.sha256(frozen), "frozen": {"files": expected}}))
    snapshot = old / "source_snapshot_0382821"
    snapshot.mkdir()
    archive = snapshot / "source.tar"
    archive.write_text("The incomplete map must be rejected before archive members are read")
    archive_sha = audit.shared.sha256(archive)
    (snapshot / "snapshot.json").write_text(json.dumps({
        "original_report_sha256": audit.shared.sha256(report),
        "original_frozen_manifest_sha256": audit.shared.sha256(frozen),
        "archive_sha256": archive_sha,
        "protected_code": {"backend/app/first.py": expected[str(backend / "app/first.py")]},
    }))
    monkeypatch.setattr(audit, "ROOT", root)
    monkeypatch.setattr(audit, "BACKEND", backend)
    monkeypatch.setattr(audit, "PREVIOUS", old)
    monkeypatch.setattr(audit, "PREVIOUS_REPORT_SHA", audit.shared.sha256(report))
    monkeypatch.setattr(audit, "PREVIOUS_ARCHIVE_SHA", archive_sha)
    with pytest.raises(ValueError, match="complete original protected code map"):
        audit.pin_previous({})


@pytest.fixture
def fake_run(tmp_path, source_raw, monkeypatch):
    out = tmp_path / "experiment"
    code = tmp_path / "code.py"
    code.write_text("fixed runtime source")
    monkeypatch.setattr(audit, "BACKEND", tmp_path)
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    monkeypatch.setattr(audit, "CODE_PATHS", ["code.py"])
    monkeypatch.setattr(audit, "SOURCE_ROWS", len(source_raw))
    monkeypatch.setattr(audit, "FINAL_ROWS", len(source_raw))
    monkeypatch.setattr(audit, "MIN_ACTUAL_TRAIN", 100)
    events = []
    models = [TrainedModel(key, "FAKE_UNIT_ONLY", f"fixed {key}", ["AMOUNT"], [threshold], [], 0)
              for key, threshold in zip(audit.MODEL_KEYS, (100_000., 200_000., 300_000.))]

    mapping = audit.data._mapping(source_raw)
    original_frame = apply_mapping(source_raw, mapping).frame
    old_vocabulary = fit_vocabulary(original_frame.iloc[:300])
    old_contract = {"artefact_type": "FAKE_UNIT_ONLY", "features": ["AMOUNT"], "thresholds": [250_000.],
                    "threshold": 250_000., "default_index": 0}
    old_model_path = tmp_path / "old.txt"
    old_model_path.write_text("previous fixed text")

    def previous(expected):
        audit.shared.seal(expected, old_model_path)
        return {"model": TrainedModel("lightgbm", "FAKE_UNIT_ONLY", old_model_path.read_text(), ["AMOUNT"], [250_000.], [], 0),
                "contract": old_contract, "preprocessing": {"vocabulary": old_vocabulary},
                "report_sha256": "fixture", "source_commit": "fixture", "frozen_manifest_sha256": "fixture",
                "source_date_to": "2026-10-06T14:06:09+00:00"}

    def generate(folder, *, rows, seed, start):
        frame = source_raw.copy()
        if seed == audit.FINAL_SEED:
            assert (out / "frozen.json").exists()
            frozen = json.loads((out / "frozen.json").read_text())
            assert frozen["fresh_final_not_created"] and frozen["source_test_not_scored"]
            assert frozen["selection"]["selected_model"] == "lightgbm"
            frame["Timestamp"] = (pd.to_datetime(frame.Timestamp, utc=True) + pd.Timedelta(days=1000)).map(lambda t: t.isoformat())
            events.append("fresh_final_after_freeze")
        else:
            assert not (out / "final").exists()
            events.append("source")
        folder.mkdir()
        frame.to_csv(folder / "transactions.csv", index=False)
        lineages = []
        scenarios = []
        for index, row in frame.iterrows():
            positive = row.is_fraud == "1"
            benign = not positive and index < 30
            pattern = row.pattern_code if positive else audit.data.PATTERNS[index % 3] if benign else ""
            scenario = f"case-{index}" if positive or benign else ""
            case_label = "simulated_intervention" if positive else "benign_lookalike" if benign else "background"
            lineages.append({"transaction_ref": row.TxnID, "scenario_id": scenario, "pattern": pattern,
                             "role": "event" if positive or benign else "background", "case_label": case_label,
                             "exact_counterfactual": 0})
            if scenario:
                scenarios.append({"scenario_id": scenario, "pattern": pattern, "case_label": case_label, "event_rows": 1})
        pd.DataFrame(lineages).to_csv(folder / "lineage.csv", index=False)
        (folder / "scenarios.json").write_text(json.dumps(scenarios))
        manifest = {"rows": len(frame), "coverage": {"mapping": mapping},
                    "counterfactual_diagnostic": {"fixture": True}}
        (folder / "manifest.json").write_text(json.dumps(manifest))
        return manifest

    def train(frame, labels, categories, **config):
        assert config == {"feature_groups": audit.GROUPS, "model_keys": audit.MODEL_KEYS,
                          "train_share": .6, "seed": audit.SEED}
        assert not (out / "final").exists()
        parts = audit.chronological_split(frame, .6)
        _, vocabulary = audit.build_matrix(frame, parts == "train")
        summaries = [{"key": model.key, "default_index": 0,
                      "validation_ranking": {"average_precision": value},
                      "validation_curve": [{"threshold": model.thresholds[0]}]}
                     for model, value in zip(models, (.7, .9, .6))]
        events.append("validation_only_native_fit")
        return SimpleNamespace(models=models, results={"models": summaries, "best_model": "lightgbm",
            "features": {"vocabulary": vocabulary, "used": ["AMOUNT"]},
            "split": {"test": {"positives": None}}})

    monkeypatch.setattr(audit, "pin_previous", previous)
    monkeypatch.setattr(audit.data, "generate_dataset", generate)
    monkeypatch.setattr(audit, "train_run", train)
    monkeypatch.setattr(audit.shared, "score", lambda key, text, frame: frame.AMOUNT.to_numpy())
    return out, events, generate, old_model_path


def test_all_arms_fixed_before_fresh_final_and_reference_is_matched(fake_run):
    out, events, _, _ = fake_run
    report = audit.run(out)
    assert events == ["source", "validation_only_native_fit", "fresh_final_after_freeze"]
    assert report["freeze_precedes_final_generation"]
    assert report["entire_source_precedes_final"] and not report["source_internal_test_evaluated"]
    assert report["previous_entire_source_precedes_final"]
    assert report["source_test_label_support_inspected"]
    assert not report["source_internal_test_prediction_metrics_evaluated"]
    assert set(report["final"]) == {*audit.MODEL_KEYS, "amount_only", "previous_lightgbm"}
    assert report["matched_previous_comparison"]["same_final_population"]
    audit.shared.verify_hashes(report["final_expected_hashes"])
    audit.shared.verify_frozen(report["frozen"], out / "frozen.json", report["frozen_manifest_sha256"])
    with pytest.raises(FileExistsError):
        audit.run(out)


@pytest.mark.parametrize("target", ["final/transactions.csv", "final/lineage.csv", "final/manifest.json"])
def test_final_inputs_cannot_be_changed_during_fixed_scoring(fake_run, monkeypatch, target):
    out, _, _, _ = fake_run
    original = audit.shared.evaluate_fixed

    def tamper(*args, **kwargs):
        value = original(*args, **kwargs)
        with (out / target).open("a") as handle:
            handle.write("tamper\n")
        return value

    monkeypatch.setattr(audit.shared, "evaluate_fixed", tamper)
    with pytest.raises(ValueError, match="Protected file changed"):
        audit.run(out)
    assert not (out / "report.json").exists()


@pytest.mark.parametrize("target", ["frozen.json", "models/lightgbm.txt", "models/lightgbm_contract.json",
                                    "selection.json", "mapping.json", "preprocessing.json"])
def test_frozen_choice_and_model_cannot_change_during_final_creation(fake_run, monkeypatch, target):
    out, _, original, _ = fake_run

    def tamper(folder, **kwargs):
        value = original(folder, **kwargs)
        if kwargs["seed"] == audit.FINAL_SEED:
            (out / target).write_text("tampered")
        return value

    monkeypatch.setattr(audit.data, "generate_dataset", tamper)
    with pytest.raises(ValueError, match="changed"):
        audit.run(out)
    assert not (out / "report.json").exists()


def test_previous_artifact_cannot_change_before_reference_scoring(fake_run, monkeypatch):
    out, _, original, old = fake_run

    def tamper(folder, **kwargs):
        value = original(folder, **kwargs)
        if kwargs["seed"] == audit.FINAL_SEED:
            old.write_text("changed reference")
        return value

    monkeypatch.setattr(audit.data, "generate_dataset", tamper)
    with pytest.raises(ValueError, match="Protected file changed"):
        audit.run(out)
    assert not (out / "report.json").exists()


def test_new_draw_with_source_calendar_overlap_is_refused(fake_run, monkeypatch):
    out, _, original, _ = fake_run

    def overlap(folder, **kwargs):
        value = original(folder, **kwargs)
        if kwargs["seed"] == audit.FINAL_SEED:
            raw = read_csv_bytes((folder / "transactions.csv").read_bytes())
            raw["Timestamp"] = (pd.to_datetime(raw.Timestamp, utc=True) - pd.Timedelta(days=1000)).map(lambda t: t.isoformat())
            raw.to_csv(folder / "transactions.csv", index=False)
        return value

    monkeypatch.setattr(audit.data, "generate_dataset", overlap)
    with pytest.raises(ValueError, match="follow both source calendars"):
        audit.run(out)
    assert not (out / "report.json").exists()


def test_final_must_also_follow_the_previous_reference_source(fake_run, monkeypatch):
    out, _, original, _ = fake_run

    def overlap_old_only(folder, **kwargs):
        value = original(folder, **kwargs)
        if kwargs["seed"] == audit.FINAL_SEED:
            raw = read_csv_bytes((folder / "transactions.csv").read_bytes())
            raw["Timestamp"] = (pd.to_datetime(raw.Timestamp, utc=True) - pd.Timedelta(days=850)).map(lambda t: t.isoformat())
            raw.to_csv(folder / "transactions.csv", index=False)
        return value

    monkeypatch.setattr(audit.data, "generate_dataset", overlap_old_only)
    with pytest.raises(ValueError, match="including the previous reference"):
        audit.run(out)
    assert not (out / "report.json").exists()


@pytest.mark.parametrize("target", ["mapping.json", "source/transactions.csv"])
def test_source_contract_cannot_change_during_fit(fake_run, monkeypatch, target):
    out, _, _, _ = fake_run
    original = audit.train_run

    def tamper(*args, **kwargs):
        value = original(*args, **kwargs)
        (out / target).write_text("changed source contract")
        return value

    monkeypatch.setattr(audit, "train_run", tamper)
    with pytest.raises(ValueError, match="Protected file changed"):
        audit.run(out)
    assert not (out / "final").exists()
