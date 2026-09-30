"""The MCC ticket reference used by fs_v2 (ASSUMPTION AS-04, PROJECT_CONTEXT §8).

FRD §8.10 compares a merchant's average ticket with "titik tengah pita
kategori" (the midpoint of its category's band) and gives no values for the
bands. The reference is the **median incoming ticket per MCC over the training
dataset**: a peer-group baseline. It is computed once, stored as a versioned
artefact, and used as it is at test, inference and live time; it is never
recomputed from the data being scored, so a merchant's own anomaly cannot move
its yardstick. It is deliberately not taken from the generator's configuration,
which would make the feature read the recipe.

    python -m app.ml.reference ml_data/train_full_s20260923

writes `app/reference/mcc_ticket_ref_v1.json`. Only transactions go in: a
merchant payment received by the business that owns the merchant, as the
feature library loads them (ingestion's validation, no labels).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from app.ml.feature_set import REFERENCE_DIR
from app.ml.loader import Dataset, load_dataset
from app.scripts.load_raw_dataset import DatasetRejected, check_manifest

REFERENCE_VERSION = "mcc_ticket_ref_v1"
REFERENCE_PATH = REFERENCE_DIR / f"{REFERENCE_VERSION}.json"
# An MCC seen fewer times than this has no reference; its ticket features are NaN.
MIN_PAYMENTS = 30


def compute(dataset: Dataset) -> dict[str, dict]:
    """MCC -> median incoming ticket, with the support behind it."""
    amounts: dict[str, list[float]] = defaultdict(list)
    merchants: dict[str, set[str]] = defaultdict(set)
    for txn in dataset.transactions:
        merchant = dataset.merchants.get(txn.merchant)
        if merchant is None or txn.out or merchant.business != txn.entity or not merchant.mcc:
            continue
        amounts[merchant.mcc].append(txn.amount)
        merchants[merchant.mcc].add(txn.merchant)
    return {
        mcc: {"median_ticket_idr": round(float(np.median(values)), 2), "payments": len(values),
              "merchants": len(merchants[mcc])}
        for mcc, values in sorted(amounts.items()) if len(values) >= MIN_PAYMENTS
    }


def _definition_hash(values: dict[str, dict]) -> str:
    canonical = json.dumps({mcc: v["median_ticket_idr"] for mcc, v in values.items()}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def build(dataset_dir: Path, out_path: Path = REFERENCE_PATH) -> dict:
    manifest, warnings = check_manifest(dataset_dir)
    values = compute(load_dataset(dataset_dir))
    artefact = {
        # TRD §10.5 envelope; this is a component of feature set fs_v2.
        "artefact_type": "FEATURE_SET",
        "version": REFERENCE_VERSION,
        "definition_hash": _definition_hash(values),
        "state": "DRAFT",
        "created_by": "app.ml.reference",
        "submitted_by": None,
        "approved_by": None,
        "approved_at": None,
        "simulation_reference": None,
        "effective_from": None,
        "superseded_by": None,
        "assumption": "AS-04",
        "method": "median incoming merchant ticket per MCC; payments received by the merchant's own business; "
                  f"MCCs with fewer than {MIN_PAYMENTS} payments are left out",
        "computed_from": {
            "generator_version": manifest.get("generator_version"),
            "profile": manifest.get("profile"),
            "seed": manifest.get("seed"),
            "manifest_sha256": hashlib.sha256((dataset_dir / "manifest.json").read_bytes()).hexdigest(),
            "warnings": warnings,
        },
        "mcc": values,
    }
    out_path.write_text(json.dumps(artefact, indent=2) + "\n", encoding="utf-8")
    return artefact


def load(path: Path = REFERENCE_PATH) -> dict[str, float]:
    """MCC -> reference ticket, exactly as stored. Refuses an edited file."""
    artefact = json.loads(path.read_text(encoding="utf-8"))
    if _definition_hash(artefact["mcc"]) != artefact["definition_hash"]:
        raise ValueError(f"{path} does not match its definition_hash: edited after it was built")
    return {mcc: float(v["median_ticket_idr"]) for mcc, v in artefact["mcc"].items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the MCC ticket reference from the training dataset.")
    parser.add_argument("dataset", type=Path, help="the training dataset (never the test set)")
    args = parser.parse_args()
    try:
        artefact = build(args.dataset)
    except DatasetRejected as exc:
        raise SystemExit(f"REJECTED: {exc}") from exc
    print(f"{REFERENCE_VERSION}: {len(artefact['mcc'])} MCCs from seed {artefact['computed_from']['seed']} "
          f"-> {REFERENCE_PATH}")


if __name__ == "__main__":
    main()
