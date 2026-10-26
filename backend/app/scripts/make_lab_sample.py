"""Flatten a generated raw dataset into one labelled CSV for the model lab.

    python -m app.scripts.generate_raw_dataset --target-transactions 100000 \
        --min-per-pattern 40 --seed 20261026 --out ml_data/lab/raw_s20261026
    python -m app.scripts.make_lab_sample --raw-dir ml_data/lab/raw_s20261026 \
        --out ml_data/lab/fcip_lab_sample_s20261026.csv

The lab trains on a single file with a label column, the shape a bank or a
public dataset would hand over. This script produces that shape from the
generator's contract files, keeping the generator's own column names (so the
lab's column-mapping step has real work to do) and its injected defects (so
validation and quarantine show up too).

Label rule, the same one `app/ml/labels.py` applies for the Fraud model: a
transaction is positive when it is one of the labelled entity's own rows of an
INJECTED_POSITIVE scenario, inside the label window, for one of the chosen
patterns (default ATO, P04, P05: the Fraud model's patterns). Everything else
is 0, including look-alikes and other patterns' scenario rows. That is a
simplification for a single-label file, recorded in the output summary.
`pattern_code` names the pattern of each positive row, for reporting only.

The output is synthetic (TRD §10.6, FRD L-01) and lives under the git-ignored
`ml_data/`; it is reproducible from the seed.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

DEFAULT_PATTERNS = ("ATO", "P04", "P05")
OUTPUT_COLUMNS = (
    "source_transaction_reference", "source_account_id", "direction", "amount_original",
    "currency_original", "value_datetime", "channel", "transaction_type", "counterparty_country",
    "source_merchant_id", "source_device_id", "ip_address", "status_from_source",
)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_sample(raw_dir: Path, out: Path, *, patterns: tuple[str, ...] = DEFAULT_PATTERNS,
                 label_column: str = "is_fraud") -> dict:
    owner = {row["source_account_id"]: row["owner_source_id"] for row in _read(raw_dir / "accounts.csv")}
    labels: dict[tuple[str, str], dict[str, str]] = {}
    for row in _read(raw_dir / "labels.csv"):
        if row["label_type"] == "INJECTED_POSITIVE" and row["pattern_code"] in patterns:
            labels[(row["scenario_id"], row["entity_source_id"])] = row
    scenario_refs: dict[str, list[str]] = {}
    for row in _read(raw_dir / "label_transactions.csv"):
        scenario_refs.setdefault(row["scenario_id"], []).append(row["source_transaction_reference"])

    transactions = _read(raw_dir / "transactions.csv")
    by_ref = {row["source_transaction_reference"]: row for row in transactions}
    positive: dict[str, str] = {}
    for (scenario_id, entity), label in labels.items():
        start, end = date.fromisoformat(label["window_start"]), date.fromisoformat(label["window_end"])
        for ref in scenario_refs.get(scenario_id, ()):
            row = by_ref.get(ref)
            if row is None or owner.get(row["source_account_id"]) != entity:
                continue
            try:
                business_date = date.fromisoformat(row["business_date"])
            except ValueError:  # an injected date defect: ingestion would quarantine it anyway
                continue
            if start <= business_date <= end:
                positive[ref] = label["pattern_code"]

    out.parent.mkdir(parents=True, exist_ok=True)
    per_pattern: dict[str, int] = {}
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([*OUTPUT_COLUMNS, label_column, "pattern_code"])
        for row in transactions:
            pattern = positive.get(row["source_transaction_reference"], "")
            if pattern:
                per_pattern[pattern] = per_pattern.get(pattern, 0) + 1
            writer.writerow([*(row[c] for c in OUTPUT_COLUMNS), 1 if pattern else 0, pattern])

    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    return {
        "rows": len(transactions), "positives": len(positive), "positives_per_pattern": per_pattern,
        "patterns": list(patterns), "label_column": label_column, "output": str(out),
        "source_manifest": {k: manifest.get(k) for k in ("generator_version", "seed", "profile")},
        "label_rule": "own in-window rows of INJECTED_POSITIVE scenarios for the chosen patterns; "
                      "look-alikes and other patterns' rows are 0 (single-label simplification)",
        "synthetic": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Flatten a generated raw dataset into one labelled lab CSV.")
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--patterns", default=",".join(DEFAULT_PATTERNS),
                        help="comma-separated pattern codes counted as positive (default: the Fraud model's)")
    parser.add_argument("--label-column", default="is_fraud")
    args = parser.parse_args()
    summary = build_sample(args.raw_dir, args.out, label_column=args.label_column,
                           patterns=tuple(p.strip() for p in args.patterns.split(",") if p.strip()))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
