"""Fit the approved small team-review exercise without touching the application DB.

The prepared CSV must have a creation-time manifest and must reproduce the
supplied transaction/key join exactly. Native selection uses validation only.
Only the selected saved model is scored on TEST, after its artifacts freeze.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import platform
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.lab.features import GROUP_KEYS, build_matrix
from app.ml.lab.mapping import apply_mapping, read_csv_bytes, suggest
from app.ml.lab.trainer import SEED, chronological_split, point, ranking, score, train_run
from app.scripts import audit_current_lab as evidence

VERSION = "team_review_training_v1"
TRAIN_SHARE = .7
MODEL_KEYS = ["logistic_regression", "lightgbm", "isolation_forest"]
BACKEND = Path(__file__).resolve().parents[2]
CODE_PATHS = [
    "app/scripts/train_team_review.py", "app/scripts/audit_current_lab.py",
    "app/ml/lab/trainer.py", "app/ml/lab/features.py", "app/ml/lab/mapping.py",
    "app/ml/behavioral/features.py", "app/ml/behavioral/model.py",
    "app/ml/behavioral/standardize.py", "app/services/tabular.py",
]
LIMITATIONS = [
    "Small synthetic development exercise with team manual labels according to Jevon; independent adjudication history is unavailable.",
    "The supplied script also emits an answer key; these files alone cannot independently distinguish generated intent from human adjudication.",
    "TRAIN share 0.7 was explicitly approved after label-support preflight; TEST support metadata was already inspected, so this is not a fully blind or independent new final.",
    "Chronological rows may share account history and scenario fragments; no independent-episode holdout is claimed.",
    "Small TEST support does not establish broad fraud, AML or production accuracy; absent positive categories are unassessed.",
    "The existing transaction feature set does not model recipient relationships, names or reviewer explanations; these are never predictive features.",
    "Channel/recipe shortcuts can inflate results; current-feature observations cannot recover unobserved intent.",
    "DORMANT remains the supplied scenario name, not proof of FRD P05's ninety-day observed dormancy.",
    "Class-weighted supervised scores are not calibrated fraud probabilities; no production model is registered or activated.",
]


def _rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames
        if not columns or len(columns) != len(set(columns)):
            raise ValueError("Source CSV headers must be present and unique")
        rows = list(reader)
    if any(None in row for row in rows):
        raise ValueError("A source CSV row has extra cells")
    return columns, rows


def _index(rows: list[dict], label: str) -> dict[str, dict]:
    if any(not row.get("transaction_ref") for row in rows):
        raise ValueError(f"{label} contains an empty transaction_ref")
    indexed = {row["transaction_ref"]: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError(f"{label} transaction_ref values are not unique")
    return indexed


def verify_prepared(data: Path, manifest_path: Path, expected_manifest_sha256: str) -> tuple[dict, dict[str, str]]:
    """Reconcile against the original expected hashes, never fresh replacements."""
    if not expected_manifest_sha256 or evidence.sha256(manifest_path) != expected_manifest_sha256:
        raise ValueError("Import manifest differs from its externally pinned preparation hash")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("version") != "team_review_import_v1" or not manifest.get("label_provenance"):
        raise ValueError("A supported import manifest with label provenance is required")
    if evidence.sha256(data) != manifest["output_sha256"]:
        raise ValueError("Prepared data differs from its original expected hash")
    expected = {str(data.resolve()): manifest["output_sha256"]}
    source = Path(manifest["input_directory"])
    hashes = manifest["source_hashes"]
    if not {"transaksi_untuk_diflag.csv", "kunci_jawaban.csv"} <= set(hashes):
        raise ValueError("Original transaction and review-key expectations are required")
    for name, wanted in hashes.items():
        if Path(name).name != name:
            raise ValueError("Import source names must be filenames, not paths")
        original = source / name
        if evidence.sha256(original) != wanted:
            raise ValueError(f"Original source differs from its declared hash: {name}")
        expected[str(original.resolve())] = wanted
    evidence.seal(expected, manifest_path)
    columns, raw = _rows(source / "transaksi_untuk_diflag.csv")
    _, answers = _rows(source / "kunci_jawaban.csv")
    prepared_columns, prepared = _rows(data)
    original, keys, labelled = (_index(rows, kind) for rows, kind in
                                ((raw, "Transactions"), (answers, "Review key"), (prepared, "Prepared data")))
    if set(original) != set(keys) or set(original) != set(labelled):
        raise ValueError("The transaction, key and prepared ID populations must match exactly")
    if prepared_columns != columns + ["pattern_code"]:
        raise ValueError("Prepared columns must preserve source fields plus category metadata only")
    if [row["transaction_ref"] for row in raw] != [row["transaction_ref"] for row in prepared]:
        raise ValueError("Preparation must preserve original row order")
    if len(raw) != manifest["source_rows"]:
        raise ValueError("Original source row count differs from import manifest")
    for ref, row in original.items():
        key, result = keys[ref], labelled[ref]
        if key.get("label") not in {"0", "1"}:
            raise ValueError("Review-key labels must be literal 0 or 1; blanks are unresolved")
        if key.get("account_number") != row.get("account_number"):
            raise ValueError("Review-key account_number differs from its transaction")
        if any(result[column] != (key["label"] if column == "is_fraud" else row[column]) for column in columns):
            raise ValueError("Preparation changed a transaction field or a supplied review label")
        if row.get("is_fraud") not in {"", key["label"]}:
            raise ValueError("Source labels conflict with the supplied review key")
        if not key.get("skenario") or result["pattern_code"] != re.sub(r"-\d+$", "", key["skenario"]):
            raise ValueError("Prepared category metadata differs from the supplied scenario family")
    if sum(row["is_fraud"] == "1" for row in prepared) != manifest["positive_rows"]:
        raise ValueError("Prepared positive count differs from import manifest")
    if sum(row["is_fraud"] == "0" for row in prepared) != manifest["negative_rows"]:
        raise ValueError("Prepared negative count differs from import manifest")
    hint = suggest(read_csv_bytes(data.read_bytes()))
    approved_mapping = {key: hint[key] for key in
                        ("fields", "label_column", "positive_values", "category_column", "date_format")}
    approved_mapping["default_currency"] = "XXX"
    if (approved_mapping["label_column"] != "is_fraud"
            or approved_mapping["positive_values"] != ["1"]
            or approved_mapping["category_column"] != "pattern_code"
            or approved_mapping["date_format"] != "iso"
            or manifest.get("mapping") != approved_mapping):
        raise ValueError("Manifest mapping differs from the approved literal labels and transaction fields")
    return manifest, expected


def assert_validation_only(outcome) -> None:
    if "positive_rate_test" in outcome.results or outcome.results["split"]["test"].get("positives") is not None:
        raise ValueError("TEST target summaries must be withheld from model comparison")
    if any(any(key.startswith("test") for key in model) for model in outcome.results["models"]):
        raise ValueError("Model comparison must contain validation results only")
    if any(model.test_curve for model in outcome.models):
        raise ValueError("Native training computed TEST predictions before selection")


def diagnostic(frame: pd.DataFrame, y: np.ndarray, categories: pd.Series, parts: np.ndarray,
               matrix: pd.DataFrame, source: Path) -> dict:
    splits = {}
    for name in ("train", "validation", "test"):
        mask = parts == name
        splits[name] = {"rows": int(mask.sum()), "positive_rows": int(y[mask].sum()),
                        "negative_rows": int(mask.sum() - y[mask].sum()),
                        "positive_families": dict(Counter(categories[mask & (y == 1)]))}
    cross = pd.DataFrame({"channel": frame.channel.to_numpy(), "label": y})
    channels = {str(channel): {"rows": len(group), "positive_rows": int(group.label.sum()),
                              "negative_rows": int(len(group) - group.label.sum())}
                for channel, group in cross.groupby("channel")}
    _, key_rows = _rows(source / "kunci_jawaban.csv")
    key = {r["transaction_ref"]: r for r in key_rows}
    episodes = pd.DataFrame({"scenario": [key[ref]["skenario"] for ref in frame.transaction_ref],
                             "label": y, "part": parts})
    episode_splits = episodes[episodes.label == 1].groupby("scenario").part.agg(lambda v: sorted(set(v)))
    return {"partition_support_already_inspected": True, "partitions": splits,
            "channel_label_counts": channels,
            "insufficient_history_rows": int((matrix.NO_BASELINE == 1).sum()),
            "rows_with_prior_mean_amount_comparison": int(matrix.AMOUNT_OVER_PRIOR_MEAN.notna().sum()),
            "positive_episodes_crossing_partitions": {name: values for name, values in episode_splits.items() if len(values) > 1},
            "positive_families_absent_test": sorted(set(categories[y == 1]) - set(categories[(parts == "test") & (y == 1)])),
            "independent_episode_holdout": False}


def evaluate_selected(model_key: str, model_text: str, features: list[str], threshold: float,
                      frame: pd.DataFrame, y: np.ndarray, categories: pd.Series,
                      matrix: pd.DataFrame, small_cutoff: float) -> tuple[dict, pd.DataFrame]:
    predictions = score(model_key, model_text, matrix.loc[:, features])
    if len(predictions) != len(frame) or not np.isfinite(predictions).all():
        raise ValueError("Every selected TEST score must be present and finite")
    measures = {**point(predictions, y, threshold), **ranking(predictions, y),
                "rows": len(y), "positive_rows": int(y.sum()), "negative_rows": int(len(y) - y.sum())}
    families = {}
    for family in sorted(set(categories)):
        positive = (categories.to_numpy() == family) & (y == 1)
        count = int(positive.sum())
        caught = int((positive & (predictions >= threshold)).sum())
        families[family] = {"positive_rows": count, "caught": caught, "missed": count - caught,
                            "recall": caught / count if count else None}
    small = frame.amount.to_numpy(dtype=float) <= small_cutoff
    small_metrics = ({**point(predictions[small], y[small], threshold), **ranking(predictions[small], y[small]),
                      "rows": int(small.sum()), "positive_rows": int(y[small].sum())} if small.any() else None)
    cache = frame[["transaction_ref", "account_number", "transaction_date", "amount", "channel"]].copy()
    cache["label"] = y
    cache["category"] = categories.to_numpy()
    cache["score"] = predictions
    cache["flagged"] = predictions >= threshold
    return {"all": measures, "by_family": families, "small_amount_train_q25": small_metrics}, cache


def run(data: Path, out: Path, manifest_path: Path | None = None, *, expected_manifest_sha256: str) -> dict:
    data, out = data.resolve(), out.resolve()
    manifest_path = (manifest_path or data.parent / "manifest.json").resolve()
    if out.exists():
        raise FileExistsError("Study output must be new; existing studies are immutable")
    manifest, expected = verify_prepared(data, manifest_path, expected_manifest_sha256)
    for name in CODE_PATHS:
        evidence.seal(expected, BACKEND / name)
    mapped = apply_mapping(read_csv_bytes(data.read_bytes()), manifest["mapping"])
    if (not mapped.report["ready"] or mapped.report["usable_rows"] != manifest["source_rows"]
            or mapped.report["quarantined"] or mapped.report["missing_label"]):
        raise ValueError("Every prepared observation must pass current normalization with a known label")
    parts = chronological_split(mapped.frame, TRAIN_SHARE)
    out.mkdir(parents=True, exist_ok=False)
    protocol = {"version": VERSION, "created_at_utc": evidence.now(), "state": "OFFLINE_DRAFT",
                "train_share": TRAIN_SHARE, "feature_groups": list(GROUP_KEYS), "models": MODEL_KEYS,
                "seed": SEED, "approval": "Jevon explicitly chose offline70/15/15 on2026-11-10 after label-support preflight.",
                "target": "Team's supplied 0/1 transaction-review decisions on synthetic observations; not verified crime.",
                "label_provenance": manifest["label_provenance"], "python": platform.python_version(),
                "preparation_manifest_sha256": expected_manifest_sha256,
                "software": {name: importlib.import_module(name).__version__ for name in
                             ("numpy", "pandas", "pyarrow", "sklearn", "lightgbm")},
                "test_metadata_already_inspected": True, "candidate_test_predictions_before_freeze": False,
                "final_policy": "One native validation AP winner at its native validation max-F1 default; only that candidate scores TEST.",
                "limitations": LIMITATIONS}
    evidence.write_json(out / "protocol.json", protocol, expected)
    print("Training three native candidates; TEST predictions remain withheld", flush=True)
    evidence.verify_hashes(expected)
    outcome = train_run(mapped.frame, mapped.labels, mapped.categories,
                        feature_groups=list(GROUP_KEYS), model_keys=MODEL_KEYS, train_share=TRAIN_SHARE, seed=SEED)
    assert_validation_only(outcome)
    evidence.verify_hashes(expected)
    matrix, vocabulary = build_matrix(mapped.frame, parts == "train")
    if vocabulary != outcome.results["features"]["vocabulary"]:
        raise ValueError("TRAIN-only vocabulary does not reproduce native training")
    selection = evidence.choose_defaults(outcome.results, outcome.models)
    chosen = selection["selected_model"]
    cutoff = float(mapped.frame.loc[parts == "train", "amount"].astype(float).quantile(.25))
    diagnostics = diagnostic(mapped.frame, mapped.labels, mapped.categories, parts, matrix,
                             Path(manifest["input_directory"]))
    evidence.write_json(out / "preflight.json", diagnostics, expected)
    evidence.write_json(out / "validation_results.json", outcome.results, expected)
    evidence.write_json(out / "mapping.json", manifest["mapping"], expected)
    evidence.write_json(out / "preprocessing.json", {"vocabulary": vocabulary, "small_amount_train_q25": cutoff}, expected)
    evidence.write_json(out / "selection.json", selection, expected)
    model_dir = out / "models"
    model_dir.mkdir()
    for model in outcome.models:
        evidence.write_text(model_dir / f"{model.key}.txt", model.model_text, expected)
        evidence.write_json(model_dir / f"{model.key}_contract.json", selection["models"][model.key], expected)
    evidence.verify_hashes(expected)
    frozen = {"frozen_at_utc": evidence.now(), "files": dict(expected), "selection": selection,
              "test_predictions_computed": False}
    evidence.write_json(out / "frozen.json", frozen)
    frozen_sha = evidence.sha256(out / "frozen.json")
    evidence.verify_frozen(frozen, out / "frozen.json", frozen_sha)
    selection = json.loads((out / "selection.json").read_text())
    chosen = selection["selected_model"]
    contract = json.loads((model_dir / f"{chosen}_contract.json").read_text())
    if contract != selection["models"][chosen]:
        raise ValueError("Saved model contract differs from the frozen selection")
    model_text = (model_dir / f"{chosen}.txt").read_text()
    sealed_preprocessing = json.loads((out / "preprocessing.json").read_text())
    sealed_mapping = json.loads((out / "mapping.json").read_text())
    # Reload all inputs and reconstruct features from the sealed TRAIN vocabulary.
    sealed_data = apply_mapping(read_csv_bytes(data.read_bytes()), sealed_mapping)
    from app.ml.behavioral.features import build_features
    from app.ml.lab.features import UNIT
    rebuilt = build_features(sealed_data.frame, UNIT, sealed_preprocessing["vocabulary"])
    order = pd.Series(np.arange(len(sealed_data.frame)), index=sealed_data.frame.transaction_ref.to_numpy())
    rebuilt = rebuilt.set_index(order.loc[rebuilt.transaction_ref].to_numpy()).sort_index()
    if not np.array_equal(rebuilt[contract["features"]].to_numpy(), matrix[contract["features"]].to_numpy(), equal_nan=True):
        raise ValueError("Sealed selected features differ from the pre-final TRAIN vocabulary")
    evidence.verify_frozen(frozen, out / "frozen.json", frozen_sha)
    test = parts == "test"
    print(f"Frozen validation winner {chosen}; evaluating its selected TEST once", flush=True)
    scored_at = evidence.now()
    final, cache = evaluate_selected(chosen, model_text, contract["features"], contract["threshold"],
        sealed_data.frame.loc[test].reset_index(drop=True), sealed_data.labels[test],
        sealed_data.categories[test].reset_index(drop=True), rebuilt.loc[test].reset_index(drop=True),
        sealed_preprocessing["small_amount_train_q25"])
    # Make unsupported source families explicit rather than omitting their null recall.
    for family in sorted(set(sealed_data.categories[sealed_data.labels == 1])):
        final["by_family"].setdefault(family, {"positive_rows": 0, "caught": 0, "missed": 0, "recall": None})
    score_path = out / "selected_test_scores.parquet"
    cache.to_parquet(score_path, index=False)
    final_hashes = {str(score_path): evidence.sha256(score_path)}
    evidence.verify_frozen(frozen, out / "frozen.json", frozen_sha)
    evidence.verify_hashes(final_hashes)
    report = {"version": VERSION, "state": "OFFLINE_DRAFT", "completed_at_utc": evidence.now(),
              "protocol": protocol, "source_validation": mapped.report, "preflight": diagnostics,
              "validation_results": outcome.results, "selection": selection, "selected_test": final,
              "small_amount_train_q25": cutoff, "test_score_started_at_utc": scored_at,
              "freeze_precedes_test_scoring": frozen["frozen_at_utc"] < scored_at,
              "frozen": frozen, "frozen_manifest_sha256": frozen_sha,
              "test_artifact_hashes": final_hashes, "test_scored_model_keys": [chosen],
              "post_test_retuning": False, "application_database_accessed": False,
              "registered_or_activated": False, "limitations": LIMITATIONS}
    evidence.write_json(out / "report.json", report)
    print(json.dumps({"report": str(out / "report.json"), "selected": chosen, "test": final["all"]}, indent=2), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    run(args.data, args.out, args.manifest, expected_manifest_sha256=args.manifest_sha256)


if __name__ == "__main__":
    main()
