"""Research views preserve observation availability and never consume labels."""

import copy

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from app.ml.behavioral.features import build_features, fit_vocabulary
from app.ml.behavioral.research_features import (
    REPRESENTATION_VERSION, build_experiment_features, feature_definitions, feature_names,
)
from app.ml.behavioral.standardize import normalize_frame


def observations(count=65, interval=1, precision="second"):
    rows = [{"transaction_ref": f"T{i:04}", "account_number": "A",
             "transaction_date": (pd.Timestamp("2023-01-01T12:00:00Z") + pd.Timedelta(days=i * interval)).isoformat(),
             "amount": f"{10 + i % 7:.2f}", "currency": "USD", "direction": "DEBIT", "channel": "ONLINE",
             "device_ref": "D", "merchant_ref": "M", "location": "City", "ip_address": "192.0.2.1",
             "login_attempts": 1, "transaction_duration": 20, "account_balance": 1000 + i,
             "timestamp_precision": precision, "source_timezone_known": precision != "day"}
            for i in range(count)]
    return normalize_frame(pd.DataFrame(rows), "canonical").frame


@pytest.mark.parametrize("unit", ["AML", "FRAUD"])
def test_full_view_is_exactly_the_frozen_original(unit):
    frame = observations()
    vocabulary = fit_vocabulary(frame)
    before, original_vocab = frame.copy(deep=True), copy.deepcopy(vocabulary)
    expected = build_features(frame, unit, vocabulary)
    actual = build_experiment_features(frame, unit, vocabulary, "full")
    assert_frame_equal(actual, expected)
    assert actual.to_csv(index=False) == expected.to_csv(index=False)
    assert_frame_equal(frame, before)
    assert vocabulary == original_vocab
    assert feature_names(unit, "full") == list(expected.columns[3:])


@pytest.mark.parametrize("unit", ["AML", "FRAUD"])
def test_portable_registry_has_no_provenance_lifecycle_or_intraday_risk_inputs(unit):
    frame = observations()
    result = build_experiment_features(frame, unit, fit_vocabulary(frame), "portable")
    names = feature_names(unit, "portable")
    assert list(result.columns) == ["account_number", "transaction_ref", "as_of", *names]
    assert len(names) == len(set(names))
    assert list(feature_definitions(unit, "portable")) == names
    assert REPRESENTATION_VERSION
    for name in names:
        assert not any(token in name for token in ("HISTORY_DAYS", "PRIOR_HISTORY_COUNT", "NO_BASELINE", "UTC_HOUR", "DAY_ONLY", "R1H", "R24H"))
    assert result[names].dtypes.eq(np.dtype("float64")).all()
    assert not np.isinf(result[names].to_numpy()).any()


@pytest.mark.parametrize("unit", ["AML", "FRAUD"])
def test_portable_future_invariance_with_sparse_date_only_observations(unit):
    frame = observations(65, interval=12, precision="day")
    vocabulary = fit_vocabulary(frame.iloc[:35])
    cut = pd.Timestamp("2024-04-14T23:59:59Z")
    full = build_experiment_features(frame, unit, vocabulary, "portable")
    past = build_experiment_features(frame[frame.transaction_date <= cut], unit, vocabulary, "portable")
    assert_frame_equal(full[full.as_of <= cut].reset_index(drop=True), past[past.as_of <= cut].reset_index(drop=True))


def test_sparse_source_supports_last30_reference_when_ninety_day_reference_is_all_missing():
    frame = observations(45, interval=13, precision="day")
    vocabulary = fit_vocabulary(frame.iloc[:35])
    old = build_features(frame, "FRAUD", vocabulary)
    new = build_experiment_features(frame, "FRAUD", vocabulary, "portable")
    assert old.AMOUNT_OVER_PRIOR_MEAN.isna().all()
    assert old.AMOUNT_Z_PRIOR.isna().all()
    assert old.BALANCE_OVER_PRIOR_MEAN.isna().all()
    assert new.iloc[:30].AMOUNT_OVER_LAST30_PRIOR_MEAN.isna().all()
    expected_mean = float(frame.iloc[0:30].amount.astype(float).mean())
    assert new.iloc[30].AMOUNT_OVER_LAST30_PRIOR_MEAN == pytest.approx(float(frame.iloc[30].amount) / expected_mean)
    assert new.iloc[30:].AMOUNT_OVER_LAST30_PRIOR_MEAN.notna().all()
    assert new.iloc[30:].AMOUNT_Z_LAST30_PRIOR.notna().all()
    assert new.iloc[30:].BALANCE_OVER_LAST30_PRIOR_MEAN.notna().all()
    # Gap measures elapsed time since prior day-end availability, not invented
    # intraday inactivity: 12days elapsed for observations13 calendar days apart.
    assert np.allclose(new.iloc[30:].GAP_OVER_LAST30_PRIOR_MEAN_GAP, 12 / 13)


def test_same_day_date_only_observation_is_not_available_to_an_exact_time_query():
    frame = observations(45)
    vocabulary = fit_vocabulary(frame.iloc[:35])
    unknown = frame.iloc[-1:].copy()
    unknown["transaction_ref"] = "unavailable-day-only"
    unknown["transaction_date"] = unknown.transaction_date.dt.floor("D")
    unknown["timestamp_precision"] = "day"
    unknown["source_timezone_known"] = False
    unknown["amount"] = 999999.0
    before = build_experiment_features(frame, "FRAUD", vocabulary, "portable")
    after = build_experiment_features(pd.concat([frame, unknown], ignore_index=True), "FRAUD", vocabulary, "portable")
    assert_frame_equal(before, after[after.transaction_ref.ne("unavailable-day-only")].reset_index(drop=True))
    # Once the source day ends, that observation really does enter the reference.
    tomorrow = frame.iloc[-1:].copy()
    tomorrow["transaction_ref"] = "tomorrow"
    tomorrow["transaction_date"] += pd.Timedelta(days=1)
    all_rows = pd.concat([frame, unknown, tomorrow], ignore_index=True)
    scored = build_experiment_features(all_rows, "FRAUD", vocabulary, "portable")
    expected_prior = pd.concat([frame.iloc[-29:], unknown], ignore_index=True).amount.astype(float).mean()
    actual = scored[scored.transaction_ref.eq("tomorrow")].iloc[0]
    assert actual.AMOUNT_OVER_LAST30_PRIOR_MEAN == pytest.approx(float(tomorrow.amount.iloc[0]) / expected_prior)


def test_equal_exact_timestamps_are_strictly_excluded_from_each_others_reference():
    frame = observations(45)
    vocabulary = fit_vocabulary(frame.iloc[:35])
    extra = frame.iloc[-1:].copy()
    extra["transaction_ref"] = "same-instant"
    extra["amount"] = 999999.0
    before = build_experiment_features(frame, "FRAUD", vocabulary, "portable")
    after = build_experiment_features(pd.concat([frame, extra], ignore_index=True), "FRAUD", vocabulary, "portable")
    original = after[after.transaction_ref.ne("same-instant")].reset_index(drop=True)
    assert_frame_equal(before, original)


@pytest.mark.parametrize("unit", ["AML", "FRAUD"])
def test_reference_expands_unequal_value_boundary_ties_instead_of_selecting_by_identifier(unit):
    daily = observations(30, precision="day")
    ties = pd.concat([daily.iloc[:1]] * 3, ignore_index=True)
    ties["transaction_ref"] = ["tie-a", "tie-b", "tie-c"]
    ties["amount"] = [10.0, 100.0, 1000.0]
    ties["account_balance"] = [100.0, 200.0, 3000.0]
    target = daily.iloc[-1:].copy()
    target["transaction_ref"] = "target"
    target["transaction_date"] += pd.Timedelta(days=11)
    target["amount"] = 25.0
    frame = pd.concat([ties, daily.iloc[1:], target], ignore_index=True)
    vocabulary = fit_vocabulary(daily)
    renamed = frame.copy()
    renamed.loc[:2, "transaction_ref"] = ["tie-c", "tie-b", "tie-a"]
    baseline = build_experiment_features(frame, unit, vocabulary, "portable")
    changed = build_experiment_features(renamed, unit, vocabulary, "portable")
    names = feature_names(unit, "portable")
    # For FRAUD compare target, and for AML its final observed week. These
    # require32 prior observations because the minimum30 boundary hits a3-way
    # date-only tie. All tied amounts/balances must contribute symmetrically.
    assert_frame_equal(baseline.iloc[-1:][names], changed.iloc[-1:][names])
    prior_mean = frame.iloc[:-1].amount.astype(float).mean()
    column = "AMOUNT_OVER_LAST30_PRIOR_MEAN" if unit == "FRAUD" else "TICKET_W_OVER_LAST30_PRIOR_MEAN"
    assert baseline.iloc[-1][column] == pytest.approx(25.0 / prior_mean)


def test_latest_mixed_precision_availability_tie_gap_is_commutative_under_identifier_renaming():
    frame = observations(40, interval=2, precision="day")
    exact = frame.iloc[-1:].copy()
    exact["transaction_ref"] = "z-latest-exact"
    # Same availability bound as that day's date-only row, but a different
    # observed time. The original raw-time last-row gap depends on ref sorting.
    exact["transaction_date"] += pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    exact["timestamp_precision"] = "second"
    exact["source_timezone_known"] = True
    target = frame.iloc[-1:].copy()
    target["transaction_ref"] = "target"
    target["transaction_date"] += pd.Timedelta(days=2)
    combined = pd.concat([frame, exact, target], ignore_index=True)
    renamed = combined.copy()
    renamed.loc[39, "transaction_ref"] = "z-latest-date"
    renamed.loc[40, "transaction_ref"] = "a-latest-exact"
    vocabulary = fit_vocabulary(frame)
    before = build_experiment_features(combined, "FRAUD", vocabulary, "portable")
    after = build_experiment_features(renamed, "FRAUD", vocabulary, "portable")
    names = feature_names("FRAUD", "portable")
    assert_frame_equal(before.iloc[-1:][names], after.iloc[-1:][names])
    old_before = build_features(combined, "FRAUD", vocabulary).iloc[-1].DAYS_SINCE_PREV_OBSERVATION
    old_after = build_features(renamed, "FRAUD", vocabulary).iloc[-1].DAYS_SINCE_PREV_OBSERVATION
    assert old_before != old_after  # The portable view must bypass this field.
    assert np.isfinite(before.iloc[-1].GAP_OVER_LAST30_PRIOR_MEAN_GAP)


def test_thirty_day_gate_is_retained_even_if_thirty_events_fit_inside_one_day():
    frame = observations(40)
    frame["transaction_date"] = pd.Timestamp("2023-01-01T00:00:00Z") + pd.to_timedelta(np.arange(40), unit="m")
    result = build_experiment_features(frame, "FRAUD", fit_vocabulary(frame), "portable")
    assert result.AMOUNT_OVER_LAST30_PRIOR_MEAN.isna().all()
    assert result.NEW_CONTEXT_EXCESS_LOGIN.isna().all()


@pytest.mark.parametrize("unit", ["AML", "FRAUD"])
def test_labels_identifiers_and_demographics_never_enter_numeric_features(unit):
    frame = observations(70)
    vocabulary = fit_vocabulary(frame.iloc[:40])
    baseline = build_experiment_features(frame, unit, vocabulary, "portable")
    changed = frame.copy()
    changed["account_number"] = "another-account"
    changed["transaction_ref"] = [f"renamed{i:05}" for i in range(len(changed))]
    changed["device_ref"] = "renamed-device"
    changed["merchant_ref"] = "renamed-merchant"
    changed["ip_address"] = "198.51.100.1"
    changed["location"] = "renamed-location"
    changed["label"] = np.arange(len(changed)) % 2
    changed["scenario"] = "answer"
    changed["source_parent"] = "leak"
    changed["customer_age"] = 99
    changed["customer_occupation"] = "OTHER"
    result = build_experiment_features(changed, unit, vocabulary, "portable")
    assert_frame_equal(baseline[feature_names(unit, "portable")], result[feature_names(unit, "portable")])


def test_aml_uses_last30_actual_day_span_before_week_and_joint_login_novelty():
    frame = observations(50, interval=10, precision="day")
    vocabulary = fit_vocabulary(frame.iloc[:35])
    result = build_experiment_features(frame, "AML", vocabulary, "portable")
    supported = result.COUNT_W_OVER_LAST30_PRIOR_RATE.notna()
    assert supported.any()
    # Exactly one event per ten days gives rate29/290=.1 events/day.
    assert np.allclose(result.loc[supported, "COUNT_W_OVER_LAST30_PRIOR_RATE"], 1 / .7)
    assert result.loc[supported, "TICKET_W_OVER_LAST30_PRIOR_MEAN"].notna().all()
    assert result.NEW_CONTEXT_EXCESS_LOGIN_W.dropna().eq(0).all()


def test_context_and_excess_login_are_joined_per_observation_before_week_aggregation():
    frame = observations(49, interval=10)
    vocabulary = fit_vocabulary(frame.iloc[:35])
    frame.loc[48, "device_ref"] = "new-device"
    frame.loc[48, "login_attempts"] = 5
    other = frame.iloc[-1:].copy()
    other["transaction_ref"] = "old-device-high-login"
    other["transaction_date"] += pd.Timedelta(days=1)
    other["device_ref"] = "D"
    other["login_attempts"] = 6
    frame = pd.concat([frame, other], ignore_index=True)
    fraud = build_experiment_features(frame, "FRAUD", vocabulary, "portable")
    assert fraud[fraud.transaction_ref.eq("T0048")].NEW_CONTEXT_EXCESS_LOGIN.iloc[0] == 4
    assert fraud[fraud.transaction_ref.eq("old-device-high-login")].NEW_CONTEXT_EXCESS_LOGIN.iloc[0] == 0
    aml = build_experiment_features(frame, "AML", vocabulary, "portable")
    assert aml.iloc[-1].NEW_CONTEXT_EXCESS_LOGIN_W == 2


@pytest.mark.parametrize("unit", ["AML", "FRAUD"])
def test_unknown_context_and_balance_are_not_made_zero(unit):
    frame = observations()
    frame[["device_ref", "ip_address", "location", "account_balance"]] = None
    result = build_experiment_features(frame, unit, fit_vocabulary(frame), "portable")
    if unit == "FRAUD":
        assert result.BALANCE_OVER_LAST30_PRIOR_MEAN.isna().all()
        assert result.NEW_CONTEXT_EXCESS_LOGIN.isna().all()
    else:
        assert result.BALANCE_W_OVER_LAST30_PRIOR_MEAN.isna().all()
        assert result.NEW_CONTEXT_EXCESS_LOGIN_W.isna().all()


def test_representation_validation_and_empty_portable_contract():
    frame = observations()
    vocabulary = fit_vocabulary(frame)
    with pytest.raises(ValueError, match="representation"):
        build_experiment_features(frame, "FRAUD", vocabulary, "unknown")
    result = build_experiment_features(frame.iloc[:0], "FRAUD", vocabulary, "portable")
    assert result.empty
    assert list(result.columns) == ["account_number", "transaction_ref", "as_of", *feature_names("FRAUD", "portable")]
