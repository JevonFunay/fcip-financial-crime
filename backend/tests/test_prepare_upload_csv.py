"""The browser adapter preserves the bridge's row stream and API byte cap."""

import csv
import hashlib
import json
from pathlib import Path

import pytest

from app.ml.loader import load_dataset
from app.models.transaction import Transaction
from app.routers.ingestion import MAX_UPLOAD_BYTES
from app.scripts.load_raw_dataset import APP_COLUMNS, load_master_data, load_merchants_and_devices, project_transactions
from app.scripts.prepare_upload_csv import UploadPreparationError, _csv_bytes, prepare_upload_csv
from app.scripts.raw_contract import FILE_SPECS, GENERATOR_VERSION
from app.services.ingestion import ingest_transactions_csv
from app.services.ml_scoring import dataset_from_db
from sqlalchemy import select

def _raw_dataset(tmp_path: Path, transactions: list[dict]) -> Path:
    """Source-shaped fixtures without generating or changing shared data."""
    raw = tmp_path / "raw"
    raw.mkdir()
    files = []
    for spec in FILE_SPECS:
        path = raw / spec.name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=spec.header, lineterminator="\n")
            writer.writeheader()
            if spec.name == "transactions.csv":
                writer.writerows(transactions)
        files.append({"name": spec.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "record_count": len(transactions) if spec.name == "transactions.csv" else 0})
    (raw / "manifest.json").write_text(json.dumps({
        "source_system_code": "NDP_WALLET_CORE", "business_date": "2026-09-30", "contract_version": "1",
        "synthetic_declaration": True, "generator_version": GENERATOR_VERSION, "profile": "fixture", "seed": 7,
        "files": files,
    }), encoding="utf-8")
    # Deliberately present: this must never reach the upload directory.
    (raw / "labels.csv").write_text("GROUND_TRUTH_MUST_STAY_OUT\n", encoding="utf-8")
    return raw


def _row(**changes) -> dict:
    row = {
        "source_transaction_reference": "T-001", "source_account_id": "A-001", "direction": "IN",
        "amount_original": "1000.00", "currency_original": "IDR", "value_datetime": "2026-09-01T09:00:00+07:00",
        "business_date": "2026-09-01", "channel": "TRANSFER", "transaction_type": "P2P_TRANSFER",
        "counterparty_reference": "CP-1", "counterparty_name": 'Dewi, "雪"\r\nbaris kedua',
        "counterparty_country": "ID", "source_merchant_id": "", "source_device_id": "D-1",
        "ip_address": "198.51.100.1", "status_from_source": "SETTLED",
    }
    row.update(changes)
    return row


def _rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        assert next(reader) == list(APP_COLUMNS)
        return list(reader)


def test_split_preserves_unicode_quotes_multiline_defects_and_cross_part_duplicates(tmp_path):
    source_rows = [_row(), _row(), _row(source_transaction_reference="T-003", amount_original="-1.00",
                                        value_datetime="01/09/2026 09:00")]
    raw = _raw_dataset(tmp_path, source_rows)
    complete = tmp_path / "complete.csv"
    project_transactions(raw, complete)
    projected_rows = _rows(complete)
    # Fit exactly one of the longest logical records, including the header.
    cap = len(_csv_bytes(APP_COLUMNS)) + max(len(_csv_bytes(row)) for row in projected_rows)
    output = tmp_path / "upload"

    report = prepare_upload_csv(raw, output, max_bytes=cap)

    reassembled = [row for part in report["parts"] for row in _rows(output / part["name"])]
    assert reassembled == projected_rows
    assert len(report["parts"]) >= 2
    assert _rows(output / report["parts"][0]["name"])[0] == _rows(output / report["parts"][1]["name"])[0]
    # Preserve the bridge's exact value, including its existing newline handling.
    assert reassembled[0][8] == projected_rows[0][8] == 'P2P_TRANSFER Dewi, "雪"\nbaris kedua'
    assert reassembled[2][2:4] == ["01/09/2026 09:00", "-1.00"]
    assert report["projected_sha256"] == report["reassembled_sha256"] == hashlib.sha256(complete.read_bytes()).hexdigest()
    assert report["source_manifest_sha256"] == hashlib.sha256((raw / "manifest.json").read_bytes()).hexdigest()
    assert report["raw_transaction_count"] == report["projected_transaction_count"] == 3
    assert report["seed"] == 7 and report["generator_version"] == GENERATOR_VERSION
    assert report["reference_date"] == report["business_date"] == "2026-09-30"
    assert report["reassembly_verified"] is True
    assert set(p.name for p in output.iterdir()) == {p["name"] for p in report["parts"]} | {"upload_manifest.json"}
    for part in report["parts"]:
        path = output / part["name"]
        assert part["size_bytes"] == path.stat().st_size <= cap <= MAX_UPLOAD_BYTES
        assert part["record_count"] == len(_rows(path))
        assert part["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert json.loads((output / "upload_manifest.json").read_text()) == report


def test_oversize_record_refuses_without_publishing_partial_success(tmp_path):
    raw = _raw_dataset(tmp_path, [_row(counterparty_name="short"), _row(counterparty_name="雪" * 1000)])
    output = tmp_path / "upload"
    with pytest.raises(UploadPreparationError, match="cannot fit in one upload"):
        prepare_upload_csv(raw, output, max_bytes=len(_csv_bytes(APP_COLUMNS)) + 400)
    assert not output.exists()
    assert not list(tmp_path.glob(".upload-*"))


def test_existing_output_is_never_overwritten(tmp_path):
    raw = _raw_dataset(tmp_path, [_row()])
    output = tmp_path / "upload"
    output.mkdir()
    existing = output / "transactions_001.csv"
    existing.write_bytes(b"already prepared\n")
    with pytest.raises(UploadPreparationError, match="destination already exists"):
        prepare_upload_csv(raw, output)
    assert existing.read_bytes() == b"already prepared\n"
    assert list(output.iterdir()) == [existing]


def test_parts_use_the_actual_api_cap_and_bridge_currency_semantics(tmp_path):
    raw = _raw_dataset(tmp_path, [_row(), _row(currency_original="USD"), _row(currency_original="1DR")])
    with pytest.raises(UploadPreparationError, match="API cap"):
        prepare_upload_csv(raw, tmp_path / "too_large", max_bytes=MAX_UPLOAD_BYTES + 1)
    report = prepare_upload_csv(raw, tmp_path / "upload")
    assert report["max_upload_bytes"] == MAX_UPLOAD_BYTES
    assert report["raw_transaction_count"] == 3
    assert report["projected_transaction_count"] == 2
    assert report["non_idr_rows_dropped"] == 1
    assert [r[4] for r in _rows(tmp_path / "upload" / report["parts"][0]["name"])] == ["IDR", "1DR"]


def test_split_small_ingestion_keeps_offline_accepted_transactions(db, tmp_path, legacy_small_raw):
    """Multiple uploads keep first-wins duplicates and deliberately bad rows."""
    output = tmp_path / "upload"
    report = prepare_upload_csv(legacy_small_raw, output, max_bytes=100_000)
    assert len(report["parts"]) > 1
    load_master_data(db, legacy_small_raw)
    load_merchants_and_devices(db, legacy_small_raw)
    total = accepted = quarantined = 0
    for part in report["parts"]:
        path = output / part["name"]
        summary = ingest_transactions_csv(db, file_name=path.name, content=path.read_text(encoding="utf-8"))
        total += summary.total_rows
        accepted += summary.accepted
        quarantined += summary.quarantined
    offline = load_dataset(legacy_small_raw)
    assert total == report["projected_transaction_count"] == 20_916
    assert accepted == len(offline.transactions) == 20_458
    assert quarantined == 458
    assert set(db.scalars(select(Transaction.transaction_ref))) == {t.ref for t in offline.transactions}
    actual, _, _ = dataset_from_db(db)
    assert {t.ref: t for t in actual.transactions} == {t.ref: t for t in offline.transactions}
    assert actual.accounts == offline.accounts
    assert actual.merchants == offline.merchants
    assert actual.devices == offline.devices
