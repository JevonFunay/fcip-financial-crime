"""Experiment provenance refuses changed inputs before label-based selection.

All data belongs to temporary portable fixtures. No user dataset or application
database is opened, and shared fixture bytes are restored after every mutation.
"""

import hashlib
import inspect
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.ml.behavioral import experiments
from test_behavioral_experiments import (
    FINAL_SEED,
    TRAIN_SEED,
    completed_experiment,
    experiment_inputs,
)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(source, previous, out, models):
    return experiments.run(source, previous, out, models, test_rows=1000, seed=TRAIN_SEED,
                           final_seed=FINAL_SEED, reference_date=date(2026, 10, 16), estimators=2,
                           max_samples_options=(16,), threshold_quantiles=(.95,), bootstrap_replicates=0)


@pytest.mark.parametrize("relative", [
    "train/transactions.csv", "validation/transactions.csv",
    "train/fit_profile.json", "validation/fit_profile.json",
])
def test_changed_previous_input_is_refused_before_outputs_or_fitting(experiment_inputs, tmp_path, monkeypatch, relative):
    source, previous = experiment_inputs
    target = previous / relative
    original = target.read_bytes()
    out, models = tmp_path / "run", tmp_path / "models"
    fit_calls = []

    def forbidden_fit(*args, **kwargs):
        fit_calls.append(True)
        raise AssertionError("A manifest mismatch must be rejected before fitting")

    monkeypatch.setattr(experiments, "_fit_candidates", forbidden_fit)
    try:
        # Even a syntactically harmless change invalidates immutable provenance.
        target.write_bytes(original + b"\n")
        with pytest.raises(ValueError, match="differs from its manifest"):
            _run(source, previous, out, models)
        assert not fit_calls
        assert not out.exists() and not models.exists()
    finally:
        target.write_bytes(original)
    assert target.read_bytes() == original


@pytest.mark.parametrize("relative", [None, "train/transactions.csv", "validation/transactions.csv"])
def test_change_during_fit_cannot_be_blessed_by_later_freeze(experiment_inputs, tmp_path, monkeypatch, relative):
    source, previous = experiment_inputs
    target = source if relative is None else previous / relative
    original = target.read_bytes()
    out, models = tmp_path / "run", tmp_path / "models"
    truth_path = (previous / "validation" / "ground_truth.csv").resolve()
    real_reader = pd.read_csv
    fits, answer_reads = [], []

    def guarded_reader(path, *args, **kwargs):
        if isinstance(path, (str, Path)) and Path(path).resolve() == truth_path:
            answer_reads.append(True)
            raise AssertionError("Changed fit inputs must be rejected before validation answers are parsed")
        return real_reader(path, *args, **kwargs)

    def interrupted_fit(source_train, generated_train, model_root, protocol):
        assert source_train.shape[0] and generated_train.shape[0]
        assert (out / "protocol.json").is_file()
        assert protocol["protected_input_checksums"][str(target.resolve())] == _digest(target)
        fits.append(True)
        # Inputs were already normalized. A later hash must not silently bless
        # this different source while the model represents the earlier bytes.
        target.write_bytes(original + b"\n")
        return {}

    def forbidden_evaluation(*args, **kwargs):
        raise AssertionError("Initial input hashes must be checked before validation scoring")

    monkeypatch.setattr(pd, "read_csv", guarded_reader)
    monkeypatch.setattr(experiments, "_fit_candidates", interrupted_fit)
    monkeypatch.setattr(experiments, "evaluate_candidates", forbidden_evaluation)
    try:
        with pytest.raises(ValueError, match="Frozen experiment input/artefact changed"):
            _run(source, previous, out, models)
        assert fits == [True] and not answer_reads
        assert not (out / "selection.json").exists()
        assert not (out / "frozen.json").exists()
        assert not (out / "final_test").exists()
    finally:
        target.write_bytes(original)
    assert target.read_bytes() == original


def test_imported_pipeline_metric_and_loading_code_is_part_of_the_freeze(completed_experiment):
    run = completed_experiment
    protocol = json.loads((run["out"] / "protocol.json").read_text())
    # Discover the actual imported dependency, rather than merely asserting a
    # hardcoded filename that could stop matching the executed helper code.
    helpers = (experiments.load_observations, experiments._evaluation_labels,
               experiments._metrics, experiments.distribution)
    dependency_paths = {str(Path(inspect.getsourcefile(helper)).resolve()) for helper in helpers}
    assert len(dependency_paths) == 1
    dependency = dependency_paths.pop()
    assert Path(dependency).name == "pipeline.py"
    assert protocol["code_checksums"][dependency] == _digest(Path(dependency))
    assert run["frozen"]["files"][dependency] == protocol["code_checksums"][dependency]
    experiments.verify_frozen(run["frozen"])
