"""Separate lab study: chronological coverage, schema and honest controls."""

import json

import pandas as pd
import pytest

from app.ml.behavioral.features import build_features, feature_names, fit_vocabulary
from app.ml.lab.mapping import apply_mapping, read_csv_bytes
from app.scripts import generate_lab_dataset as generator
from app.scripts.make_lab_sample import OUTPUT_COLUMNS


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    out = tmp_path_factory.mktemp("lab_coverage") / "new"
    report = generator.generate_dataset(out, rows=20_000, seed=2026110411)
    raw = read_csv_bytes((out / "transactions.csv").read_bytes())
    lineage = pd.read_csv(out / "lineage.csv", keep_default_na=False)
    return out, report, raw, lineage


def test_same_upload_layout_and_no_generated_row_loss(study):
    out, report, raw, lineage = study
    assert list(raw.columns) == [*OUTPUT_COLUMNS, "is_fraud", "pattern_code"]
    assert len(raw) == len(lineage) == report["rows"] == 20_000
    assert raw.source_transaction_reference.is_unique
    assert set(raw.status_from_source) == {"POSTED"}
    assert set(raw.currency_original) == {"IDR"}
    assert report["upload_bytes"] <= generator.MAX_UPLOAD_BYTES
    checked = report["coverage"]["normalization"]
    assert checked["accepted"] == checked["usable_rows"] == 20_000
    assert checked["quarantined"] == checked["missing_label"] == 0
    assert report["legacy_generator_changed"] is False
    assert report["coverage"]["coverage_assertions_passed"]
    for name, entry in report["files"].items():
        assert generator.sha256(out / name) == entry["sha256"]


def test_every_partition_has_all_patterns_low_tickets_and_lookalikes(study):
    _, report, _, _ = study
    splits = report["coverage"]["splits"]
    assert sum(part["rows"] for part in splits.values()) == 20_000
    for part in splits.values():
        for pattern in generator.PATTERNS:
            item = part["patterns"][pattern]
            assert item["positive_rows"] > 0
            assert item["benign_lookalike_rows"] > 0
            assert item["small_positive_rows"] > 0
            assert item["small_benign_rows"] > 0
    diagnostic = report["coverage"]["scenario_partition_diagnostic"]
    assert diagnostic["independent_scenario_holdout_claim"] is False
    assert diagnostic["scenarios_with_history_or_event_in_multiple_partitions"] >= diagnostic["scenarios_with_event_rows_in_multiple_partitions"]


def test_all_source_dates_stay_within_manifest_calendar(study):
    _, report, raw, _ = study
    dates = pd.to_datetime(raw.value_datetime, utc=True)
    assert dates.min() >= pd.Timestamp(report["period_start"])
    assert dates.max() < pd.Timestamp(report["period_end_exclusive"])
    assert report["days"] == 720


def test_dormant_low_ticket_positives_have_real_prior_gap_and_count_branch(study):
    out, _, raw, lineage = study
    joined = raw.merge(lineage, left_on="source_transaction_reference", right_on="transaction_ref", validate="one_to_one")
    cases = json.loads((out / "scenarios.json").read_text())
    low = [c for c in cases if c["pattern"] == "P05" and c["magnitude"] == "low"
           and c["case_label"] == "simulated_intervention"]
    assert low
    for case in low:
        own = joined[joined.scenario_id.eq(case["scenario_id"])]
        history = pd.to_datetime(own.loc[own.role.eq("history"), "value_datetime"], utc=True)
        event = own.loc[own.role.eq("event")]
        moments = pd.to_datetime(event.value_datetime, utc=True)
        assert 32 <= len(history) <= 64
        assert len(event) >= 16 >= 5
        assert moments.max() - moments.min() < pd.Timedelta(days=7)
        assert moments.min() - history.max() >= pd.Timedelta(days=100)
        actual_gap = (moments.min() - history.max()).total_seconds() / 86400
        # Stored metadata uses the latest actual observation, not the last
        # pre-jitter list item; both internal and CSV clocks retain seconds.
        assert case["prior_observed_gap_days"] == pytest.approx(actual_gap, abs=1e-12, rel=0)
        assert event.amount_original.astype(float).sum() < 25_000_000
        assert (event.is_fraud == "1").all()
    assert len({c["history_rows"] for c in cases}) > 10


def test_exact_controls_do_not_offer_a_perfect_observable_intent_shortcut(study):
    _, report, raw, lineage = study
    joined = raw.merge(lineage, left_on="source_transaction_reference", right_on="transaction_ref", validate="one_to_one")
    exact = joined[joined.exact_counterfactual.eq(1)]
    assert len(exact) > 0
    assert 0 < report["design"]["actual_exact_pair_share"] < 1
    pair = next(iter(exact.groupby("pair_id")))[1]
    a = pair[pair.case_label.eq("simulated_intervention")].sort_values("value_datetime")
    b = pair[pair.case_label.eq("benign_lookalike")].sort_values("value_datetime")
    observed = [c for c in OUTPUT_COLUMNS if c not in
                {"source_transaction_reference", "source_account_id", "source_device_id"}]
    pd.testing.assert_frame_equal(a[observed].reset_index(drop=True), b[observed].reset_index(drop=True))
    assert a.is_fraud.eq("1").any() and not b.is_fraud.eq("1").any()

    # Neutral identifiers differ, but the actual current FRAUD feature views match.
    frame = pd.concat([a[list(generator.UPLOAD_COLUMNS)], b[list(generator.UPLOAD_COLUMNS)]], ignore_index=True)
    mapping = generator._mapping(frame)
    canonical = apply_mapping(frame, mapping).frame
    features = build_features(canonical, "FRAUD", fit_vocabulary(canonical))
    account_a = a.source_account_id.iloc[0]
    account_b = b.source_account_id.iloc[0]
    views = []
    for account in (account_a, account_b):
        refs = set(frame.loc[frame.source_account_id.eq(account), "source_transaction_reference"])
        views.append(features[features.transaction_ref.isin(refs)].sort_values("as_of")
                     [feature_names("FRAUD")].reset_index(drop=True))
    pd.testing.assert_frame_equal(*views)


def test_identifier_status_and_schema_do_not_contain_target_words(study):
    _, _, raw, _ = study
    for name in ("source_transaction_reference", "source_account_id", "source_device_id", "source_merchant_id"):
        assert not raw[name].str.contains("ATO|P04|P05|fraud|normal|positive|negative", case=False, regex=True).any()
    assert set(raw.pattern_code[raw.is_fraud.eq("0")]) == {""}


def test_channel_support_matches_the_prior_wallet_contract_for_all_labels(study):
    _, report, raw, lineage = study
    known = {"TRANSFER", "QRIS", "PAYMENT", "AGENT"}
    assert set(generator.CHANNELS) == known
    assert set(report["design"]["label_independent_channel_support"]) == known
    assert set(raw.channel) == known
    joined = raw.merge(lineage, left_on="source_transaction_reference", right_on="transaction_ref")
    for label in ("simulated_intervention", "benign_lookalike", "background"):
        assert set(joined.loc[joined.case_label.eq(label), "channel"]) == known


def test_missing_temporal_pattern_coverage_fails_instead_of_repartitioning(study):
    _, _, raw, lineage = study
    changed = raw.copy()
    changed.loc[changed.pattern_code.eq("ATO"), ["is_fraud", "pattern_code"]] = ["0", ""]
    with pytest.raises(generator.CoverageError, match="ATO"):
        generator.validate_coverage(changed, lineage)


def test_same_seed_reproduces_every_file_byte_for_byte(study, tmp_path):
    out, first, _, _ = study
    other = tmp_path / "second"
    second = generator.generate_dataset(other, rows=20_000, seed=2026110411)
    assert first == second
    for path in out.iterdir():
        assert path.read_bytes() == (other / path.name).read_bytes()


def test_prior_output_is_never_overwritten(study):
    out, _, _, _ = study
    with pytest.raises(FileExistsError):
        generator.generate_dataset(out, rows=20_000, seed=2026110411)


@pytest.mark.parametrize("rows", [1, 19_999, 500_001])
def test_unsupported_volume_is_refused_without_writes(rows, tmp_path):
    out = tmp_path / "bad"
    with pytest.raises(ValueError, match="rows must"):
        generator.generate_dataset(out, rows=rows)
    assert not out.exists()


def test_existing_upload_byte_cap_is_enforced(tmp_path, monkeypatch):
    out = tmp_path / "too_large"
    monkeypatch.setattr(generator, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(generator.CoverageError, match="50 MiB"):
        generator.generate_dataset(out, rows=20_000, seed=2026110411)
    assert not out.exists()


def test_a_different_fixed_calendar_requires_an_explicit_new_version(tmp_path):
    with pytest.raises(ValueError, match="720-day"):
        generator.generate_dataset(tmp_path / "bad", rows=20_000, days=365)
