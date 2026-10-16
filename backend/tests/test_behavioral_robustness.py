"""Recall-preserving selection and scientific separation for repaired research."""

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from app.ml.behavioral import generate_v2, robustness
from app.ml.behavioral.features import fit_vocabulary
from app.ml.behavioral.research_features import build_experiment_features
from app.ml.behavioral.robustness_features import GAP, build_relative, feature_names
from app.ml.behavioral.standardize import normalize_frame
from test_behavioral_experiments import experiment_inputs


def _selection_case(fault):
    # Twelve positives (four/group) plus twenty background observations.
    # Reference detects three/group and ten background; candidate flags only
    # one background, so losing coverage still improves its apparent precision.
    labels = pd.DataFrame({"label": [1] * 12 + [0] * 20,
                           "small": [True] * 32,
                           "group": [i // 4 for i in range(12)] + [i % 3 for i in range(20)]})
    for recipe in range(3):
        labels[f"recipe:r{recipe}"] = [i % 4 % 3 == recipe for i in range(12)] + [False] * 20
    reference = np.array([i % 4 < 3 for i in range(12)] + [True] * 10 + [False] * 10)
    candidate = reference.copy()
    candidate[13:] = False
    if fault == "all":
        candidate[0] = False
    elif fault == "small":
        labels.loc[3, "small"] = False
        candidate[0], candidate[3] = False, True
    elif fault == "parent_group:1":
        candidate[4], candidate[3] = False, True
    elif fault == "recipe:r1":
        candidate[1], candidate[3] = False, True
    elif fault == "small_parent_group:0":
        labels.loc[[3, 4], "small"] = False
        candidate[[0, 4]], candidate[[3, 7]] = False, True

    def result(flags, source_flags):
        return {"slices": robustness._slices(labels, np.where(flags, .8, .2), .5),
                "source_unlabelled": {"units_flagged": source_flags}}

    base, changed = result(reference, 10), result(candidate, 11 if fault == "source_load" else 5)
    return {"AML": {robustness.REFERENCE: base, "targeted__auto": changed}}


@pytest.mark.parametrize("fault", ["all", "small", "parent_group:1", "recipe:r1", "small_parent_group:0"])
def test_precision_gain_cannot_compensate_for_losing_one_reference_tp(fault):
    candidates = _selection_case(fault)
    reference, changed = candidates["AML"][robustness.REFERENCE], candidates["AML"]["targeted__auto"]
    assert changed["slices"]["all"]["precision"] > reference["slices"]["all"]["precision"]
    assert changed["slices"][fault]["tp"] == reference["slices"][fault]["tp"] - 1
    choice = robustness.strict_select(candidates)["AML"]
    assert choice["selected"] == robustness.REFERENCE and not choice["improvement_found"]
    assert any(fault in reason for reason in choice["excluded"]["targeted__auto"])


def test_precision_gain_also_cannot_increase_unlabelled_source_workload():
    choice = robustness.strict_select(_selection_case("source_load"))["AML"]
    assert choice["selected"] == robustness.REFERENCE
    assert "more unlabelled-source review flags than reference" in choice["excluded"]["targeted__auto"]


def test_preserved_recall_with_fewer_false_flags_is_eligible():
    choice = robustness.strict_select(_selection_case("none"))["AML"]
    assert choice["selected"] == "targeted__auto" and choice["improvement_found"]


def test_every_generated_descendant_of_a_parent_has_the_same_group():
    parents = [f"P{i:02}" for i in range(12)]
    groups = robustness.parent_groups(parents, seed=20261016)
    assert groups == robustness.parent_groups(list(reversed(parents)) + parents, seed=20261016)
    clones = pd.DataFrame({"parent": np.repeat(parents, 9)})
    clones["group"] = clones.parent.map(groups)
    assert clones.groupby("parent").group.nunique().eq(1).all()
    assert sorted(clones.group.unique()) == [0, 1, 2]
    assert set(groups) == set(parents)


@pytest.fixture(scope="module")
def completed_robustness(experiment_inputs, tmp_path_factory):
    source, previous = experiment_inputs
    root = tmp_path_factory.mktemp("robustness")
    out, models = root / "run", root / "models"
    original = source.read_bytes()
    groups = generate_v2.source_partitions(source)
    real_generate, real_fit = generate_v2.generate, robustness.fit_model
    real_reader, real_open = pd.read_csv, Path.open
    fit_calls, parsed_answers, generation = [], [], []
    generated_accounts = {}
    generating = False
    final_path = out / "final_test"
    final_started = False
    train_answer_reads = []

    def guarded_open(path, mode="r", *args, **kwargs):
        if "r" in mode or "+" in mode:
            resolved = Path(path).resolve()
            assert not resolved.is_relative_to((previous / "test").resolve())
            assert resolved != (previous / "evaluation.json").resolve()
            if resolved == (out / "train" / "ground_truth.csv").resolve() and not generating:
                train_answer_reads.append(str(path))
                raise AssertionError("TRAIN answers are unavailable to the research workflow")
            if resolved.is_relative_to(final_path.resolve()):
                assert (out / "frozen.json").is_file()
        return real_open(path, mode, *args, **kwargs)

    def guarded_reader(path, *args, **kwargs):
        if isinstance(path, (str, Path)):
            resolved = Path(path).resolve()
            if resolved == (out / "train" / "ground_truth.csv").resolve():
                raise AssertionError("TRAIN answers must never be parsed")
            if resolved == (out / "validation" / "ground_truth.csv").resolve():
                assert len(fit_calls) == 6 and len(list(models.rglob("model.txt"))) == 6
                parsed_answers.append("validation")
            if resolved == (final_path / "ground_truth.csv").resolve():
                assert final_started and (out / "frozen.json").is_file()
                parsed_answers.append("final")
        return real_reader(path, *args, **kwargs)

    def guarded_fit(frame, names, **kwargs):
        assert not parsed_answers and not final_started
        assert not {"label", "target", "scenario", "scenario_id"} & set(frame)
        assert not {"y", "labels", "validation", "target"} & set(kwargs)
        assert not set(robustness.META) & set(names)
        accounts = set(frame.account_number)
        assert accounts <= set(groups["train"]) | generated_accounts["train"]
        assert not accounts & (set(groups["validation"]) | set(groups["test"]) | generated_accounts["validation"])
        fit_calls.append(tuple(names))
        return real_fit(frame, names, **kwargs)

    def guarded_generation(source_path, destination, split, *args, **kwargs):
        nonlocal generating, final_started
        if split == "test":
            assert len(fit_calls) == 6 and parsed_answers == ["validation"]
            assert (out / "selection.json").is_file()
            frozen = json.loads((out / "frozen.json").read_text())
            robustness.verify_frozen(frozen)
            assert str((out / "protocol.json").resolve()) in frozen["files"]
            final_started = True
        generating = True
        try:
            manifest = real_generate(source_path, destination, split, *args, **kwargs)
        finally:
            generating = False
        generation.append(split)
        if split in {"train", "validation"}:
            generated_accounts[split] = set(real_reader(destination / "transactions.csv").account_number)
        return manifest

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "open", guarded_open)
        patch.setattr(pd, "read_csv", guarded_reader)
        patch.setattr(robustness, "fit_model", guarded_fit)
        patch.setattr(generate_v2, "generate", guarded_generation)
        report = robustness.run(source, out, models, train_rows=1000, validation_rows=1000,
                                 test_rows=1000, seed=2026101620, final_seed=2026101602,
                                 estimators=3, bootstrap_replicates=0)
    assert source.read_bytes() == original and not train_answer_reads
    return {"report": report, "out": out, "models": models, "fits": fit_calls,
            "answers": parsed_answers, "generation": generation, "groups": groups}


def test_all_unsupervised_fits_precede_grouped_selection_and_fresh_final(completed_robustness):
    run = completed_robustness
    assert len(run["fits"]) == 6
    assert run["generation"] == ["train", "validation", "test"]
    assert run["answers"] == ["validation", "final"]
    report = run["report"]
    assert report["fit_uses_targets"] is False and report["selection_uses_synthetic_validation_labels"] is True
    assert report["no_database_registration_or_promotion"] is True
    assert report["test_generated_after_selection_and_freeze"] is True
    robustness.verify_frozen(report["frozen_artefact_checksums"])
    protocol = json.loads((run["out"] / "protocol.json").read_text())
    assert "not k-fold refits" in protocol["validation_group_kind"]
    for unit in robustness.UNITS:
        slices = report["validation"][unit][robustness.REFERENCE]["slices"]
        assert all(f"parent_group:{i}" in slices for i in range(3))
        assert all(f"small_parent_group:{i}" in slices for i in range(3))
        for role in ("selected", "reference"):
            assert report["fresh_final"][unit][role]["units"] > 0


@pytest.mark.parametrize("target_name", ["source", "protocol", "train_csv"])
def test_during_fit_input_or_protocol_change_stops_before_validation(experiment_inputs, tmp_path, monkeypatch, target_name):
    source, _ = experiment_inputs
    original_source = source.read_bytes()
    out, models = tmp_path / "run", tmp_path / "models"
    fit_calls = []

    def interrupted_fit(source_train, generated_train, destination, protocol):
        fit_calls.append(True)
        target = {"source": source, "protocol": out / "protocol.json", "train_csv": out / "train" / "transactions.csv"}[target_name]
        target.write_bytes(target.read_bytes() + b"\n")
        return {}

    def forbidden_validation(*args, **kwargs):
        raise AssertionError("A changed initial input/configuration cannot reach validation answers")

    monkeypatch.setattr(robustness, "fit_candidates", interrupted_fit)
    monkeypatch.setattr(robustness, "evaluate_validation", forbidden_validation)
    try:
        with pytest.raises(ValueError, match="Frozen experiment input/artefact changed"):
            robustness.run(source, out, models, train_rows=1000, validation_rows=1000, test_rows=1000,
                           seed=2026101620, final_seed=2026101602, estimators=3, bootstrap_replicates=0)
        assert fit_calls == [True]
        assert not (out / "selection.json").exists() and not (out / "final_test").exists()
    finally:
        source.write_bytes(original_source)


def _relative_observations():
    rows = [{"transaction_ref": f"T{i:03}", "account_number": "A",
             "transaction_date": (pd.Timestamp("2023-01-01") + pd.Timedelta(days=3 * i)).date().isoformat(),
             "amount": f"{10 + i % 7}.00", "currency": "USD", "direction": "DEBIT", "channel": "ONLINE",
             "timestamp_precision": "day", "source_timezone_known": False}
            for i in range(65)]
    return normalize_frame(pd.DataFrame(rows), "canonical").frame


@pytest.mark.parametrize("unit", ["AML", "FRAUD"])
def test_targeted_view_excludes_unconditional_context_and_remains_future_invariant(unit):
    observations = _relative_observations()
    vocabulary = fit_vocabulary(observations.iloc[:40])
    cutoff = pd.Timestamp("2023-05-07T23:59:59Z")

    def view(frame):
        portable = build_experiment_features(frame, unit, vocabulary, "portable")
        return build_relative(frame, unit, vocabulary, portable)

    full = view(observations)
    past = view(observations[observations.transaction_date <= cutoff])
    assert_frame_equal(full[full.as_of <= cutoff].reset_index(drop=True), past[past.as_of <= cutoff].reset_index(drop=True))
    assert full[feature_names(unit)].notna().any().any()
    assert not {"LOGIN_ATTEMPTS", "TRANSACTION_DURATION", "ACCOUNT_BALANCE", "DEVICE_FIRST_USE"} & set(feature_names(unit))
    if unit == "AML":
        assert full[GAP].notna().any()


@pytest.mark.parametrize("artifact", ["model.txt", "model_card.json"])
@pytest.mark.parametrize("phase", ["later_fit", "validation"])
def test_model_bytes_cannot_be_reblessed_after_fit_or_validation(experiment_inputs, tmp_path, monkeypatch, artifact, phase):
    source, _ = experiment_inputs
    out, models = tmp_path / "run", tmp_path / "models"
    real_fit, real_validation = robustness.fit_model, robustness.evaluate_validation
    fits, mutations = [], []

    def changed_during_later_fit(frame, names, **kwargs):
        if len(fits) == 1:
            target = models / "mixed_full_256" / "aml" / artifact
            assert target.is_file()
            target.write_bytes(target.read_bytes() + b"\n")
            mutations.append(target)
        fits.append(True)
        return real_fit(frame, names, **kwargs)

    def changed_during_validation(cards, *args, **kwargs):
        result = real_validation(cards, *args, **kwargs)
        target = next(iter(cards.values()))["folder"] / artifact
        target.write_bytes(target.read_bytes() + b"\n")
        mutations.append(target)
        return result

    def forbidden_validation(*args, **kwargs):
        raise AssertionError("A model/card changed during a later fit must fail before validation")

    if phase == "later_fit":
        monkeypatch.setattr(robustness, "fit_model", changed_during_later_fit)
        monkeypatch.setattr(robustness, "evaluate_validation", forbidden_validation)
    else:
        monkeypatch.setattr(robustness, "evaluate_validation", changed_during_validation)
    with pytest.raises(ValueError, match="Frozen experiment input/artefact changed"):
        robustness.run(source, out, models, train_rows=1000, validation_rows=1000, test_rows=1000,
                       seed=2026101620, final_seed=2026101602, estimators=3, bootstrap_replicates=0)
    assert len(mutations) == 1
    assert not (out / "frozen.json").exists() and not (out / "final_test").exists()


def test_grouped_labels_include_exact_money_boundary(tmp_path):
    frame = normalize_frame(pd.DataFrame([
        {"transaction_ref": f"T{i}", "account_number": "A", "transaction_date": f"2023-01-0{i + 1}",
         "amount": amount, "currency": "USD", "direction": "DEBIT", "channel": "ONLINE"}
        for i, amount in enumerate(["83.44", "83.45"])
    ]), "canonical").frame
    truth = pd.DataFrame({"transaction_ref": ["T0", "T1"], "label": [1, 0],
                          "scenario": ["stress", "ordinary"]})
    features = frame[["transaction_ref", "account_number"]].assign(as_of=frame.transaction_date)
    lineage = tmp_path / "lineage.csv"
    pd.DataFrame({"account_number": ["A"], "source_account_id": ["parent"]}).to_csv(lineage, index=False)
    labels = robustness._labels(frame, features, "FRAUD", truth, lineage, 83.44, {"parent": 0})
    assert labels.small.tolist() == [True, False]
