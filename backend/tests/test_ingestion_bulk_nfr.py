"""NFR-05 (FRD §14.1): ingesting 100k transactions, including validation,
must complete within 10 minutes.

Excluded from the default `pytest` run (see pytest.ini). Run explicitly:
    pytest -m slow -v tests/test_ingestion_bulk_nfr.py
"""

import time

import pytest

from app.scripts.generate_bulk_transactions import write_csv
from app.scripts.seed import seed_master_data
from app.services.ingestion import ingest_transactions_csv

NFR_05_BUDGET_SECONDS = 10 * 60
TOTAL_ROWS = 100_000
INVALID_RATIO = 0.05


@pytest.mark.slow
def test_ingesting_100k_transactions_completes_within_nfr_05_budget(db, tmp_path):
    seed_master_data(db)
    csv_path = tmp_path / "transactions_bulk.csv"
    total, expected_invalid = write_csv(csv_path, total=TOTAL_ROWS, invalid_ratio=INVALID_RATIO)
    content = csv_path.read_text()

    started = time.perf_counter()
    summary = ingest_transactions_csv(db, file_name="transactions_bulk.csv", content=content)
    elapsed = time.perf_counter() - started

    print(
        f"\nNFR-05: ingested {summary.total_rows:,} rows in {elapsed:.1f}s "
        f"(accepted={summary.accepted:,}, quarantined={summary.quarantined:,}, "
        f"budget={NFR_05_BUDGET_SECONDS}s)"
    )

    assert summary.total_rows == total
    assert summary.quarantined == expected_invalid
    assert summary.accepted == total - expected_invalid
    assert elapsed < NFR_05_BUDGET_SECONDS, (
        f"NFR-05 violated: ingestion took {elapsed:.1f}s, budget is {NFR_05_BUDGET_SECONDS}s"
    )
