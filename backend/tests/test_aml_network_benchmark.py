"""Scientific guards for the independent offline sender/receiver benchmark.

No application database, original FCIP final test, or user transactions are used.
The fixture has synthetic simulator labels, which must remain evaluation-only.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from app.ml.behavioral import aml_network_benchmark as benchmark


@pytest.fixture
def network_source(tmp_path):
    raw = tmp_path / "raw"
    (raw / benchmark.DATA).mkdir(parents=True)
    rows = []
    for window in range(8):
        for sender in range(60):
            for offset in (1, 2, 4):
                positive = sender % 13 == 0 and offset == 1
                rows.append({
                    "tran_id": str(len(rows)), "orig_acct": f"A{sender}",
                    "bene_acct": f"A{(sender + offset + window) % 60}", "tx_type": "TRANSFER",
                    "base_amt": 12.34 + sender,
                    "tran_timestamp": (pd.Timestamp("2020-01-01", tz="UTC") + pd.Timedelta(days=2 * window)).isoformat(),
                    "is_sar": positive, "alert_id": str(sender % 2) if positive else "-1",
                })
    transactions = pd.DataFrame(rows)
    transactions.to_csv(raw / benchmark.TRANSACTIONS, index=False, compression="gzip")
    patterns = transactions.loc[transactions.is_sar, ["tran_id", "alert_id", "is_sar"]].copy()
    patterns["alert_type"] = patterns.alert_id.map({"0": "cycle", "1": "scatter_gather"})
    patterns.to_csv(raw / benchmark.PATTERNS, index=False, compression="gzip")
    pd.DataFrame({"acct_id": [f"A{i}" for i in range(60)], "acct_rptng_crncy": "USD",
                  "prior_sar_count": 999}).to_csv(raw / benchmark.ACCOUNTS, index=False, compression="gzip")
    benchmark.write_json(raw / "provenance.json", {
        "source": "test fixture, not downloaded public data",
        "files": {name: benchmark.sha256(raw / name) for name in
                  (benchmark.TRANSACTIONS, benchmark.PATTERNS, benchmark.ACCOUNTS)},
    })
    return raw


def _run(raw, out):
    return benchmark.run(raw, out, seed=20261016, estimators=2, max_samples=16,
                         window_days=2, train_account_fraction=.7, train_time_fraction=.5)


def test_targets_are_read_only_after_estimator_and_threshold_freeze(network_source, tmp_path, monkeypatch):
    out = tmp_path / "result"
    original_read, original_fit = pd.read_csv, benchmark.fit_model
    fit_called = False
    target_reads = []

    def guarded_read(path, *args, **kwargs):
        columns = set(kwargs.get("usecols", []))
        if columns & {"is_sar", "alert_id", "alert_type", "prior_sar_count"}:
            assert fit_called
            frozen = json.loads((out / "frozen.json").read_text())
            benchmark.verify_hashes(frozen["files"])
            target_reads.append(Path(path))
        assert "prior_sar_count" not in columns
        return original_read(path, *args, **kwargs)

    def guarded_fit(frame, names, **kwargs):
        nonlocal fit_called
        assert list(names) == benchmark.FEATURES
        assert set(frame) == {"account", "window", "as_of", *benchmark.FEATURES}
        assert not set(names) & {"account", "window", "as_of", "base_amt", "is_sar", "alert_id"}
        assert not target_reads
        fit_called = True
        return original_fit(frame, names, **kwargs)

    monkeypatch.setattr(pd, "read_csv", guarded_read)
    monkeypatch.setattr(benchmark, "fit_model", guarded_fit)
    report = _run(network_source, out)
    assert len(target_reads) == 2
    assert report["fit_uses_targets"] is False
    assert report["threshold_selection_uses_targets"] is False
    assert report["state"] == "DRAFT"
    assert report["no_database_registration_or_promotion"] is True
    assert report["model"]["amount_used_as_predictor"] is False
    assert "2-day" in json.loads((out / "protocol.json").read_text())["unit"]
    benchmark.verify_hashes(report["frozen"]["files"])
    assert str((network_source / benchmark.TRANSACTIONS).resolve()) in report["frozen"]["files"]


def test_partition_has_disjoint_accounts_times_and_transaction_ids(network_source):
    observations = benchmark.load_transactions(network_source)
    splits, report = benchmark.graph_partition(observations, seed=20261016, account_fraction=.7,
                                              time_fraction=.5, window_days=2)
    train, holdout = splits["train"], splits["holdout"]
    assert not (set(train.sender) | set(train.receiver)) & (set(holdout.sender) | set(holdout.receiver))
    assert train.timestamp.max() < holdout.timestamp.min()
    assert not set(train.transaction_id) & set(holdout.transaction_id)
    assert report["weak_components"] == 1
    assert report["component_disjoint_possible"] is False
    excluded = (report["excluded_cross_group_transactions"] + report["excluded_train_accounts_after_cutoff"]
                + report["excluded_holdout_accounts_before_cutoff"])
    assert excluded == report["excluded_total"]
    assert len(train) + len(holdout) + excluded == len(observations)


def test_graph_features_use_supplied_edges_and_not_ids_amounts_or_labels():
    frame = pd.DataFrame({"transaction_id": ["0", "1", "2", "3"], "sender": ["A", "B", "C", "B"],
                          "receiver": ["B", "C", "A", "A"],
                          "timestamp": pd.to_datetime(["2020-01-01"] * 4, utc=True)})
    features = benchmark.build_graph_features(frame, base_date="2020-01-01T00:00:00Z", window_days=2)
    a = features.set_index("account").loc["A"]
    assert a.IN_COUNT == 2 and a.OUT_COUNT == 1
    assert a.RECIPROCAL_PEERS == 1 and a.DIRECTED_TRIANGLES == 1
    assert a.IN_DISTINCT_PEERS == 2 and a.OUT_DISTINCT_PEERS == 1
    renamed = frame.assign(sender=frame.sender.map({"A": "X", "B": "Y", "C": "Z"}),
                           receiver=frame.receiver.map({"A": "X", "B": "Y", "C": "Z"}),
                           base_amt=10**12, is_sar=True, prior_sar_count=999, alert_id="leak")
    other = benchmark.build_graph_features(renamed, base_date="2020-01-01T00:00:00Z", window_days=2)
    pd.testing.assert_frame_equal(features[benchmark.FEATURES], other[benchmark.FEATURES])
    assert features.as_of.eq(pd.Timestamp("2020-01-02T23:59:59Z")).all()


def test_future_window_does_not_change_past_observation_features(network_source):
    frame = benchmark.load_transactions(network_source)
    prefix = frame.loc[frame.timestamp < pd.Timestamp("2020-01-05", tz="UTC")]
    early = benchmark.build_graph_features(prefix, base_date="2020-01-01T00:00:00Z", window_days=2)
    whole = benchmark.build_graph_features(frame, base_date="2020-01-01T00:00:00Z", window_days=2)
    pd.testing.assert_frame_equal(early, whole.loc[whole.as_of < pd.Timestamp("2020-01-05", tz="UTC")].reset_index(drop=True))


def test_metrics_do_not_fabricate_recall_or_auc_without_positive_labels():
    result = benchmark._metrics([False, False], [.8, .2], .5)
    assert result["fp"] == 1 and result["tn"] == 1 and result["tp"] == result["fn"] == 0
    assert result["precision"] == 0
    assert result["recall"] is None and result["average_precision"] is None and result["roc_auc"] is None


def test_changed_source_fails_before_fit_or_output_creation(network_source, tmp_path, monkeypatch):
    source = network_source / benchmark.TRANSACTIONS
    source.write_bytes(source.read_bytes() + b"tamper")
    monkeypatch.setattr(benchmark, "fit_model", lambda *a, **k: pytest.fail("Must verify source before fitting"))
    out = tmp_path / "result"
    with pytest.raises(ValueError, match="Frozen benchmark"):
        _run(network_source, out)
    assert not out.exists()


@pytest.mark.parametrize("changed", ["source", "protocol", "provenance"])
def test_change_during_fit_is_not_blessed_by_later_freeze(network_source, tmp_path, monkeypatch, changed):
    out = tmp_path / "result"
    original_fit = benchmark.fit_model

    def tampering_fit(*args, **kwargs):
        result = original_fit(*args, **kwargs)
        path = {"source": network_source / benchmark.TRANSACTIONS,
                "protocol": out / "protocol.json", "provenance": network_source / "provenance.json"}[changed]
        path.write_bytes(path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(benchmark, "fit_model", tampering_fit)
    monkeypatch.setattr(benchmark, "evaluate", lambda *a, **k: pytest.fail("Must reject mutation before reading evaluation targets"))
    with pytest.raises(ValueError, match="Frozen benchmark"):
        _run(network_source, out)
    assert not (out / "frozen.json").exists()
    assert not (out / "report.json").exists()


@pytest.mark.parametrize("changed", ["model.txt", "frozen.json"])
def test_estimator_or_manifest_change_during_evaluation_rejects_report(network_source, tmp_path, monkeypatch, changed):
    out = tmp_path / "result"

    def tampering_evaluate(*args, **kwargs):
        assert (out / "frozen.json").exists()
        model = out / changed
        model.write_text(model.read_text() + "\n")
        return {}

    monkeypatch.setattr(benchmark, "evaluate", tampering_evaluate)
    with pytest.raises(ValueError, match="Frozen benchmark"):
        _run(network_source, out)
    assert not (out / "report.json").exists()
    assert not (out / "holdout_scores.parquet").exists()


def test_existing_evidence_is_never_overwritten(network_source, tmp_path, monkeypatch):
    out = tmp_path / "result"
    out.mkdir()
    (out / "marker.txt").write_text("preserve me")
    monkeypatch.setattr(benchmark, "load_transactions", lambda *a: pytest.fail("No reads needed when output exists"))
    with pytest.raises(FileExistsError, match="immutable"):
        _run(network_source, out)
    assert {p.name: p.read_text() for p in out.iterdir()} == {"marker.txt": "preserve me"}


def test_changing_only_labels_cannot_change_fitted_model_or_thresholds(network_source, tmp_path):
    first = _run(network_source, tmp_path / "first")
    transactions = pd.read_csv(network_source / benchmark.TRANSACTIONS, dtype={"tran_id": "string"})
    transactions["is_sar"] = ~benchmark._bool(transactions.is_sar)
    transactions.to_csv(network_source / benchmark.TRANSACTIONS, index=False, compression="gzip")
    provenance_path = network_source / "provenance.json"
    provenance = json.loads(provenance_path.read_text())
    provenance["files"][benchmark.TRANSACTIONS] = benchmark.sha256(network_source / benchmark.TRANSACTIONS)
    provenance_path.write_text(json.dumps(provenance))
    second = _run(network_source, tmp_path / "second")
    assert (tmp_path / "first" / "model.txt").read_bytes() == (tmp_path / "second" / "model.txt").read_bytes()
    assert first["model"]["thresholds"] == second["model"]["thresholds"]
    assert first["evaluation"]["holdout_SAR_transactions"] != second["evaluation"]["holdout_SAR_transactions"]
    assert first["evaluation"]["thresholds"]["auto"]["account_window"] != second["evaluation"]["thresholds"]["auto"]["account_window"]
