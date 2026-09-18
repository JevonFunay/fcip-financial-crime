"""Generates a synthetic transaction CSV for NFR-05 volume testing (FRD §14.1):
ingesting 100k transactions, including validation, must complete within 10 minutes.

Run via: docker compose exec backend python -m app.scripts.generate_bulk_transactions
Writes backend/sample_data/transactions_bulk.csv by default (~95% valid rows
referencing the accounts app/scripts/seed.py creates, ~5% invalid with the
same error variety as sample_data/transactions_sample.csv).
"""

import argparse
import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

HEADER = [
    "transaction_ref",
    "account_number",
    "transaction_date",
    "amount",
    "currency",
    "direction",
    "channel",
    "counterparty_ref",
    "description",
]

# Must match the accounts app/scripts/seed.py creates.
SEEDED_ACCOUNTS = [
    ("ACC-1001", "IDR"),
    ("ACC-1002", "IDR"),
    ("ACC-2001", "IDR"),
    ("ACC-3001", "IDR"),
    ("ACC-4001", "IDR"),
    ("ACC-5001", "IDR"),
    ("ACC-5002", "USD"),
    ("ACC-6001", "IDR"),
]
CHANNELS = ["CASH", "TRANSFER", "WIRE", "EFT"]
DIRECTIONS = ["CREDIT", "DEBIT"]
WINDOW_START = datetime(2027, 1, 1, tzinfo=timezone.utc)
WINDOW_DAYS = 300
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "sample_data" / "transactions_bulk.csv"


def _valid_row(index: int, rng: random.Random) -> list[str]:
    account, currency = rng.choice(SEEDED_ACCOUNTS)
    when = WINDOW_START + timedelta(seconds=rng.randint(0, WINDOW_DAYS * 24 * 3600))
    amount = round(rng.uniform(10_000, 480_000_000), 2)
    counterparty = f"CP-{rng.randint(1000, 9999)}" if rng.random() < 0.6 else ""
    return [
        f"BULK-{index:07d}",
        account,
        when.isoformat(),
        f"{amount:.2f}",
        currency,
        rng.choice(DIRECTIONS),
        rng.choice(CHANNELS),
        counterparty,
        "Synthetic bulk transaction",
    ]


# Same failure modes exercised individually in sample_data/transactions_sample.csv,
# so quarantine gets the same error variety at volume.
_INVALID_VARIANTS = [
    lambda row: row[:3] + ["not-a-number"] + row[4:],  # unparseable amount
    lambda row: row[:3] + ["-1500000.00"] + row[4:],  # negative amount
    lambda row: row[:2] + ["2027-02-30"] + row[3:],  # impossible calendar date
    lambda row: row[:5] + ["SIDEWAYS"] + row[6:],  # invalid direction
    lambda row: [row[0], "ACC-9999"] + row[2:],  # account does not exist
    lambda row: row[:4] + [""] + row[5:],  # missing currency
]


def generate_rows(total: int, invalid_ratio: float, seed: int):
    rng = random.Random(seed)
    n_invalid = round(total * invalid_ratio)
    invalid_indices = set(rng.sample(range(total), n_invalid))
    for i in range(total):
        row = _valid_row(i, rng)
        if i in invalid_indices:
            row = rng.choice(_INVALID_VARIANTS)(row)
        yield row


def write_csv(path: Path, *, total: int = 100_000, invalid_ratio: float = 0.05, seed: int = 42) -> tuple[int, int]:
    """Writes the CSV and returns (total_rows, expected_invalid_rows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_invalid = round(total * invalid_ratio)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(generate_rows(total, invalid_ratio, seed))
    return total, n_invalid


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--total", type=int, default=100_000)
    parser.add_argument("--invalid-ratio", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    total, n_invalid = write_csv(args.output, total=args.total, invalid_ratio=args.invalid_ratio, seed=args.seed)
    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(f"wrote {total:,} rows ({total - n_invalid:,} valid, {n_invalid:,} invalid) to {args.output}")
    print(f"file size: {size_mb:.2f} MB")
