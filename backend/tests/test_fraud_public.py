"""Independent public benchmark guards: target blindness, chronology, freeze."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from app.ml.behavioral import fraud_public as fp


@pytest.fixture
def public_source(tmp_path):
    rng = np.random.default_rng(11)
    frame = pd.DataFrame(rng.normal(size=(180, 28)), columns=fp.PC)
    frame["Amount"] = rng.lognormal(2, 1, len(frame))
    frame["Time"] = np.repeat(np.arange(60), 3).astype(float)
    frame["Class"] = (np.arange(len(frame)) % 13 == 0).astype(int)
    source = tmp_path / "source.parquet"
    frame.to_parquet(source, index=False)
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"data_set_description": {"id": "1597", "version": "1",
                "parquet_url": "https://data.openml.org/datasets/0000/1597/dataset_1597.pq", "licence": "Public"}}))
    return source, metadata, frame


def test_projection_never_requests_class_and_predictors_exclude_time(public_source, monkeypatch):
    source, _, _ = public_source
    real = fp.pq.read_table
    requested = []
    def guarded(*args, **kwargs):
        requested.append(kwargs["columns"])
        assert "Class" not in kwargs["columns"]
        return real(*args, **kwargs)
    monkeypatch.setattr(fp.pq, "read_table", guarded)
    frame = fp.read_features(source)
    assert requested == [["Time"] + fp.BASE]
    assert "Time" not in fp.BASE and "Time" not in fp.PEER
    assert "Class" not in frame


def test_equal_relative_times_never_cross_partition(public_source):
    source, _, _ = public_source
    frames, manifest = fp.chronological_partitions(fp.read_features(source))
    assert sum(map(len, frames.values())) == 180
    sets = [set(part.Time) for part in frames.values()]
    assert not sets[0] & sets[1] and not sets[0] & sets[2] and not sets[1] & sets[2]
    assert frames["train"].Time.max() < frames["validation"].Time.min()
    assert frames["validation"].Time.max() < frames["final"].Time.min()
    assert manifest["target_used"] is False


def test_training_targets_are_unavailable_and_heldout_targets_align(public_source):
    source, _, original = public_source
    _, manifest = fp.chronological_partitions(fp.read_features(source))
    with pytest.raises(ValueError, match="TRAIN labels"):
        fp.read_labels(source, "train", manifest)
    expected = original[(original.Time > manifest["train_upper_inclusive"]) &
                        (original.Time <= manifest["validation_upper_inclusive"])].Class.to_numpy()
    np.testing.assert_array_equal(fp.read_labels(source, "validation", manifest), expected)


def test_shuffled_duplicate_times_keep_distinct_feature_and_label_alignment(public_source):
    source, _, original = public_source
    shuffled = original.sample(frac=1, random_state=17).reset_index(drop=True)
    shuffled.to_parquet(source, index=False)
    frames, manifest = fp.chronological_partitions(fp.read_features(source))
    ordered = shuffled.sort_values("Time", kind="stable")
    for split in ("validation", "final"):
        if split == "validation":
            expected = ordered[(ordered.Time > manifest["train_upper_inclusive"]) &
                               (ordered.Time <= manifest["validation_upper_inclusive"])]
        else:
            expected = ordered[ordered.Time > manifest["validation_upper_inclusive"]]
        np.testing.assert_array_equal(fp.read_labels(source, split, manifest), expected.Class)
        np.testing.assert_array_equal(frames[split].V1, expected.V1)


def test_schema_and_nonfinite_input_are_rejected(public_source, tmp_path):
    _, _, frame = public_source
    frame.loc[0, "V3"] = np.inf
    path = tmp_path / "bad.parquet"
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="finite"):
        fp.read_features(path)
    frame["fraud_hint"] = frame.Class
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="schema"):
        fp.read_features(path)


def test_peer_profile_has_no_future_dependence_and_preserves_opaque_components(public_source):
    source, _, _ = public_source
    frames, _ = fp.chronological_partitions(fp.read_features(source))
    profile = fp.fit_peers(frames["train"], seed=2, clusters=3)
    before = json.dumps(profile, sort_keys=True)
    output = fp.peer_features(frames["validation"], profile)
    changed = frames["validation"].copy()
    changed.loc[0, fp.PC + ["Amount"]] = 1e6
    other = fp.peer_features(changed, profile)
    assert json.dumps(profile, sort_keys=True) == before
    assert_frame_equal(output[fp.BASE], frames["validation"][fp.BASE])
    assert_frame_equal(output.iloc[1:], other.iloc[1:])
    assert np.isfinite(other.to_numpy()).all()
    assert set(output) == set(fp.BASE + fp.PEER)


@pytest.mark.parametrize("slice_name", ["all", "small_amount", "relative_time_block:1"])
def test_higher_precision_with_lower_reference_coverage_is_rejected(slice_name):
    base = {key: {"tp": 8, "fp": 40, "precision": 8/48} for key in
            ("all", "small_amount", "relative_time_block:0", "relative_time_block:1", "relative_time_block:2")}
    candidate = {key: {"tp": 8, "fp": 2, "precision": .8} for key in base}
    candidate[slice_name]["tp"] = 7
    choice = fp.select({fp.REFERENCE: base, "better_looking__auto": candidate})
    assert choice["selected"] == fp.REFERENCE and not choice["improvement_found"]


def test_preserved_coverage_with_fewer_false_flags_is_selected():
    baseline = {"all": {"tp": 8, "fp": 40, "precision": 8/48},
                "small_amount": {"tp": 2, "fp": 10, "precision": 2/12}}
    changed = {"all": {"tp": 8, "fp": 2, "precision": .8},
               "small_amount": {"tp": 2, "fp": 1, "precision": 2/3}}
    choice = fp.select({fp.REFERENCE: baseline, "candidate__auto": changed})
    assert choice["selected"] == "candidate__auto" and choice["improvement_found"]


def test_entire_protocol_fits_before_validation_and_freezes_before_final(public_source, tmp_path, monkeypatch):
    source, metadata, _ = public_source
    out = tmp_path / "research"
    fits, labels = [], []
    real_fit, real_labels = fp.fit_model, fp.read_labels
    def guarded_fit(frame, names, **kwargs):
        assert not labels
        assert not {"Class", "Time", "label", "target"} & set(frame)
        assert not {"y", "Class", "labels", "target"} & set(kwargs)
        fits.append(names)
        return real_fit(frame, names, **kwargs)
    def guarded_labels(path, split, partition):
        assert len(fits) == 3
        if split == "final":
            assert labels == ["validation"]
            frozen = json.loads((out / "frozen.json").read_text())
            fp.verify_frozen(frozen)
            assert (out / "selection.json").is_file()
        labels.append(split)
        return real_labels(path, split, partition)
    monkeypatch.setattr(fp, "fit_model", guarded_fit)
    monkeypatch.setattr(fp, "read_labels", guarded_labels)
    report = fp.run(source, metadata, out, seed=4, estimators=3)
    assert len(fits) == 3 and labels == ["validation", "final"]
    assert report["fit_uses_targets"] is False
    assert report["freeze_precedes_final_labels"] is True
    assert report["no_database_registration_or_promotion"] is True
    fp.verify_frozen(report["frozen"])
    assert report["protocol"]["currency"].startswith("source units unknown")
    assert report["final_labels_first_parsed_at_utc"] >= report["frozen"]["frozen_at_utc"]
    with pytest.raises(FileExistsError):
        fp.run(source, metadata, out)
    source.write_bytes(source.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="Frozen input changed"):
        fp.verify_frozen(report["frozen"])


def test_source_tampering_during_training_is_not_blessed_at_freeze(public_source, tmp_path, monkeypatch):
    source, metadata, _ = public_source
    real_fit = fp.fit_model
    def guarded(*args, **kwargs):
        result = real_fit(*args, **kwargs)
        metadata.write_text(metadata.read_text() + " ")
        return result
    monkeypatch.setattr(fp, "fit_model", guarded)
    out = tmp_path / "tampered"
    with pytest.raises(ValueError, match="Frozen input changed"):
        fp.run(source, metadata, out, estimators=2)
    assert not (out / "frozen.json").exists()
    assert not (out / "final_scores.parquet").exists()


@pytest.mark.parametrize("artefact", ["model.txt", "model_card.json"])
def test_model_or_card_tampering_during_later_fit_is_rejected(public_source, tmp_path, monkeypatch, artefact):
    source, metadata, _ = public_source
    out = tmp_path / "model_tamper"
    real_fit = fp.fit_model
    calls = 0
    def guarded(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = real_fit(*args, **kwargs)
        if calls == 2:
            path = out / "models" / "raw_256" / artefact
            path.write_text(path.read_text() + " ")
        return result
    monkeypatch.setattr(fp, "fit_model", guarded)
    with pytest.raises(ValueError, match="Frozen input changed"):
        fp.run(source, metadata, out, estimators=2)
    assert not (out / "frozen.json").exists()


@pytest.mark.parametrize("artefact", ["model.txt", "model_card.json"])
def test_model_or_card_tampering_during_validation_is_rejected(public_source, tmp_path, monkeypatch, artefact):
    source, metadata, _ = public_source
    out = tmp_path / "val_tamper"
    real_labels = fp.read_labels
    def guarded(path, split, partition):
        values = real_labels(path, split, partition)
        if split == "validation":
            protected = out / "models" / "raw_256" / artefact
            protected.write_text(protected.read_text() + " ")
        return values
    monkeypatch.setattr(fp, "read_labels", guarded)
    with pytest.raises(ValueError, match="Frozen input changed"):
        fp.run(source, metadata, out, estimators=2)
    assert not (out / "frozen.json").exists()
    assert not (out / "report.json").exists()


def test_frozen_manifest_mutation_during_final_is_rejected(public_source, tmp_path, monkeypatch):
    source, metadata, _ = public_source
    out = tmp_path / "manifest_tamper"
    real_labels = fp.read_labels
    def guarded(path, split, partition):
        values = real_labels(path, split, partition)
        if split == "final":
            manifest = out / "frozen.json"
            manifest.write_text(manifest.read_text() + " ")
        return values
    monkeypatch.setattr(fp, "read_labels", guarded)
    with pytest.raises(ValueError, match="Frozen manifest changed"):
        fp.run(source, metadata, out, estimators=2)
    assert not (out / "report.json").exists()

