"""Target-ordering, full-edge chronology and coverage guards for AML research."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml.behavioral import aml_temporal as temporal
from app.ml.behavioral import aml_network_benchmark as old


@pytest.fixture
def network(tmp_path):
    raw = tmp_path / "raw"
    (raw / old.DATA).mkdir(parents=True)
    records = []
    for window in range(6):
        for sender in range(12):
            for offset in (1, 2, 4):
                positive = sender % 4 == 0 and offset == 1
                records.append({"tran_id": str(len(records)), "orig_acct": f"A{sender}",
                                "bene_acct": f"A{(sender + offset + window) % 12}", "tx_type": "TRANSFER",
                                "base_amt": 1. + sender + offset,
                                "tran_timestamp": (pd.Timestamp("2020-01-01", tz="UTC") + pd.Timedelta(days=window * 2)).isoformat(),
                                "is_sar": positive, "alert_id": str(window * 10 + sender) if positive else "-1"})
    transactions = pd.DataFrame(records)
    transactions.to_csv(raw / old.TRANSACTIONS, index=False, compression="gzip")
    patterns = transactions.loc[transactions.is_sar, ["tran_id", "alert_id", "is_sar"]].copy()
    patterns["alert_type"] = np.where(patterns.alert_id.astype(int) % 8 == 0, "cycle", "scatter_gather")
    patterns.to_csv(raw / old.PATTERNS, index=False, compression="gzip")
    pd.DataFrame({"acct_id": [f"A{i}" for i in range(12)], "acct_rptng_crncy": "USD"}).to_csv(raw / old.ACCOUNTS, index=False, compression="gzip")
    old.write_json(raw / "provenance.json", {"source": "private test fixture", "files": {name: old.sha256(raw / name) for name in [old.TRANSACTIONS, old.PATTERNS, old.ACCOUNTS]}})
    return raw


def run(raw, out):
    return temporal.run(raw, out, estimators=2, window_days=2, train_windows=2, validation_windows=2)


def test_complete_network_chronology_never_drops_account_edges(network):
    observations = old.load_transactions(network)
    parts, meta = temporal.temporal_partition(observations, window_days=2, train_windows=2, validation_windows=2)
    assert sum(map(len, parts.values())) == len(observations)
    assert meta["excluded_transactions"] == meta["excluded_cross_group_edges"] == 0
    assert parts["train"].timestamp.max() < parts["validation"].timestamp.min() < parts["final"].timestamp.min()
    assert set(parts["train"].transaction_id).isdisjoint(parts["validation"].transaction_id)
    assert meta["splits"]["final"]["unseen_train_accounts"] == 0
    assert meta["train_windows"] == [0, 1] and meta["final_windows"] == [4, 5]


def test_all_fits_before_validation_and_final_targets_only_after_freeze(network, tmp_path, monkeypatch):
    out = tmp_path / "out"
    original_fit, original_read = temporal.fit_model, temporal.read_labels
    fits, reads = [], []
    observations = old.load_transactions(network)
    parts, _ = temporal.temporal_partition(observations, window_days=2, train_windows=2, validation_windows=2)

    def guarded_fit(frame, names, **kwargs):
        assert not reads
        assert not set(names) & {"account", "window", "is_sar", "alert_id", "base_amt"}
        assert frame.window.max() == 1
        fits.append(list(names))
        return original_fit(frame, names, **kwargs)

    def guarded_read(raw, ids):
        assert len(fits) == 3
        if not reads:
            assert ids == set(parts["validation"].transaction_id)
            assert (out / "fitted.json").exists()
            assert not (out / "frozen.json").exists()
        else:
            frozen = json.loads((out / "frozen.json").read_text())
            old.verify_hashes(frozen["files"])
            assert ids == (set(parts["final"].transaction_id) if len(reads) == 1 else set(observations.transaction_id))
        reads.append(ids)
        return original_read(raw, ids)

    monkeypatch.setattr(temporal, "fit_model", guarded_fit)
    monkeypatch.setattr(temporal, "read_labels", guarded_read)
    report = run(network, out)
    assert len(reads) == 3
    assert report["fit_uses_targets"] is False
    assert report["selection_uses_validation_labels"] is True
    assert report["final_retuned"] is False
    assert report["state"] == "DRAFT"
    assert report["frozen_at"] <= report["final_started_at"]
    old.verify_hashes(report["frozen"]["files"])


def test_future_windows_do_not_change_past_features_or_train_peer_profile(network):
    frame = old.load_transactions(network)
    prefix = frame.loc[frame.timestamp.lt(pd.Timestamp("2020-01-05", tz="UTC"))]
    early = temporal.build_features(prefix, base_date="2020-01-01T00:00:00Z", window_days=2)
    full = temporal.build_features(frame, base_date="2020-01-01T00:00:00Z", window_days=2)
    pd.testing.assert_frame_equal(early, full.loc[full.window.lt(2)].reset_index(drop=True))
    profile = temporal.fit_peer_profile(early)
    assert profile == temporal.fit_peer_profile(full.loc[full.window.lt(2)])
    # Later data is transformed using the same learned bins without modifying them.
    before = json.dumps(profile, sort_keys=True)
    temporal.peer_view(full, profile)
    assert json.dumps(profile, sort_keys=True) == before


def test_target_or_amount_columns_cannot_change_graph_features(network):
    frame = old.load_transactions(network)
    original = temporal.build_features(frame, base_date="2020-01-01T00:00:00Z", window_days=2)
    other = temporal.build_features(frame.assign(is_sar=True, alert_type="leak", alert_id=9000, base_amt=10**20),
                                    base_date="2020-01-01T00:00:00Z", window_days=2)
    pd.testing.assert_frame_equal(original, other)


@pytest.mark.parametrize("guard", ["tp", "low_amount_positive_tp", "recipe_tp", "pattern_or_fragment_tp"])
def test_precision_gain_cannot_override_coverage_loss(guard):
    baseline = {"precision": .1, "tp": 5, "fp": 45, "low_amount_positive_tp": 3, "recipe_tp": {"cycle": 2}, "pattern_or_fragment_tp": {"cycle": 2}}
    candidate = {**baseline, "precision": .9, "fp": 1}
    candidate[guard] = {"cycle": 1} if guard in ("recipe_tp", "pattern_or_fragment_tp") else baseline[guard] - 1
    selected = temporal.choose({"graph256__auto": baseline, "better_precision": candidate})
    assert selected["selected"] == "graph256__auto"
    assert "better_precision" in selected["rejected"]


def test_pattern_fragments_are_never_counted_as_complete():
    obs = pd.DataFrame({"transaction_id": ["a", "b", "c"], "sender": ["A", "B", "D"], "receiver": ["B", "A", "E"], "window": [4, 4, 5]})
    scores = pd.DataFrame({"account": ["A", "B", "D", "E"], "window": [4, 4, 5, 5], "score": [.9, .2, .2, .2]})
    patterns = pd.DataFrame({"tran_id": ["a", "b", "c", "outside"], "alert_id": ["1", "1", "2", "2"], "alert_type": ["cycle"] * 4})
    result = temporal.pattern_evaluation(obs, scores, patterns.iloc[:3], patterns, .5)["cycle"]
    assert result["complete_patterns"] == 1 and result["boundary_fragments"] == 1
    assert result["detected_complete_patterns"] == 1 and result["complete_pattern_recall"] == 1.
    assert result["observed_fragment_recall"] == .5


def test_empty_diagnostic_metrics_remain_null():
    values = old._metrics([], [], .5)
    assert values["units"] == 0 and values["precision"] is values["recall"] is values["flag_rate"] is None


@pytest.mark.parametrize("changed", ["source", "protocol", "provenance"])
def test_mutation_during_fit_cannot_be_reblessed(network, tmp_path, monkeypatch, changed):
    out = tmp_path / "out"
    original = temporal.fit_model
    calls = 0

    def tamper(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            path = {"source": network / old.TRANSACTIONS, "protocol": out / "protocol.json", "provenance": network / "provenance.json"}[changed]
            path.write_bytes(path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(temporal, "fit_model", tamper)
    monkeypatch.setattr(temporal, "read_labels", lambda *a: pytest.fail("No target reads after initial expectation changes"))
    with pytest.raises(ValueError, match="Frozen benchmark"):
        run(network, out)
    assert not (out / "frozen.json").exists() and not (out / "report.json").exists()


def test_postfreeze_selection_mutation_rejects_report(network, tmp_path, monkeypatch):
    out = tmp_path / "out"
    original = temporal.read_labels
    calls = 0

    def tamper(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            path = out / "selection.json"
            path.write_bytes(path.read_bytes() + b"\n")
        return original(*args)

    monkeypatch.setattr(temporal, "read_labels", tamper)
    with pytest.raises(ValueError, match="Frozen benchmark"):
        run(network, out)
    assert not (out / "report.json").exists()


def test_source_tamper_fails_before_output(network, tmp_path, monkeypatch):
    path = network / old.TRANSACTIONS
    path.write_bytes(path.read_bytes() + b"bad")
    monkeypatch.setattr(temporal, "fit_model", lambda *a, **k: pytest.fail("No fit after bad source"))
    with pytest.raises(ValueError, match="Frozen benchmark"):
        run(network, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_existing_outputs_are_immutable(network, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "marker").write_text("keep")
    with pytest.raises(FileExistsError, match="immutable"):
        run(network, out)
    assert (out / "marker").read_text() == "keep"
