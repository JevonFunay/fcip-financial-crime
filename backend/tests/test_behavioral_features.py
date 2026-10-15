"""Serving parity, point-in-time history, and observational feature boundaries."""

import json

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from app.ml.behavioral.features import FEATURE_DEFINITIONS, FeatureInputError, build_features, feature_names, fit_vocabulary
from app.ml.behavioral.standardize import normalize_csv, normalize_frame


def observed_frame(days=45, precision="second"):
    rows = []
    for i in range(days):
        rows.append({"transaction_ref": f"TX{i:03}", "account_number": "AC1",
                     "transaction_date": (pd.Timestamp("2023-01-01T12:00:00Z") + pd.Timedelta(days=i)).isoformat(),
                     "amount": float(10 + i % 7), "currency": "USD", "direction": "DEBIT", "channel": "ONLINE",
                     "device_ref": "D1", "merchant_ref": "M1", "location": "Source city", "ip_address": "192.0.2.1",
                     "login_attempts": 1, "transaction_duration": 20, "account_balance": 1000 + i,
                     "timestamp_precision": precision, "source_timezone_known": precision != "day"})
    return normalize_frame(pd.DataFrame(rows), profile="canonical").frame


@pytest.mark.parametrize("unit", ["AML", "FRAUD"])
def test_labels_demographics_and_identifier_renaming_do_not_change_numeric_features(unit):
    frame = observed_frame()
    vocabulary = fit_vocabulary(frame)
    baseline = build_features(frame, unit, vocabulary)
    renamed = frame.copy()
    renamed["account_number"] = "another-account"
    renamed["transaction_ref"] = [f"different-{i:04}" for i in range(len(frame))]
    renamed["device_ref"] = "different-device"
    renamed["merchant_ref"] = "different-merchant"
    renamed["ip_address"] = "198.51.100.2"
    renamed["location"] = "Other label for the same location"
    renamed["label"] = 1
    renamed["scenario_id"] = "answer"
    renamed["customer_age"] = 99
    renamed["customer_occupation"] = "other"
    changed = build_features(renamed, unit, vocabulary)
    assert_frame_equal(baseline[feature_names(unit)], changed[feature_names(unit)])
    assert all((unit, name) in FEATURE_DEFINITIONS for name in feature_names(unit))


@pytest.mark.parametrize("unit", ["AML", "FRAUD"])
def test_adding_future_observations_cannot_change_earlier_features(unit):
    frame = observed_frame(80)
    vocabulary = fit_vocabulary(frame.iloc[:45])
    cut = pd.Timestamp("2023-02-12T23:59:59Z")
    past = frame[frame.transaction_date <= cut]
    all_features = build_features(frame, unit, vocabulary)
    expected = all_features[all_features.as_of <= cut].reset_index(drop=True)
    actual = build_features(past, unit, vocabulary)
    actual = actual[actual.as_of <= cut].reset_index(drop=True)
    assert_frame_equal(expected, actual)


def test_day_only_rows_have_no_fabricated_intraday_order_or_hour():
    frame = observed_frame(45, "day")
    extra = frame.iloc[-1:].copy()
    extra["transaction_ref"] = "TXduplicate-date"
    extra["amount"] = 50.0
    extra["device_ref"] = "new-device"
    two = pd.concat([frame, extra], ignore_index=True)
    vocabulary = fit_vocabulary(frame)
    scored = build_features(two, "FRAUD", vocabulary)
    same_day = scored[scored.as_of == scored.as_of.max()]
    assert same_day.PRIOR_HISTORY_COUNT.tolist() == [44.0, 44.0]
    assert same_day[["COUNT_R1H", "COUNT_R24H", "DEBIT_VALUE_R24H", "UTC_HOUR_SIN", "UTC_HOUR_COS"]].isna().all().all()
    before = build_features(frame, "FRAUD", vocabulary)
    assert_frame_equal(before, scored[scored.transaction_ref != "TXduplicate-date"].reset_index(drop=True))


def test_current_exact_row_cannot_treat_same_day_day_only_row_as_prior():
    frame = observed_frame(45)
    day_row = frame.iloc[-1:].copy()
    day_row["transaction_ref"] = "day-unknown-order"
    day_row["transaction_date"] = day_row.transaction_date.dt.floor("D")
    day_row["timestamp_precision"] = "day"
    day_row["source_timezone_known"] = False
    combined = pd.concat([frame, day_row], ignore_index=True)
    result = build_features(combined, "FRAUD", fit_vocabulary(frame))
    current = result[result.transaction_ref == "TX044"].iloc[0]
    assert current.PRIOR_HISTORY_COUNT == 44
    assert current.COUNT_R24H == 2  # yesterday's exact row and this row


def test_unavailable_day_only_row_cannot_change_the_first_exact_observation_age():
    first = observed_frame(1)
    vocabulary = fit_vocabulary(first)
    day = first.copy()
    day["transaction_ref"] = "same-day-order-unknown"
    day["transaction_date"] = day.transaction_date.dt.floor("D")
    day["timestamp_precision"] = "day"
    day["source_timezone_known"] = False
    baseline = build_features(first, "FRAUD", vocabulary)
    combined = build_features(pd.concat([first, day], ignore_index=True), "FRAUD", vocabulary)
    assert_frame_equal(baseline, combined[combined.transaction_ref == "TX000"].reset_index(drop=True))


def test_non_boolean_timezone_metadata_cannot_create_utc_hour_features():
    frame = observed_frame(1)
    vocabulary = fit_vocabulary(frame)
    frame["source_timezone_known"] = "false"
    with pytest.raises(FeatureInputError, match="must be booleans"):
        build_features(frame, "FRAUD", vocabulary)


def test_history_gate_unknown_context_and_dormancy_are_explicit():
    frame = observed_frame()
    vocabulary = fit_vocabulary(frame)
    frame.loc[44, "transaction_date"] += pd.Timedelta(days=120)
    frame["device_ref"] = None
    frame["ip_address"] = None
    result = build_features(frame, "FRAUD", vocabulary)
    short = result[result.PRIOR_HISTORY_COUNT < 30]
    assert short.NO_BASELINE.eq(1).all()
    assert short[["AMOUNT_OVER_PRIOR_MEAN", "AMOUNT_Z_PRIOR", "DEVICE_FIRST_USE"]].isna().all().all()
    assert result.DEVICE_FIRST_USE.isna().all() and result.IP_FIRST_USE.isna().all()
    dormant = result[result.transaction_ref == "TX044"].iloc[0]
    assert dormant.DAYS_SINCE_PREV_OBSERVATION == 121
    assert np.isnan(dormant.AMOUNT_OVER_PRIOR_MEAN)


@pytest.mark.parametrize("unit", ["AML", "FRAUD"])
def test_canonical_csv_and_live_frame_have_identical_features(tmp_path, unit):
    frame = observed_frame()
    vocabulary = fit_vocabulary(frame)
    emitted = frame.copy()
    emitted["source_original"] = emitted.source_original.map(json.dumps)
    path = tmp_path / "upload.csv"
    emitted.to_csv(path, index=False)
    uploaded = normalize_csv(path, profile="canonical")
    assert uploaded.quarantine.empty
    assert_frame_equal(build_features(frame, unit, vocabulary), build_features(uploaded.frame, unit, vocabulary))


def test_currency_contract_and_unseen_channel_are_not_refitted():
    training = observed_frame()
    vocabulary = fit_vocabulary(training)
    assert json.loads(json.dumps(vocabulary)) == vocabulary
    unseen = training.iloc[-1:].copy()
    unseen["channel"] = "NEW_CHANNEL"
    result = build_features(unseen, "FRAUD", vocabulary)
    assert result.CHANNEL_UNSEEN.iloc[0] == 1
    assert result.CHANNEL_TRAIN_FREQUENCY.iloc[0] == 0
    unseen["currency"] = "IDR"
    with pytest.raises(FeatureInputError, match="currency differs"):
        build_features(unseen, "FRAUD", vocabulary)
    mixed = pd.concat([training, unseen.assign(transaction_ref="foreign")], ignore_index=True)
    with pytest.raises(FeatureInputError, match="mix currency"):
        build_features(mixed, "AML", vocabulary)


def test_week_unit_is_account_week_ending_sunday_utc_and_short_baselines_stay_unknown():
    frame = observed_frame(3)
    result = build_features(frame, "AML", fit_vocabulary(frame))
    assert result.as_of.tolist() == [pd.Timestamp("2023-01-01T23:59:59Z"), pd.Timestamp("2023-01-08T23:59:59Z")]
    assert result.TXN_COUNT_W.tolist() == [1.0, 2.0]
    assert result.transaction_ref.isna().all()
    assert result.NO_BASELINE.eq(1).all()
    assert result[["COUNT_OVER_PRIOR_WEEKLY_MEAN", "DEVICE_NEW_SHARE_W"]].isna().all().all()
