"""Source-cadence simulation: heldout lineage and honest clock observability."""

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from app.ml.behavioral.generate_v2 import (
    GENERATOR_VERSION, LOOKALIKE_RECIPES, SOURCE_COLUMNS, STRESS_RECIPES, _account, generate, source_partitions,
)
from app.ml.behavioral.standardize import CANONICAL_COLUMNS, normalize_csv


@pytest.fixture
def source(tmp_path):
    records = []
    for account in range(20):
        days = np.r_[0, np.cumsum(np.resize([1, 3, 7, 13, 30, 70], 69 + account % 10))]
        for i, day in enumerate(days):
            when = pd.Timestamp("2020-01-01") + pd.Timedelta(days=int(day))
            supplied = when.strftime("%m/%d/%Y %H:%M") if i % 8 == 0 else when.strftime("%m/%d/%Y")
            records.append({"TransactionID": f"T{account:02}{i:03}", "AccountID": f"A{account:02}",
                            "TransactionAmount": 10 + account * 3 + i % 40,
                            "TransactionDate": supplied, "TransactionType": "Debit" if i % 3 else "Credit",
                            "Location": f"City{i % 4}", "DeviceID": f"D{i % 4}", "IP Address": f"192.0.2.{i % 4 + 1}",
                            "MerchantID": f"M{i % 5}", "Channel": ["Online", "ATM", "Branch"][i % 3],
                            "CustomerAge": 25 + account, "CustomerOccupation": "Engineer",
                            "TransactionDuration": 20 + i % 80, "LoginAttempts": 1 + int(i % 17 == 0),
                            "AccountBalance": 1000 + account * 10 + i})
    path = tmp_path / "source.csv"
    pd.DataFrame(records, columns=SOURCE_COLUMNS).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def load(out, filename="transactions.csv"):
    return pd.read_csv(out / filename, keep_default_na=False)


def test_exact_determinism_no_source_mutation_and_no_existing_output(source, tmp_path):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    first, second = tmp_path / "one", tmp_path / "two"
    assert generate(source, first, "train", 2001, 34) == generate(source, second, "train", 2001, 34)
    for path in first.iterdir():
        assert path.read_bytes() == (second / path.name).read_bytes()
    assert len(load(first)) == 2001
    assert load(first).transaction_ref.is_unique
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError, match="overwrite"):
        generate(source, first, "train", 2001, 34)


def test_train_only_fit_heldout_actual_parent_lineage_and_disjoint_proxy_ids(source, tmp_path):
    partitions = source_partitions(source)
    source_frame = pd.read_csv(source)
    train = tmp_path / "train"
    train_manifest = generate(source, train, "train", 3000, 100)
    fitted = json.loads((train / "fit_profile.json").read_text())
    original_train = source_frame[source_frame.AccountID.isin(partitions["train"])]
    assert fitted["generator_version"] == GENERATOR_VERSION
    assert fitted["source_training_rows"] == len(original_train)
    assert fitted["cadence_reference"]["mean_observations_per_account"] == pytest.approx(original_train.groupby("AccountID").size().mean())
    sets = {"train": set(load(train).account_number)}
    for split in ("validation", "test"):
        out = tmp_path / split
        manifest = generate(source, out, split, 1500, 100, fit_profile=train / "fit_profile.json")
        assert (out / "fit_profile.json").read_bytes() == (train / "fit_profile.json").read_bytes()
        assert manifest["fit_profile_sha256"] == train_manifest["fit_profile_sha256"]
        assert "NOT a chronological holdout" in manifest["split_policy"]
        lineage = load(out, "lineage.csv")
        assert set(lineage.source_account_id) <= set(partitions[split])
        parent_rows = source_frame.iloc[lineage.source_ordinal.to_numpy() - 1]
        assert parent_rows.AccountID.tolist() == lineage.source_account_id.tolist()
        assert parent_rows.TransactionID.tolist() == lineage.source_transaction_id.tolist()
        sets[split] = set(load(out).account_number)
    assert not sets["train"] & sets["validation"]
    assert not sets["train"] & sets["test"]
    assert not sets["validation"] & sets["test"]


def test_precision_unknown_clock_and_calendar_support_match_actual_source_parent(source, tmp_path):
    out = tmp_path / "cadence"
    manifest = generate(source, out, "train", 10000, 20261018)
    result = normalize_csv(out / "transactions.csv", "canonical")
    companion = normalize_csv(out / "source_shape.csv")
    assert result.quarantine.empty and companion.quarantine.empty
    for column in CANONICAL_COLUMNS:
        pd.testing.assert_series_equal(result.frame[column], companion.frame[column], check_names=False)
    original = normalize_csv(source).frame
    lineage = load(out, "lineage.csv")
    row_sources = original.iloc[lineage.source_ordinal.to_numpy() - 1]
    assert row_sources.timestamp_precision.tolist() == result.frame.timestamp_precision.tolist()
    assert not result.frame.source_timezone_known.any()
    assert .80 < result.frame.timestamp_precision.eq("day").mean() < .95
    assert result.frame.groupby("account_number").size().median() < 150
    spans = result.frame.groupby("account_number").transaction_date.agg(lambda x:(x.max()-x.min()).days)
    assert spans.median() > 1000  # Multi-year source support was not made180days.
    parent_by_account = lineage.groupby("account_number").source_account_id.first()
    for account, group in result.frame.groupby("account_number"):
        parent = original[original.account_number.eq(parent_by_account[account])]
        assert group.transaction_date.dt.floor("D").min() >= parent.transaction_date.dt.floor("D").min()
        assert group.transaction_date.dt.floor("D").max() <= parent.transaction_date.dt.floor("D").max()
    assert manifest["window"]["chronological_holdout"] is False
    assert manifest["cadence_summary"]["median_generated_gap_days"] > 3


def test_stress_benign_dormancy_followup_and_unobservable_intraday_cases_are_retained(source, tmp_path):
    out = tmp_path / "recipes"
    manifest = generate(source, out, "train", 10000, 20261019)
    rows, truth = load(out), load(out, "ground_truth.csv")
    assert set(STRESS_RECIPES) <= set(truth.scenario)
    assert set(LOOKALIKE_RECIPES) <= set(truth.scenario)
    assert truth[truth.scenario.eq("legitimate_dormant_reactivation")].label.eq(0).all()
    assert truth[truth.scenario.isin(STRESS_RECIPES)].label.eq(1).all()
    assert manifest["counts"]["shared_hiatus_accounts"] > 0
    assert manifest["counts"]["stress_accounts_with_followup"] > 0
    assert manifest["counts"]["lookalike_accounts_with_followup"] > 0
    assert manifest["counts"]["recipe_accounts_without_event_support"] == 0
    joined = rows.merge(truth, on=["transaction_ref", "account_number"], validate="one_to_one")
    joined["transaction_date"] = pd.to_datetime(joined.transaction_date, format="mixed", utc=True)
    for account, group in joined.groupby("account_number"):
        events = group[group.scenario.ne("ordinary")]
        if len(events):
            ordinary = group[group.scenario.eq("ordinary")]
            assert ordinary.transaction_date.min() < events.transaction_date.min()
            assert ordinary.transaction_date.max() > events.transaction_date.max()
    hard = truth[truth.scenario.isin(["card_testing_burst", "micro_splitting", "pass_through_proxy"])]
    assert len(hard) > 0 and hard.intraday_observable.eq(False).any()
    assert hard.label.eq(1).all()  # No dropping/relabeling unobservable hard cases.
    assert set(rows.columns) == set(CANONICAL_COLUMNS)
    assert not set(["label", "scenario", "intraday_observable", "source_ordinal"]) & set(rows.columns)
    assert rows.groupby("account_number").customer_age.nunique().eq(1).all()
    assert rows.groupby("account_number").customer_occupation.nunique().eq(1).all()
    assert rows.ip_address.str.match(r"^(192\.0\.2|198\.51\.100|203\.0\.113)\.").all()


def test_heldout_requires_v2_frozen_profile_and_bad_request_does_not_create_output(source, tmp_path):
    with pytest.raises(ValueError, match="frozen"):
        generate(source, tmp_path / "none", "validation", 1000, 1)
    for value in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            generate(source, tmp_path / "bad", "train", value, 1)
    assert not (tmp_path / "bad").exists()
    train = tmp_path / "train"
    generate(source, train, "train", 1000, 1)
    profile = json.loads((train / "fit_profile.json").read_text())
    profile["generator_version"] = "old-generator"
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="does not match"):
        generate(source, tmp_path / "wrong", "validation", 1000, 1, fit_profile=wrong)
    assert not (tmp_path / "wrong").exists()


def test_stress_and_benign_dormancy_share_cadence_and_followup_instead_of_label_markers(source, tmp_path):
    out = tmp_path / "profile"
    generate(source, out, "train", 1000, 5)
    profile = json.loads((out / "fit_profile.json").read_text())
    original = normalize_csv(source).frame
    parent = original[original.account_number.eq(profile["source_training_account_ids"][0])].copy()
    for column in ("amount", "account_balance", "customer_age", "transaction_duration", "login_attempts"):
        parent[column] = parent[column].astype(float)
    # Counterfactual labels are evaluated separately: swapping only the assumed
    # crime/benign interpretation must not change hiatus, event timing, IDs,
    # amount/context distributions, precision, or post-event ordinary records.
    stress = _account(parent, 100, np.random.default_rng(901), "dormant_reactivation", profile, 1, "train", 0)
    benign = _account(parent, 100, np.random.default_rng(901), "legitimate_dormant_reactivation", profile, 1, "train", 0)
    pd.testing.assert_frame_equal(stress[0], benign[0])
    assert np.array_equal(stress[1], benign[1])
    assert np.array_equal(stress[3], benign[3])
    assert (stress[2][stress[1]] == 1).all()
    assert (benign[2] == 0).all()
    for result, event, *_ in (stress, benign):
        assert result.loc[~event, "transaction_date"].max() > result.loc[event, "transaction_date"].max()
