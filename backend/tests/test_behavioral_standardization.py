"""The source contract preserves unknowns and quarantines observable errors."""

import json
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from app.ml.behavioral.standardize import NormalizationError, normalize_csv, normalize_frame


def source_row(**changes):
    row = {"TransactionID": "TX1", "AccountID": "AC1", "TransactionAmount": "14.09",
           "TransactionDate": "4/11/2023", "TransactionType": "Debit", "Channel": "Online",
           "Location": "San Diego", "DeviceID": "D1", "IP Address": "192.0.2.1", "MerchantID": "M1",
           "CustomerAge": "70", "CustomerOccupation": "Doctor", "TransactionDuration": "81",
           "LoginAttempts": "1", "AccountBalance": "5112.21"}
    return {**row, **changes}


def test_explicit_source_profile_preserves_currency_precision_and_unlabelled_provenance(tmp_path):
    rows = [source_row(label="1"), source_row(TransactionID="TX2", TransactionDate="4/11/2023 16:29")]
    path = tmp_path / "source.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    result = normalize_csv(path)
    assert result.report["accepted"] == 2 and result.report["quarantined"] == 0
    assert result.frame.currency.tolist() == ["USD", "USD"]
    assert result.frame.timestamp_precision.tolist() == ["day", "minute"]
    assert not result.frame.source_timezone_known.any()
    assert result.frame.transaction_date.iloc[0] == pd.Timestamp("2023-04-11T00:00:00Z")
    assert result.frame.channel.tolist() == ["ONLINE", "ONLINE"]
    assert result.frame.source_original.iloc[0]["label"] == "1"
    assert "label" not in result.frame and "label" in result.report["excluded_columns"]
    assert result.report["labels_used"] is False
    assert "confirmed" in result.report["currency_provenance"]
    assert result.frame.counterparty_ref.isna().all()
    assert result.frame.counterparty_country.isna().all()


def test_explicit_offset_on_month_first_companion_is_not_inferred():
    result = normalize_frame(pd.DataFrame([source_row(TransactionDate="4/11/2023 16:29:30+00:00")]))
    assert result.frame.source_timezone_known.tolist() == [True]
    assert result.frame.timestamp_precision.tolist() == ["second"]
    assert result.frame.transaction_date.iloc[0] == pd.Timestamp("2023-04-11T16:29:30Z")


def test_alias_ambiguity_and_missing_contract_are_file_errors():
    ambiguous = pd.DataFrame([{**source_row(), "transaction_ref": "different"}])
    with pytest.raises(NormalizationError, match="Ambiguous aliases"):
        normalize_frame(ambiguous)
    with pytest.raises(NormalizationError, match="Missing required"):
        normalize_frame(pd.DataFrame([{"TransactionID": "1"}]))


@pytest.mark.parametrize(("column", "bad_value"), [
    ("TransactionAmount", "inf"), ("TransactionAmount", "-1"), ("TransactionAmount", "1.234"),
    ("TransactionDate", "2/30/2023"), ("LoginAttempts", "-1"), ("LoginAttempts", "1.5"),
    ("TransactionDuration", "NaN"), ("IP Address", "not-an-ip"), ("TransactionType", "unknown"),
])
def test_invalid_values_quarantine_with_original_and_source_ordinal(column, bad_value):
    result = normalize_frame(pd.DataFrame([source_row(), source_row(TransactionID="TX2", **{column: bad_value})]))
    assert result.report["accepted"] == 1 and result.report["quarantined"] == 1
    assert result.quarantine.source_ordinal.tolist() == [2]
    assert result.quarantine.source_original.iloc[0][column] == bad_value
    assert result.quarantine.error_reason.iloc[0]


def test_invalid_first_reference_does_not_suppress_valid_second_row():
    result = normalize_frame(pd.DataFrame([source_row(TransactionAmount="-1"), source_row(), source_row()]))
    assert result.frame.source_ordinal.tolist() == [2]
    assert result.quarantine.source_ordinal.tolist() == [1, 3]
    assert "duplicate transaction_ref" in result.quarantine.error_reason.iloc[1]


def test_canonical_csv_roundtrip_and_unknown_currency(tmp_path):
    original = normalize_frame(pd.DataFrame([source_row()])).frame
    emitted = original.copy()
    emitted["source_original"] = emitted.source_original.map(json.dumps)
    path = tmp_path / "canonical.csv"
    emitted.to_csv(path, index=False)
    result = normalize_csv(path, profile="canonical")
    assert result.report["quarantined"] == 0
    assert_frame_equal(result.frame.drop(columns="schema_profile"), original.drop(columns="schema_profile"))
    generic = original.drop(columns="currency")
    assert normalize_frame(generic, profile="canonical").frame.currency.tolist() == ["XXX"]


def test_customer_demographics_remain_event_observations():
    result = normalize_frame(pd.DataFrame([source_row(), source_row(TransactionID="TX2", CustomerAge="20", CustomerOccupation="Student")]))
    assert result.frame.customer_age.tolist() == [70.0, 20.0]
    assert result.frame.customer_occupation.tolist() == ["Doctor", "Student"]
    assert "customer_country" not in result.frame and "entity_type" not in result.frame


def test_malformed_csv_cannot_silently_shift_or_drop_extra_values(tmp_path):
    path = tmp_path / "malformed.csv"
    path.write_text("TransactionID,AccountID\nTX1,AC1,extra\n")
    with pytest.raises(NormalizationError, match="3 fields"):
        normalize_csv(path)


def test_missing_optional_context_is_unknown_not_zero():
    row = source_row()
    for name in ("LoginAttempts", "TransactionDuration", "AccountBalance", "CustomerAge", "DeviceID", "MerchantID"):
        row.pop(name)
    result = normalize_frame(pd.DataFrame([row]))
    assert np.isnan(result.frame.login_attempts.iloc[0])
    assert result.frame.device_ref.iloc[0] is None


def test_month_first_profile_never_falls_back_to_day_first():
    result = normalize_frame(pd.DataFrame([
        source_row(TransactionDate="13/04/2023"),
        source_row(TransactionID="TX2", TransactionDate="4/13/2023"),
    ]))
    assert result.frame.transaction_ref.tolist() == ["TX2"]
    assert result.frame.transaction_date.tolist() == [pd.Timestamp("2023-04-13T00:00:00Z")]
    assert result.quarantine.source_ordinal.tolist() == [1]


@pytest.mark.parametrize("claimed_precision", ["minute", "second"])
def test_precision_metadata_cannot_invent_a_time(claimed_precision):
    row = {"transaction_ref": "TX1", "account_number": "AC1", "transaction_date": "2023-04-11",
           "amount": "14.09", "currency": "USD", "direction": "DEBIT", "channel": "ONLINE",
           "timestamp_precision": claimed_precision}
    result = normalize_frame(pd.DataFrame([row]), "canonical")
    assert result.frame.empty
    assert "cannot invent finer precision" in result.quarantine.error_reason.iloc[0]


def test_declared_lower_precision_discards_finer_carrier_values():
    frame = normalize_frame(pd.DataFrame([source_row(TransactionDate="4/11/2023 16:29:30")])).frame
    frame["timestamp_precision"] = "minute"
    result = normalize_frame(frame, "canonical")
    assert result.frame.transaction_date.tolist() == [pd.Timestamp("2023-04-11T16:29:00Z")]


def test_exact_money_survives_canonical_csv_even_at_storage_limit(tmp_path):
    result = normalize_frame(pd.DataFrame([
        source_row(TransactionAmount="9999999999999999.99"),
        source_row(TransactionID="TX2", TransactionAmount="10000000000000000.00"),
    ]))
    assert result.frame.amount.tolist() == [Decimal("9999999999999999.99")]
    assert result.quarantine.source_ordinal.tolist() == [2]
    emitted = result.frame.drop(columns=["source_original"])
    path = tmp_path / "exact.csv"
    emitted.to_csv(path, index=False)
    roundtrip = normalize_csv(path, "canonical")
    assert roundtrip.frame.amount.tolist() == [Decimal("9999999999999999.99")]
    json.dumps(roundtrip.frame.source_original.iloc[0])


def test_invalid_provenance_cannot_suppress_a_later_valid_reference():
    result = normalize_frame(pd.DataFrame([
        source_row(source_ordinal="-1"), source_row(source_ordinal="2"),
        source_row(TransactionID="TX3", source_ordinal=str(2**63)),
    ]))
    assert result.frame.source_ordinal.tolist() == [2]
    assert result.quarantine.source_ordinal.tolist() == ["-1", str(2**63)]


def test_empty_file_with_a_valid_header_has_an_empty_reconciled_result(tmp_path):
    path = tmp_path / "empty.csv"
    pd.DataFrame(columns=source_row()).to_csv(path, index=False)
    result = normalize_csv(path)
    assert result.report["total_rows"] == result.report["accepted"] == result.report["quarantined"] == 0
    assert result.frame.empty and result.quarantine.empty
