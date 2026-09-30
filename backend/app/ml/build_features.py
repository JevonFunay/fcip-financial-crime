"""Build the fs_v1 feature matrices for one generated dataset.

    python -m app.ml.build_features ml_data/train_full_s20260923
    python -m app.ml.build_features ml_data/test_full_s20261001

Writes, next to the dataset (ignored by git, like the dataset itself):
    features_fs_v1/aml.parquet     one row per (entity, calendar week)
    features_fs_v1/fraud.parquet   one row per accepted transaction
    features_fs_v1/build.json      provenance: dataset manifest, feature set, timings

Labels are not joined here; that is stage 2, and it is the only place they
meet the features.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from app.ml.feature_set import FEATURE_SET_VERSION, PARAMETERS
from app.ml.features import build_matrices
from app.ml.loader import load_dataset
from app.scripts.load_raw_dataset import DatasetRejected, check_manifest


def build(dataset_dir: Path, out_dir: Path | None = None) -> dict:
    manifest, warnings = check_manifest(dataset_dir)
    out_dir = out_dir or dataset_dir / f"features_{FEATURE_SET_VERSION}"
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    dataset = load_dataset(dataset_dir)
    loaded = time.perf_counter()
    aml, fraud = build_matrices(dataset)
    built = time.perf_counter()
    aml.to_parquet(out_dir / "aml.parquet", index=False)
    fraud.to_parquet(out_dir / "fraud.parquet", index=False)

    report = {
        "feature_set_version": FEATURE_SET_VERSION,
        "parameters": {p.name: p.value for p in PARAMETERS},
        "dataset": {
            "path": str(dataset_dir),
            "generator_version": manifest.get("generator_version"),
            "profile": manifest.get("profile"),
            "seed": manifest.get("seed"),
            "manifest_sha256": hashlib.sha256((dataset_dir / "manifest.json").read_bytes()).hexdigest(),
            "warnings": warnings,
        },
        "rows": {
            "transactions_accepted": len(dataset.transactions),
            "transactions_not_used": dict(dataset.rejected),
            "entities": len({t.entity for t in dataset.transactions}),
            "aml": len(aml),
            "fraud": len(fraud),
        },
        "seconds": {"load": round(loaded - started, 1), "features": round(built - loaded, 1)},
    }
    (out_dir / "build.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the fs_v1 feature matrices for a generated dataset.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    try:
        report = build(args.dataset, args.out)
    except DatasetRejected as exc:
        raise SystemExit(f"REJECTED: {exc}") from exc
    rows, seconds = report["rows"], report["seconds"]
    print(f"{FEATURE_SET_VERSION} from {args.dataset} (generator {report['dataset']['generator_version']}, "
          f"seed {report['dataset']['seed']})")
    print(f"  accepted transactions {rows['transactions_accepted']:,}; not used {rows['transactions_not_used']}")
    print(f"  AML rows {rows['aml']:,} · Fraud rows {rows['fraud']:,} · entities {rows['entities']:,}")
    print(f"  load {seconds['load']}s · features {seconds['features']}s")


if __name__ == "__main__":
    main()
