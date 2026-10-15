"""Generator leakage/lineage contracts; simulated recipe labels stay sidecars."""

import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml.behavioral.generate import SOURCE_COLUMNS, generate, source_partitions
from app.ml.behavioral.standardize import CANONICAL_COLUMNS, normalize_csv


@pytest.fixture
def source(tmp_path):
    records = []
    for account in range(20):
        for txn in range(12):
            records.append({
                "TransactionID": f"TX{account:02d}{txn:02d}", "AccountID": f"AC{account:03d}",
                "TransactionAmount": f"{1 + account * 5 + txn * 2:.2f}",
                "TransactionDate": f"1/{txn + 1}/2023" if txn % 2 else f"1/{txn + 1}/2023 12:30",
                "TransactionType": "Debit" if txn % 3 else "Credit", "Location": f"City{account % 4}",
                "DeviceID": f"D{account}", "IP Address": "192.0.2.1", "MerchantID": f"M{txn % 5}",
                "Channel": ["ATM", "Online", "Branch"][txn % 3], "CustomerAge": str(25 + account),
                "CustomerOccupation": "Engineer", "TransactionDuration": str(20 + txn * 5),
                "LoginAttempts": str(1 + (txn == 11)), "AccountBalance": f"{100 + account * 20 + txn:.2f}",
            })
    path = tmp_path / "source.csv"
    pd.DataFrame(records, columns=SOURCE_COLUMNS).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def read(out, name="transactions.csv"):
    return pd.read_csv(out / name, keep_default_na=False)


def test_split_before_augmentation_is_deterministic_disjoint_and_complete(source):
    partitions = source_partitions(source)
    assert partitions == source_partitions(source)
    assert list(map(len, partitions.values())) == [14, 3, 3]
    all_ids = [account for ids in partitions.values() for account in ids]
    assert len(all_ids) == len(set(all_ids)) == 20


def test_exact_size_byte_reproducibility_source_unchanged_and_no_overwrite(source, tmp_path):
    initial = source.read_bytes()
    a, b = tmp_path / "a", tmp_path / "b"
    one = generate(source, a, "train", 501, 123)
    two = generate(source, b, "train", 501, 123)
    assert one == two
    for path in a.iterdir():
        assert path.read_bytes() == (b / path.name).read_bytes()
    assert source.read_bytes() == initial
    assert len(read(a)) == 501
    assert read(a).transaction_ref.is_unique
    with pytest.raises(FileExistsError, match="Refusing"):
        generate(source, a, "train", 501, 123)


def test_global_fit_is_source_training_only_and_heldout_parents_are_real(source, tmp_path):
    train, validation, test = [tmp_path / name for name in ("train", "validation", "test")]
    generate(source, train, "train", 751, 10)
    fitted = json.loads((train / "fit_profile.json").read_text())
    partitions = source_partitions(source)
    raw = pd.read_csv(source)
    train_rows = raw[raw.AccountID.isin(partitions["train"])]
    assert fitted["source_training_rows"] == len(train_rows)
    assert fitted["amount_quantiles"]["0.1"] == pytest.approx(train_rows.TransactionAmount.quantile(0.1))
    assert fitted["amount_quantiles"]["0.4"] == pytest.approx(train_rows.TransactionAmount.quantile(0.4))
    manifests = {"train": json.loads((train / "manifest.json").read_text())}
    for split, out in (("validation", validation), ("test", test)):
        manifests[split] = generate(source, out, split, 751, 20, fit_profile=train / "fit_profile.json")
        assert (out / "fit_profile.json").read_bytes() == (train / "fit_profile.json").read_bytes()
    for split, out in (("train", train), ("validation", validation), ("test", test)):
        lineage = read(out, "lineage.csv")
        assert set(lineage.source_account_id) <= set(partitions[split])
        assert set(lineage.source_account_id) == set(manifests[split]["source_parent_account_ids"])
        for row in lineage.itertuples(index=False):
            original = raw.iloc[row.source_ordinal - 1]
            assert original.AccountID == row.source_account_id
            assert original.TransactionID == row.source_transaction_id
        expected_hash = hashlib.sha256((train / "fit_profile.json").read_bytes()).hexdigest()
        assert manifests[split]["fit_profile_sha256"] == expected_hash
    windows = [manifests[s]["window"] for s in ("train", "validation", "test")]
    assert windows[0]["end_exclusive"] == windows[1]["start_inclusive"]
    assert windows[1]["end_exclusive"] == windows[2]["start_inclusive"]
    assert windows[2]["end_exclusive"] == "2026-10-16T00:00:00+00:00"


def test_heldout_requires_matching_frozen_fit(source, tmp_path):
    with pytest.raises(ValueError, match="requires"):
        generate(source, tmp_path / "none", "validation", 10, 1)
    train = tmp_path / "train"
    generate(source, train, "train", 10, 1)
    profile = json.loads((train / "fit_profile.json").read_text())
    profile["source_sha256"] = "different source"
    bad = tmp_path / "wrong.json"
    bad.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="does not match"):
        generate(source, tmp_path / "bad", "test", 10, 1, fit_profile=bad)
    assert not (tmp_path / "bad").exists()


def test_canonical_companion_normalize_identically_without_predictive_provenance(source, tmp_path):
    out = tmp_path / "generated"
    manifest = generate(source, out, "train", 1000, 991)
    raw = read(out)
    assert tuple(raw.columns) == CANONICAL_COLUMNS
    assert tuple(read(out, "source_shape.csv").columns) == SOURCE_COLUMNS
    canonical = normalize_csv(out / "transactions.csv", "canonical")
    companion = normalize_csv(out / "source_shape.csv")
    assert canonical.report["quarantined"] == companion.report["quarantined"] == 0
    for column in CANONICAL_COLUMNS:
        pd.testing.assert_series_equal(canonical.frame[column], companion.frame[column], check_names=False)
    labels = read(out, "ground_truth.csv")
    assert set(labels.label) <= {0, 1}
    assert set(labels.transaction_ref) == set(raw.transaction_ref)
    assert set(labels.account_number) == set(raw.account_number)
    assert all("label" not in c and "scenario" not in c and "source_ordinal" not in c for c in raw)
    assert raw.counterparty_ref.eq("").all() and raw.counterparty_country.eq("").all()
    assert raw.currency.eq("USD").all() and raw.source_timezone_known.all()
    assert raw.timestamp_precision.eq("second").all()
    assert raw.ip_address.str.match(r"^(192\.0\.2|198\.51\.100|203\.0\.113)\.").all()
    assert raw.groupby("account_number").customer_age.nunique().eq(1).all()
    assert raw.groupby("account_number").customer_occupation.nunique().eq(1).all()
    assert "simulation" in manifest["timestamp_policy"]
    dates = pd.to_datetime(raw.transaction_date, utc=True)
    assert dates.ge(pd.Timestamp(manifest["window"]["start_inclusive"])).all()
    assert dates.lt(pd.Timestamp(manifest["window"]["end_exclusive"])).all()


def test_low_amount_stress_and_legitimate_overlap_at_10k(source, tmp_path):
    out = tmp_path / "pilot"
    manifest = generate(source, out, "train", 10_000, 20261015)
    transactions, labels = read(out), read(out, "ground_truth.csv")
    from app.ml.behavioral.generate import STRESS_RECIPES, LOOKALIKE_RECIPES
    assert set(STRESS_RECIPES) <= set(labels.scenario)
    assert set(LOOKALIKE_RECIPES) <= set(labels.scenario)
    assert labels.label.sum() > 0
    assert set(labels.loc[labels.label.eq(1), "label_type"]) == {"synthetic_stress"}
    low_stress = labels.scenario.isin(["card_testing_burst", "micro_splitting", "pass_through_proxy"])
    assert labels.loc[low_stress, "amount_band"].eq("train_p10_p40").all()
    ordinary = labels.scenario.eq("ordinary")
    assert labels.loc[ordinary, "amount_band"].nunique() == 3
    assert labels.loc[labels.scenario.isin(LOOKALIKE_RECIPES), "label"].eq(0).all()
    assert transactions.groupby("account_number").device_ref.nunique().ge(2).all()
    assert manifest["counts"]["rows"] == len(transactions)
    assert sum(manifest["scenario_counts"].values()) == 10_000


@pytest.mark.parametrize("rows", [0, -1, True, 1.5])
def test_bad_size_fails_without_creating_output(source, tmp_path, rows):
    out = tmp_path / "invalid"
    with pytest.raises(ValueError, match="positive integer"):
        generate(source, out, "train", rows, 1)
    assert not out.exists()
