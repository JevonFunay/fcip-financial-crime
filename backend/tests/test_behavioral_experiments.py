"""Scientific separation for offline retraining; no app database or user data.

Synthetic validation may select a model, but no target enters estimator fitting.
A newly generated final evaluation is available only after the selection freeze.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.ml.behavioral import experiments
from app.ml.behavioral.generate import SOURCE_COLUMNS, generate, source_partitions


TRAIN_SEED = 20261015
FINAL_SEED = 2026101601


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _snapshot(root):
    return {str(path.relative_to(root)): _sha256(path) for path in sorted(root.rglob("*")) if path.is_file()}


def _under(path, parent):
    try:
        return Path(path).resolve().is_relative_to(parent.resolve())
    except (TypeError, ValueError):
        return False


def _walk(value, path=()):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from _walk(child, (*path, str(key)))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from _walk(child, (*path, str(i)))


@pytest.fixture(scope="module")
def experiment_inputs(tmp_path_factory):
    folder = tmp_path_factory.mktemp("behavioral_experiment_inputs")
    rows = []
    for account in range(40):
        for transaction in range(12):
            rows.append({
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
    source = folder / "source.csv"
    pd.DataFrame(rows, columns=SOURCE_COLUMNS).to_csv(source, index=False, encoding="utf-8-sig")
    previous = folder / "previous"
    generate(source, previous / "train", "train", 1000, TRAIN_SEED, reference_date=date(2026, 10, 15))
    generate(source, previous / "validation", "validation", 1000, TRAIN_SEED + 1,
             fit_profile=previous / "train" / "fit_profile.json", reference_date=date(2026, 10, 15))
    # Traps contain no historical user test observations/results. Opening them
    # would nevertheless prove the runner consulted a prohibited old final path.
    (previous / "test").mkdir()
    (previous / "test" / "transactions.csv").write_text("DO NOT READ OLD FINAL INPUT\n")
    (previous / "test" / "ground_truth.csv").write_text("DO NOT READ OLD FINAL ANSWERS\n")
    (previous / "evaluation.json").write_text('{"old_final_results":"DO NOT READ"}\n')
    return source, previous


@pytest.fixture(scope="module")
def completed_experiment(experiment_inputs, tmp_path_factory):
    source, previous = experiment_inputs
    root = tmp_path_factory.mktemp("behavioral_experiment_outputs")
    out, models = root / "run", root / "models"
    original_bytes = source.read_bytes()
    input_hashes = {name: _snapshot(previous / name) for name in ("train", "validation")}
    partitions = source_partitions(source)
    generated_train_accounts = set(pd.read_csv(previous / "train" / "transactions.csv").account_number)
    validation_accounts = set(pd.read_csv(previous / "validation" / "transactions.csv").account_number)
    events, fits, vocabularies, frozen_at_generation = [], [], [], {}
    validation_answers_seen = False
    final_destination = None
    real_fit, real_vocabulary = experiments.fit_model, experiments.fit_vocabulary
    real_generate, real_reader, real_open = experiments.generate, pd.read_csv, Path.open

    def guard_path(path):
        if not isinstance(path, (str, Path)):
            return
        path = Path(path).resolve()
        assert not _under(path, previous / "test"), "Old final observations/answers must never be read"
        assert path != (previous / "evaluation.json").resolve(), "Old final results must never be read"
        assert path != (previous / "train" / "ground_truth.csv").resolve(), "Training answers must never be read"
        if final_destination is not None and _under(path, final_destination):
            assert (out / "frozen.json").is_file(), "Final observations cannot be read before the freeze"

    def guarded_open(path, mode="r", *args, **kwargs):
        if "r" in mode or "+" in mode:
            guard_path(path)
        return real_open(path, mode, *args, **kwargs)

    def guarded_reader(path, *args, **kwargs):
        nonlocal validation_answers_seen
        guard_path(path)
        if isinstance(path, (str, Path)) and Path(path).resolve() == (previous / "validation" / "ground_truth.csv").resolve():
            assert fits, "Validation selection must follow candidate fitting"
            assert len(list(models.rglob("model.txt"))) == len(fits), "Every candidate must be fitted before reading validation answers"
            if not validation_answers_seen:
                events.append("validation_answers")
            validation_answers_seen = True
        return real_reader(path, *args, **kwargs)

    def guarded_fit(frame, names, **kwargs):
        assert not validation_answers_seen, "No estimator may be fitted after validation selection begins"
        assert final_destination is None, "No fitting may use the final evaluation"
        assert not {"label", "target", "scenario", "scenario_id", "source_ordinal"} & set(frame.columns)
        assert not {"y", "target", "labels", "validation", "validation_y"} & set(kwargs)
        assert not {"account_number", "transaction_ref", "as_of"} & set(names)
        assert "account_number" in frame
        accounts = set(frame.account_number)
        assert not accounts & (set(partitions["validation"]) | set(partitions["test"]) | validation_accounts)
        assert accounts <= set(partitions["train"]) | generated_train_accounts
        fits.append({"accounts": accounts, "features": tuple(names), "kwargs": kwargs})
        events.append("fit")
        return real_fit(frame, names, **kwargs)

    def guarded_vocabulary(frame):
        assert not validation_answers_seen and final_destination is None
        assert not {"label", "target", "scenario", "scenario_id"} & set(frame.columns)
        accounts = set(frame.account_number)
        assert accounts <= set(partitions["train"]) | generated_train_accounts
        assert not accounts & validation_accounts
        vocabularies.append(accounts)
        return real_vocabulary(frame)

    def observed_generation(source_arg, destination, split, *args, **kwargs):
        nonlocal final_destination
        assert split == "test", "The experiment must reuse immutable training/validation splits"
        assert validation_answers_seen and fits
        assert (out / "protocol.json").is_file() and (out / "selection.json").is_file()
        assert (out / "frozen.json").is_file(), "Selection and all configuration must be frozen before final generation"
        frozen = json.loads((out / "frozen.json").read_text())
        experiments.verify_frozen(frozen)
        frozen_at_generation.update(frozen)
        assert kwargs.get("fit_profile") == previous / "train" / "fit_profile.json"
        assert kwargs.get("seed", args[1] if len(args) > 1 else None) == FINAL_SEED
        final_destination = Path(destination)
        events.append("final_generation")
        return real_generate(source_arg, destination, split, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "open", guarded_open)
        patch.setattr(pd, "read_csv", guarded_reader)
        patch.setattr(experiments, "fit_model", guarded_fit)
        patch.setattr(experiments, "fit_vocabulary", guarded_vocabulary)
        patch.setattr(experiments, "generate", observed_generation)
        report = experiments.run(source, previous, out, models, test_rows=1000, seed=TRAIN_SEED,
                                 final_seed=FINAL_SEED, reference_date=date(2026, 10, 16), estimators=2,
                                 max_samples_options=(16,), threshold_quantiles=(.95, .99), bootstrap_replicates=0)
    assert source.read_bytes() == original_bytes
    assert {name: _snapshot(previous / name) for name in input_hashes} == input_hashes
    return {"source": source, "previous": previous, "out": out, "models": models, "report": report,
            "fits": fits, "vocabularies": vocabularies, "events": events, "frozen": frozen_at_generation,
            "final": final_destination, "partitions": partitions}


def test_unsupervised_fit_validation_selection_freeze_then_new_final(completed_experiment):
    run = completed_experiment
    assert run["report"]["fit_uses_targets"] is False
    assert run["report"]["selection_uses_synthetic_validation_labels"] is True
    assert run["fits"] and run["vocabularies"]
    assert run["events"].index("validation_answers") > max(i for i, event in enumerate(run["events"]) if event == "fit")
    assert run["events"].index("final_generation") > run["events"].index("validation_answers")
    assert any(record["accounts"] == set(run["partitions"]["train"]) for record in run["fits"])
    assert any(record["accounts"] > set(run["partitions"]["train"]) for record in run["fits"])
    experiments.verify_frozen(run["frozen"])
    manifest = json.loads((run["final"] / "manifest.json").read_text())
    assert manifest["seed"] == FINAL_SEED
    assert FINAL_SEED not in {TRAIN_SEED, TRAIN_SEED + 1, TRAIN_SEED + 2}
    assert manifest["fit_profile_sha256"] == _sha256(run["previous"] / "train" / "fit_profile.json")
    assert len(pd.read_csv(run["final"] / "transactions.csv")) == 1000
    parent_sets, generated_ids = {}, {}
    for split, folder in (("train", run["previous"] / "train"), ("validation", run["previous"] / "validation"),
                          ("test", run["final"])):
        lineage = pd.read_csv(folder / "lineage.csv")
        parent_sets[split] = set(lineage.source_account_id)
        generated_ids[split] = set(lineage.account_number)
        assert parent_sets[split] <= set(run["partitions"][split])
    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        assert parent_sets[left].isdisjoint(parent_sets[right])
        assert generated_ids[left].isdisjoint(generated_ids[right])


def test_source_evaluations_report_review_load_without_inventing_accuracy(completed_experiment):
    metrics = [node for path, node in _walk(completed_experiment["report"])
               if {"precision", "recall"} <= node.keys()
               and any("unlabelled" in part or "unlabeled" in part
                       or part.startswith("source_") and "source_only" not in part for part in path)]
    assert metrics, "The experiment must retain its original-source unlabelled evaluation"
    for metric in metrics:
        for name in ("precision", "recall", "f1", "average_precision", "roc_auc", "accuracy"):
            assert metric.get(name) is None, "Source data has no verified target for accuracy estimates"


def test_freeze_detects_changed_candidate_artifact(completed_experiment):
    run = completed_experiment
    model = next(run["models"].rglob("model.txt"))
    content = model.read_bytes()
    try:
        model.write_bytes(content + b"\n ")
        with pytest.raises((ValueError, RuntimeError)):
            experiments.verify_frozen(run["frozen"])
    finally:
        model.write_bytes(content)
    experiments.verify_frozen(run["frozen"])


@pytest.mark.parametrize("existing", ["run", "models"])
def test_existing_output_refuses_before_fitting_or_changing_inputs(experiment_inputs, tmp_path, monkeypatch, existing):
    source, previous = experiment_inputs
    out, models = tmp_path / "run", tmp_path / "models"
    occupied = out if existing == "run" else models
    occupied.mkdir()
    marker = occupied / "keep.txt"
    marker.write_bytes(b"existing immutable artifact\n")
    before = {"source": _sha256(source), "train": _snapshot(previous / "train"),
              "validation": _snapshot(previous / "validation"), "occupied": _snapshot(occupied)}

    def forbidden(*args, **kwargs):
        pytest.fail("Existing output must be rejected before fitting or generating")

    monkeypatch.setattr(experiments, "fit_model", forbidden)
    monkeypatch.setattr(experiments, "generate", forbidden)
    with pytest.raises(FileExistsError):
        experiments.run(source, previous, out, models, test_rows=1000, seed=TRAIN_SEED,
                        final_seed=FINAL_SEED, estimators=2, max_samples_options=(16,),
                        threshold_quantiles=(.95,), bootstrap_replicates=0)
    assert {"source": _sha256(source), "train": _snapshot(previous / "train"),
            "validation": _snapshot(previous / "validation"), "occupied": _snapshot(occupied)} == before
    assert not (models if existing == "run" else out).exists()


def _selection_candidate(*, total_tp=10, small_tp=5, f1=.4, average_precision=.2, source_flags=100):
    return {"synthetic": {"synthetic_stress": {"tp": total_tp, "f1": f1, "average_precision": average_precision},
                          "small_amount": {"tp": small_tp}},
            "source_unlabelled": {"units_flagged": source_flags}}


def test_selection_preserves_total_and_small_stress_coverage_and_source_review_load():
    candidates = {
        experiments.REFERENCE: _selection_candidate(),
        "drop_total_coverage": _selection_candidate(total_tp=7, f1=.9),
        "drop_small_coverage": _selection_candidate(small_tp=3, f1=.9),
        "flood_source": _selection_candidate(source_flags=101, f1=.9),
    }
    result = experiments.select_candidates({"AML": candidates}, {
        "selection": {"minimum_reference_tp_retention": .80},
    })["AML"]
    assert result["minimum_total_tp"] == 8 and result["minimum_small_amount_tp"] == 4
    assert result["selected"] == experiments.REFERENCE
    assert set(result["excluded"]) == set(candidates) - {experiments.REFERENCE}
    assert result["eligible"] == [experiments.REFERENCE]


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ({"f1": .6, "average_precision": .1, "source_flags": 95},
         {"f1": .5, "average_precision": .9, "source_flags": 0}),
        ({"f1": .5, "average_precision": .4, "source_flags": 95},
         {"f1": .5, "average_precision": .3, "source_flags": 0}),
        ({"f1": .5, "average_precision": .4, "source_flags": 20},
         {"f1": .5, "average_precision": .4, "source_flags": 30}),
        ({"f1": .5, "average_precision": .4, "source_flags": 20},
         {"f1": .5, "average_precision": .4, "source_flags": 20}),
    ], ids=["f1-before-ap", "ap-before-review-load", "lower-source-review-load", "stable-lexical-tie"],
)
def test_selection_uses_declared_ranking_and_stable_ties(left, right):
    result = experiments.select_candidates({"FRAUD": {
        experiments.REFERENCE: _selection_candidate(),
        "alpha": _selection_candidate(**left), "zeta": _selection_candidate(**right),
    }}, {"selection": {"minimum_reference_tp_retention": .80}})["FRAUD"]
    assert result["selected"] == "alpha"


@pytest.mark.parametrize("final_seed", [TRAIN_SEED, TRAIN_SEED + 1, TRAIN_SEED + 2])
def test_reused_final_draw_seed_is_rejected_before_any_output(experiment_inputs, tmp_path, monkeypatch, final_seed):
    source, previous = experiment_inputs
    out, models = tmp_path / "run", tmp_path / "models"

    def forbidden(*args, **kwargs):
        pytest.fail("A previously used split seed must be refused before fitting/generation")

    monkeypatch.setattr(experiments, "fit_model", forbidden)
    monkeypatch.setattr(experiments, "generate", forbidden)
    with pytest.raises(ValueError, match="seed"):
        experiments.run(source, previous, out, models, test_rows=1000, seed=TRAIN_SEED,
                        final_seed=final_seed, estimators=2, max_samples_options=(16,),
                        threshold_quantiles=(.95,), bootstrap_replicates=0)
    assert not out.exists() and not models.exists()


def test_parent_cluster_intervals_do_not_count_duplicate_rows_as_new_independent_evidence():
    import numpy as np

    labels = np.array([1, 0, 1, 0], dtype=bool)
    selected = np.array([1, 0, 1, 1], dtype=bool)
    reference = np.array([1, 1, 0, 0], dtype=bool)
    parents = np.array(["PARENT-A", "PARENT-A", "PARENT-B", "PARENT-B"])
    original = experiments.clustered_intervals(labels, selected, reference, parents, replicates=100, seed=71)
    duplicates = experiments.clustered_intervals(
        np.repeat(labels, 25), np.repeat(selected, 25), np.repeat(reference, 25), np.repeat(parents, 25),
        replicates=100, seed=71,
    )
    assert original["clusters"] == duplicates["clusters"] == 2
    assert original["intervals"] == duplicates["intervals"]
    assert original["intervals"]["precision"]["reference"]["valid_replicates"] < 100
    identical = experiments.clustered_intervals(labels, selected, selected, parents, replicates=100, seed=71)
    for metric in ("precision", "recall", "f1"):
        assert identical["intervals"][metric]["paired_delta"] == {"low": 0.0, "high": 0.0, "valid_replicates": 100}
