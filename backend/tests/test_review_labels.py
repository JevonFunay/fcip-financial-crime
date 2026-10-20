"""Blinding, sample design, review integrity and honest unresolved outcomes."""
import copy
import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml.behavioral import review_labels as review
from app.ml.behavioral.model import fit_model


@pytest.fixture
def inputs():
    observations = pd.DataFrame({
        "transaction_ref": [f"T{i:03}" for i in range(80)],
        "account_number": [f"A{i // 10}" for i in range(80)],
        "transaction_date": [pd.Timestamp("2025-01-01", tz="UTC") + pd.Timedelta(days=i % 10 * 8) for i in range(80)],
        "amount": [10.0 if i % 2 else 200.0 for i in range(80)], "currency": "USD", "direction": "DEBIT",
        "channel": "ONLINE", "timestamp_precision": "day", "source_timezone_known": False,
        "login_attempts": 1, "device_ref": [f"D{i // 10}" for i in range(80)]})
    fraud = observations[["transaction_ref"]].assign(score=[.6 if i % 4 < 2 else .4 for i in range(80)], threshold=.5)
    dates = observations.transaction_date
    obs = observations.assign(as_of=dates.dt.floor("D") - pd.to_timedelta(dates.dt.dayofweek, unit="D") + pd.Timedelta(days=7) - pd.Timedelta(seconds=1))
    aml = obs[["account_number", "as_of"]].drop_duplicates().reset_index(drop=True)
    aml["score"], aml["threshold"] = [.6 if i % 4 < 2 else .4 for i in range(len(aml))], .5
    return observations, {"AML": aml, "FRAUD": fraud}


def export(inputs, path, size=40):
    obs, scored = inputs
    return review.export_review_packet(obs, scored, path, sample_size=size, seed=42, small_amount_cutoff=20,
                                      input_hashes={"source": "a" * 64}, model_metadata={"AML": {"state": "DRAFT"}, "FRAUD": {"state": "DRAFT"}})


def responses(path, tmp_path, status="NORMAL"):
    data = pd.read_csv(path / "annotations_template.csv", dtype=str, keep_default_na=False)
    if status != "PENDING":
        data["status"], data["reason"], data["reviewer_id"], data["reviewed_at"] = status, "Review of visible context", "analyst-1", "2026-10-20T09:00:00+07:00"
    dest = tmp_path / "responses.csv"
    data.to_csv(dest, index=False)
    return dest, data


def test_packet_is_blinded_and_all_initial_labels_unknown(inputs, tmp_path):
    path = tmp_path / "packet"
    manifest = export(inputs, path)
    assert manifest["completed"] == 0 and manifest["pending"] == 40
    records = [json.loads(line) for line in (path / "packet.jsonl").read_text().splitlines()]
    prohibited = {"score", "threshold", "flagged", "small", "stratum", "label", "scenario", "sampling_weight", "domain_outcome"}
    def check(value):
        if isinstance(value, dict):
            assert not prohibited.intersection(value)
            for child in value.values():
                check(child)
        if isinstance(value, list):
            for child in value:
                check(child)
    check(records)
    assert all("+00:00" not in row["transaction_date"] for case in records for row in case["observations"])
    data = pd.read_csv(path / "annotations_template.csv", keep_default_na=False)
    assert data.status.eq("PENDING").all() and data.domain_outcome.eq("UNKNOWN").all()
    assert data[["reason", "reviewer_id", "reviewed_at"]].eq("").all().all()
    assert "not encrypted" in json.loads((path / "protocol.json").read_text())["blinding_boundary"]


def test_every_population_stratum_has_nonzero_inclusion_probability(inputs, tmp_path):
    path = tmp_path / "packet"
    export(inputs, path)
    protocol = json.loads((path / "protocol.json").read_text())
    mapping = pd.read_csv(path / "sealed_mapping.csv")
    assert set(protocol["strata"]) == set(mapping.stratum)
    assert mapping.groupby("unit").size().eq(20).all()
    for name, item in protocol["strata"].items():
        sample = mapping[mapping.stratum.eq(name)]
        assert len(sample) == item["sample_size"] > 0
        assert 0 < item["inclusion_probability"] <= 1
        assert np.allclose(sample.inclusion_probability, item["sample_size"] / item["population_size"])
        assert sample.sampling_weight.sum() == pytest.approx(item["population_size"])
    assert set(mapping.flagged) == {True, False}
    assert set(mapping.small) == {True, False}


def test_deterministic_export_and_no_overwrite(inputs, tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    export(inputs, first)
    export(inputs, second)
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {p.name: p.read_bytes() for p in second.iterdir()}
    with pytest.raises(FileExistsError):
        export(inputs, first)


def test_pending_reports_null_precision_and_full_unresolved(inputs, tmp_path):
    path = tmp_path / "packet"
    export(inputs, path)
    response, _ = responses(path, tmp_path, "PENDING")
    report = review.import_reviews(path, response, tmp_path / "imported")
    assert report["fraud_precision"] is None and report["aml_crime_precision"] is None
    for unit in report["unit_results"].values():
        assert unit["estimated_population_anomaly_precision"] is None
        assert unit["weighted_anomalous_share_of_resolved_flags"] is None
        assert unit["approximate_95pct_design_interval"] is None
        assert unit["conservative_95pct_sampling_interval"] is None
        assert unit["unresolved_sample_fraction"] == unit["unresolved_flagged_weight_fraction"] == 1
        assert unit["unresolved_review_bounds_on_sample_weighted_anomaly_share"] == [0, 1]
    with pytest.raises(FileExistsError):
        review.import_reviews(path, response, tmp_path / "imported")


def test_insufficient_evidence_excluded_without_becoming_normal(inputs, tmp_path):
    path = tmp_path / "packet"
    export(inputs, path)
    response, data = responses(path, tmp_path, "INSUFFICIENT_EVIDENCE")
    mapping = pd.read_csv(path / "sealed_mapping.csv")
    first_flagged = mapping.loc[mapping.flagged & mapping.unit.eq("FRAUD"), "review_id"].iloc[0]
    data.loc[data.review_id.eq(first_flagged), "status"] = "ANOMALOUS"
    data.to_csv(response, index=False)
    joined, protocol = review.validate_responses(path, response)
    report = review.evaluate_responses(joined, protocol)
    fraud = report["unit_results"]["FRAUD"]
    assert fraud["flagged_resolved"] == 1
    assert fraud["weighted_anomalous_share_of_resolved_flags"] == 1
    assert fraud["estimated_population_anomaly_precision"] is None
    assert 0 < fraud["unresolved_flagged_weight_fraction"] < 1
    assert report["unit_results"]["AML"]["flagged_resolved"] == 0


def test_resolved_population_estimator_is_design_weighted(inputs, tmp_path):
    path = tmp_path / "packet"
    export(inputs, path)
    response, data = responses(path, tmp_path)
    mapping = pd.read_csv(path / "sealed_mapping.csv")
    chosen = mapping.loc[mapping.flagged & mapping.small, "review_id"]
    data.loc[data.review_id.isin(chosen), "status"] = "ANOMALOUS"
    data.to_csv(response, index=False)
    joined, protocol = review.validate_responses(path, response)
    report = review.evaluate_responses(joined, protocol)
    for unit, result in report["unit_results"].items():
        flagged = joined[joined.unit.eq(unit) & joined.flagged]
        expected = flagged.loc[flagged.status.eq("ANOMALOUS"), "sampling_weight"].sum() / flagged.sampling_weight.sum()
        assert result["estimated_population_anomaly_precision"] == pytest.approx(expected)
        low, high = result["conservative_95pct_sampling_interval"]
        assert 0 <= low <= expected <= high <= 1


def test_zero_observed_anomalies_does_not_make_conservative_interval_certain(inputs, tmp_path):
    path = tmp_path / "packet"
    export(inputs, path)
    response, _ = responses(path, tmp_path)
    joined, protocol = review.validate_responses(path, response)
    report = review.evaluate_responses(joined, protocol)
    for result in report["unit_results"].values():
        assert result["estimated_population_anomaly_precision"] == 0
        assert result["conservative_95pct_sampling_interval"][1] > 0


@pytest.mark.parametrize("field,value", [("reviewer_id", ""), ("reason", "short"), ("reviewed_at", "2026-10-20T09:00:00"),
                                        ("reviewed_at", "yesterday"), ("domain_outcome", "CONFIRMED_FRAUD"), ("status", "FRAUD")])
def test_invalid_completed_reviews_rejected(inputs, tmp_path, field, value):
    path = tmp_path / "packet"
    export(inputs, path)
    response, data = responses(path, tmp_path)
    data.loc[0, field] = value
    data.to_csv(response, index=False)
    with pytest.raises(ValueError):
        review.validate_responses(path, response)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "pending_metadata"])
def test_review_set_is_exact_and_pending_is_uncompleted(inputs, tmp_path, mutation):
    path = tmp_path / "packet"
    export(inputs, path)
    response, data = responses(path, tmp_path, "PENDING")
    if mutation == "missing":
        data = data.iloc[:-1]
    if mutation == "duplicate":
        data.loc[0, "review_id"] = data.loc[1, "review_id"]
    if mutation == "extra":
        data["model_flag"] = "leak"
    if mutation == "pending_metadata":
        data.loc[0, "reason"] = "An unfinished judgement"
    data.to_csv(response, index=False)
    with pytest.raises(ValueError):
        review.validate_responses(path, response)


@pytest.mark.parametrize("filename", ["packet.jsonl", "protocol.json", "sealed_mapping.csv", "annotations_template.csv"])
def test_packet_tampering_rejected(inputs, tmp_path, filename):
    path = tmp_path / "packet"
    export(inputs, path)
    with (path / filename).open("a") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="integrity"):
        review.verify_packet(path)


def test_strictly_prior_context_has_no_same_day_future_observations(inputs, tmp_path):
    path = tmp_path / "packet"
    export(inputs, path, size=160)
    records = [json.loads(line) for line in (path / "packet.jsonl").read_text().splitlines()]
    for case in records:
        first = min(pd.Timestamp(row["transaction_date"]) for row in case["observations"])
        for row in case["prior_context"]:
            assert pd.Timestamp(row["transaction_date"]).date() < first.date()


def test_scoring_population_must_be_exact(inputs, tmp_path):
    observations, scored = inputs
    bad = copy.deepcopy(scored)
    bad["FRAUD"] = bad["FRAUD"].iloc[:-1]
    with pytest.raises(ValueError, match="exactly"):
        export((observations, bad), tmp_path / "packet")
    with pytest.raises(ValueError, match="strata"):
        export(inputs, tmp_path / "small", size=2)


def test_no_fit_signature_accepts_review_labels():
    assert "labels" not in inspect.signature(fit_model).parameters
    assert "fit_model(" not in inspect.getsource(review)


def test_display_preserves_source_clock_precision():
    frame = pd.DataFrame({"transaction_date": pd.to_datetime(["2025-01-01T00:00:00Z", "2025-01-02T04:05:00Z", "2025-01-03T04:05:06Z"]),
                          "timestamp_precision": ["day", "minute", "second"], "source_timezone_known": [False, False, True]})
    records = review._records(frame)
    assert [row["transaction_date"] for row in records] == ["2025-01-01", "2025-01-02T04:05", "2025-01-03T04:05:06+00:00"]


def test_review_context_keeps_whole_oldest_availability_tie(inputs, tmp_path):
    observations, _ = inputs
    observations = observations.iloc[:40].copy()
    observations["account_number"] = "A"
    observations["transaction_date"] = pd.Timestamp("2025-01-01", tz="UTC")
    observations.loc[39, "transaction_date"] = pd.Timestamp("2025-02-01", tz="UTC")
    fraud = observations[["transaction_ref"]].assign(score=.6, threshold=.5)
    dates = observations.transaction_date
    observations["as_of"] = dates.dt.floor("D") - pd.to_timedelta(dates.dt.dayofweek, unit="D") + pd.Timedelta(days=7) - pd.Timedelta(seconds=1)
    aml = observations[["account_number", "as_of"]].drop_duplicates().assign(score=.6, threshold=.5)
    packet = tmp_path / "packet"
    export((observations, {"AML": aml, "FRAUD": fraud}), packet, size=42)
    cases = [json.loads(line) for line in (packet / "packet.jsonl").read_text().splitlines()]
    target = next(case for case in cases if case["unit"] == "FRAUD" and case["observations"][0]["transaction_ref"] == "T039")
    assert target["strictly_prior_observations_count"] == 39
    assert len(target["prior_context"]) == 39


def test_source_export_rejects_inputs_changed_after_export(inputs, tmp_path, monkeypatch):
    from app.ml.behavioral import generate
    from app.ml.behavioral.features import fit_vocabulary
    from app.ml.behavioral.research_features import build_experiment_features

    observations, _ = inputs
    source = tmp_path / "source.csv"
    source.write_text("private source bytes\n")
    model_root = tmp_path / "models"
    vocabulary = fit_vocabulary(observations)
    hashes = {str(source.resolve()): review.sha256(source)}
    for unit in ("AML", "FRAUD"):
        folder = model_root / unit.lower()
        folder.mkdir(parents=True)
        frame = build_experiment_features(observations, unit, vocabulary, "full")
        names = [name for name in frame if name not in {"transaction_ref", "account_number", "as_of"}]
        text, _ = fit_model(frame, names, seed=1, estimators=2, max_samples=4)
        (folder / "model.txt").write_text(text)
        (folder / "model_card.json").write_text(json.dumps({"state": "DRAFT", "unit": unit, "targets_used": False,
            "specification": {"representation": "full"}, "vocabulary": vocabulary, "threshold_candidates": {"auto": .5}}))
        for name in ("model.txt", "model_card.json"):
            hashes[str((folder / name).resolve())] = review.sha256(folder / name)
    frozen = tmp_path / "frozen.json"
    frozen.write_text(json.dumps({"files": hashes}))
    monkeypatch.setattr(review, "load_observations", lambda *args: (observations, {}))
    monkeypatch.setattr(generate, "source_partitions", lambda *args: {"train": ["A0", "A1", "A2", "A3"], "validation": ["A4", "A5", "A6", "A7"], "test": []})
    real_export = review.export_review_packet
    def tamper_after(*args, **kwargs):
        manifest = real_export(*args, **kwargs)
        source.write_text("changed after scoring\n")
        return manifest
    monkeypatch.setattr(review, "export_review_packet", tamper_after)
    with pytest.raises(ValueError, match="changed"):
        review.export_source_validation(source, model_root, frozen, tmp_path / "packet", sample_size=20)
