"""Validation error slices preserve score identity and account-week units."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml.behavioral import error_audit
from app.ml.behavioral.features import fit_vocabulary
from app.ml.behavioral.standardize import normalize_frame
from test_behavioral_experiments import completed_experiment, experiment_inputs


@pytest.fixture
def audit_inputs():
    rows = []
    for account in ("A", "B"):
        for day in range(45):
            rows.append({"transaction_ref": f"{account}{day:03}", "account_number": account,
                         "transaction_date": (pd.Timestamp("2023-01-01T12:00:00Z") + pd.Timedelta(days=day)).isoformat(),
                         "amount": "10.00", "currency": "USD", "direction": "DEBIT", "channel": "ONLINE",
                         "timestamp_precision": "second", "source_timezone_known": True,
                         "device_ref": "old", "ip_address": "192.0.2.1", "location": "City",
                         "login_attempts": 1, "account_balance": 1000})
    frame = normalize_frame(pd.DataFrame(rows), "canonical").frame
    truth = pd.DataFrame({"transaction_ref": frame.transaction_ref, "account_number": frame.account_number,
                          "label": 0, "label_type": "synthetic_background", "scenario": "ordinary"})
    truth.loc[truth.transaction_ref.eq("A038"), ["label", "label_type", "scenario"]] = [1, "synthetic_stress", "micro_splitting"]
    truth.loc[truth.transaction_ref.eq("A039"), ["label", "label_type", "scenario"]] = [1, "synthetic_stress", "micro_splitting"]
    truth.loc[truth.transaction_ref.eq("B038"), ["label_type", "scenario"]] = ["synthetic_lookalike", "shopping"]
    return frame, truth, fit_vocabulary(frame.iloc[:35])


@pytest.mark.parametrize("unit", ["AML", "FRAUD"])
def test_every_slice_partitions_units_and_confusion_counts_once(audit_inputs, unit):
    frame, truth, vocabulary = audit_inputs
    annotated = error_audit.unit_annotations(frame, truth.sample(frac=1, random_state=4), unit, vocabulary, 10.0)
    assert annotated.amount_slice.eq("small").all()  # Inclusive boundary.
    scores = annotated[error_audit._keys(unit)].copy()
    scores["score"] = np.where(annotated.label.eq(1), .2, .7)
    joined = error_audit.attach_scores(annotated, scores.sample(frac=1, random_state=7), unit, .5)
    table = error_audit.aggregate_slices(joined, unit, "selected", "fixed")
    for _, dimension in table.groupby("dimension"):
        assert dimension.units.sum() == len(annotated)
        for name in ("tp", "fp", "fn", "tn"):
            assert dimension[name].sum() == joined[name].sum()
    assert joined.tp.sum() == joined.tn.sum() == 0
    assert joined.fn.sum() == annotated.label.sum()
    assert annotated["history_support"].isin(["under_30_prior", "under_30_days", "supported"]).all()


def test_aml_collapses_two_stress_transactions_and_background_into_one_week(audit_inputs):
    frame, truth, vocabulary = audit_inputs
    annotated = error_audit.unit_annotations(frame, truth, "AML", vocabulary, 10.)
    week = annotated[(annotated.account_number == "A") & annotated.as_of.eq(pd.Timestamp("2023-02-12T23:59:59Z"))]
    assert len(week) == 1
    assert week.label.iloc[0] == 1
    assert week.recipe.iloc[0] == "micro_splitting"
    assert week.label_type.iloc[0] == "synthetic_stress"
    assert annotated.label.sum() == 1
    lookalike = annotated[(annotated.account_number == "B") & annotated.as_of.eq(pd.Timestamp("2023-02-12T23:59:59Z"))]
    assert lookalike.label.iloc[0] == 0 and lookalike.recipe.iloc[0] == "shopping"


def test_multiple_stress_recipes_in_one_week_remain_one_mixed_unit(audit_inputs):
    frame, truth, vocabulary = audit_inputs
    truth.loc[truth.transaction_ref.eq("A039"), "scenario"] = "session_change"
    annotated = error_audit.unit_annotations(frame, truth, "AML", vocabulary, 10.)
    positive = annotated[annotated.label.eq(1)]
    assert len(positive) == 1 and positive.recipe.iloc[0] == "mixed_recipes"


@pytest.mark.parametrize("fault", ["missing", "duplicate", "wrong_account", "nan"])
def test_cached_score_mismatch_cannot_silently_change_error_counts(audit_inputs, fault):
    frame, truth, vocabulary = audit_inputs
    annotated = error_audit.unit_annotations(frame, truth, "FRAUD", vocabulary, 10.)
    scores = annotated[error_audit._keys("FRAUD")].assign(score=.6)
    if fault == "missing":
        scores = scores.iloc[:-1]
    elif fault == "duplicate":
        scores.iloc[1] = scores.iloc[0]
    elif fault == "wrong_account":
        scores.loc[0, "account_number"] = "different"
    else:
        scores.loc[0, "score"] = np.nan
    with pytest.raises(ValueError, match="score"):
        error_audit.attach_scores(annotated, scores, "FRAUD", .5)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unknown_label", "wrong_account", "inconsistent_type"])
def test_only_complete_explicit_synthetic_answers_may_be_audited(audit_inputs, fault):
    frame, truth, vocabulary = audit_inputs
    if fault == "missing":
        truth = truth.iloc[:-1]
    elif fault == "duplicate":
        truth.iloc[1] = truth.iloc[0]
    elif fault == "unknown_label":
        truth.loc[0, "label"] = -1
    elif fault == "wrong_account":
        truth.loc[0, "account_number"] = "wrong"
    else:
        truth.loc[0, "label_type"] = "synthetic_stress"
    with pytest.raises(ValueError):
        error_audit.unit_annotations(frame, truth, "AML", vocabulary, 10.)


def test_declared_final_split_is_refused_before_reading_observations_or_scores(tmp_path, monkeypatch):
    validation = tmp_path / "not_validation"
    validation.mkdir()
    (validation / "manifest.json").write_text(json.dumps({"split": "test"}))

    def forbidden(*args, **kwargs):
        raise AssertionError("A final split must never be loaded by this audit")

    monkeypatch.setattr(error_audit, "normalize_csv", forbidden)
    monkeypatch.setattr(pd, "read_parquet", forbidden)
    out = tmp_path / "audit"
    with pytest.raises(ValueError, match="validation split"):
        error_audit.run(validation, tmp_path / "run", tmp_path / "models", out)
    assert not out.exists()


def test_unknown_observations_stay_unknown_in_device_login_slices(audit_inputs):
    frame, truth, vocabulary = audit_inputs
    frame[["device_ref", "ip_address", "location", "login_attempts"]] = None
    annotated = error_audit.unit_annotations(frame, truth, "FRAUD", vocabulary, 10.)
    assert annotated.device_novelty.eq("unknown").all()
    assert annotated.context_novelty.eq("unknown").all()
    assert annotated.login_bucket.eq("unknown").all()


def test_mixed_week_precision_is_observation_metadata_not_a_recipe(audit_inputs):
    frame, truth, vocabulary = audit_inputs
    frame.loc[frame.transaction_ref.eq("A038"), "timestamp_precision"] = "day"
    frame.loc[frame.transaction_ref.eq("A038"), "transaction_date"] = pd.Timestamp("2023-02-08T00:00:00Z")
    frame.loc[frame.transaction_ref.eq("A038"), "source_timezone_known"] = False
    annotated = error_audit.unit_annotations(frame, truth, "AML", vocabulary, 10.)
    week = annotated[(annotated.account_number == "A") & annotated.as_of.eq(pd.Timestamp("2023-02-12T23:59:59Z"))]
    assert week.precision.iloc[0] == "mixed_precision"
    assert week.recipe.iloc[0] == "micro_splitting"


def test_cli_workflow_uses_only_validation_and_preserves_unlabelled_source_scope(completed_experiment, tmp_path, monkeypatch):
    research = completed_experiment
    real_open = Path.open
    forbidden_paths = [research["final"].resolve(), (research["previous"] / "test").resolve()]

    def validation_only(path, mode="r", *args, **kwargs):
        if "r" in mode or "+" in mode:
            resolved = Path(path).resolve()
            assert not any(resolved.is_relative_to(parent) for parent in forbidden_paths)
            assert resolved.name not in {"report.json", "evaluation.json"}, "Final results must not enter this audit"
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", validation_only)
    out = tmp_path / "audit"
    report = error_audit.run(research["previous"] / "validation", research["out"],
                             research["out"].parent / "models", out)
    assert report["fit_performed"] is report["model_or_threshold_changes"] is False
    for unit, roles in report["models"].items():
        for role, metrics in roles.items():
            assert set(metrics["source_unlabelled_review_load"]) == {"units", "units_flagged", "flag_rate"}
            assert sum(metrics[key] for key in ("tp", "fp", "fn", "tn")) == metrics["units"]
            recipe_rows = report["recipe_errors"][unit][role]
            assert sum(row["units"] for row in recipe_rows) == metrics["units"]
    assert json.loads((out / "audit.json").read_text())["scope"].startswith("VALIDATION ONLY")
    assert (out / "audit.md").is_file() and (out / "slices.csv").is_file()
