"""Measure the unchanged model lab on a frozen, fresh synthetic monitoring draw.

This is an offline measurement, not training improvement, registration or model
approval. The lab's exposed internal test is development evidence. A second
generator draw is created only after model/default-threshold selection freezes.
Both draws still share the generator and scenario definitions; neither measures
real-world fraud, AML accuracy, new typologies or independent real customers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import platform
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.behavioral.features import build_features
from app.ml.lab.features import FEATURE_GROUPS, build_matrix
from app.ml.lab.mapping import apply_mapping, map_for_monitoring, read_csv_bytes, suggest
from app.ml.lab.trainer import SEED, best_f1_threshold, chronological_split, point, ranking, score, train_run
from app.scripts.generate_raw_dataset import RawDatasetGenerator
from app.scripts.make_lab_sample import DEFAULT_PATTERNS, build_sample
from app.scripts.raw_contract import GENERATOR_VERSION

VERSION = "current_lab_quality_audit_v1"
BACKEND = Path(__file__).resolve().parents[2]
ROWS = 100_000
MIN_PER_PATTERN = 25
TRAIN_SOURCE_SEED = 2026110401
FINAL_SOURCE_SEED = 2026110402
TRAIN_REFERENCE = date(2026, 9, 30)
FINAL_REFERENCE = date(2027, 3, 31)
TRAIN_SHARE = 0.6
GROUPS = [g["key"] for g in FEATURE_GROUPS]
MODEL_KEYS = ["logistic_regression", "lightgbm", "isolation_forest"]
CODE_PATHS = [
    "app/scripts/audit_current_lab.py", "app/scripts/generate_raw_dataset.py",
    "app/scripts/raw_contract.py", "app/scripts/make_lab_sample.py",
    "app/ml/lab/trainer.py", "app/ml/lab/features.py", "app/ml/lab/mapping.py",
    "app/ml/behavioral/features.py", "app/ml/behavioral/model.py",
    "app/ml/behavioral/standardize.py", "app/services/tabular.py",
]
LIMITATIONS = [
    "This is a new offline fit of the current lab pipeline, not measurement of an ACTIVE database artifact.",
    "Synthetic same-generator/recipe evaluation, not adjudicated real fraud, AML or production accuracy.",
    "Internal chronological test is already exposed by the unchanged lab and is development evidence.",
    "Fresh final is a different stochastic draw in a later simulated period, not unseen fraud typologies or independent real customers.",
    "Sequential account/transaction identifier templates recur across draws; history is never joined by those IDs.",
    "Final features use accepted final-episode history only, as first monitoring upload does; training-study rows are not monitored transactions.",
    "Target is own in-window scenario rows of ATO/P04/P05. Other injected patterns are reference negatives under the existing single-label policy, not proven legitimate transactions.",
    "The lab's original timestamp-tie splitting and all existing production feature/label conventions are measured without repair.",
    "Supervised class-weighted outputs are uncalibrated scores, not validated fraud probabilities.",
    "No application database, model registry, active rule or production model is changed.",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def seal(expected: dict[str, str], path: Path, text: str | None = None) -> None:
    actual = sha256(path)
    wanted = hashlib.sha256(text.encode()).hexdigest() if text is not None else actual
    if actual != wanted:
        raise ValueError(f"Artifact differs from its original expected bytes: {path}")
    key = str(path.resolve())
    if key in expected and expected[key] != wanted:
        raise ValueError(f"Cannot replace an original hash expectation: {path}")
    expected[key] = wanted


def verify_hashes(expected: dict[str, str]) -> None:
    for name, wanted in expected.items():
        if not Path(name).is_file() or sha256(Path(name)) != wanted:
            raise ValueError(f"Protected file changed: {name}")


def write_text(path: Path, text: str, expected: dict[str, str] | None = None) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
    if expected is not None:
        seal(expected, path, text)


def write_json(path: Path, value, expected: dict[str, str] | None = None) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", expected)


def verify_frozen(frozen: dict, manifest: Path, original_manifest_hash: str) -> None:
    verify_hashes(frozen["files"])
    if sha256(manifest) != original_manifest_hash:
        raise ValueError("Frozen manifest changed after selection")


def generate_draw(raw_dir: Path, sample: Path, *, seed: int, reference: date) -> dict:
    if raw_dir.exists() or sample.exists():
        raise FileExistsError("Draw output must be new; old datasets are immutable")
    generator = RawDatasetGenerator(seed=seed, reference_date=reference,
                                    target_transactions=ROWS, min_per_pattern=MIN_PER_PATTERN)
    generator.generate()
    generated = generator.write(raw_dir)
    summary = build_sample(raw_dir, sample)
    manifest = json.loads((raw_dir / "manifest.json").read_text())
    if generated["generator_version"] != GENERATOR_VERSION or len(generator.transactions) != ROWS:
        raise ValueError("Generator changed its declared contract")
    for entry in manifest["files"]:
        if sha256(raw_dir / entry["name"]) != entry["sha256"]:
            raise ValueError("Generated raw file does not match its original manifest")
    return {"generated": generated, "sample": summary}


def fixed_mapping(raw: pd.DataFrame) -> dict:
    proposed = suggest(raw)
    mapping = {key: proposed[key] for key in
               ("fields", "label_column", "positive_values", "category_column", "date_format")}
    if (mapping["label_column"] != "is_fraud" or mapping["positive_values"] != ["1"]
            or mapping["category_column"] != "pattern_code"):
        raise ValueError("Current lab suggestions no longer match the unchanged sample contract")
    mapping["default_currency"] = "XXX"
    check_sample_labels(raw, mapping)
    return mapping


def check_sample_labels(raw: pd.DataFrame, mapping: dict) -> None:
    labels = raw[mapping["label_column"]].astype(str).str.strip()
    if not set(labels.unique()) <= {"", "0", "1"}:
        raise ValueError("Audit sample labels must be literal 0/1 or explicitly unresolved blanks")


def split_diagnostics(frame: pd.DataFrame, parts: np.ndarray) -> dict:
    result = {}
    for left, right in (("train", "validation"), ("validation", "test")):
        earlier, later = frame.loc[parts == left], frame.loc[parts == right]
        boundary = earlier.transaction_date.max()
        shared = set(earlier.transaction_date) & set(later.transaction_date)
        result[f"{left}_to_{right}"] = {
            "earlier_latest": boundary.isoformat(), "later_earliest": later.transaction_date.min().isoformat(),
            "shared_timestamp_values": len(shared),
            "earlier_rows_at_boundary": int(earlier.transaction_date.eq(boundary).sum()),
            "later_rows_at_same_boundary": int(later.transaction_date.eq(boundary).sum()),
        }
    return {"policy": "Unchanged lab lexsort(date, transaction_ref), exact row-count split; ties may cross",
            "boundaries": result}


def choose_defaults(results: dict, models: list) -> dict:
    """Use validation AP and each native validation-max-F1 default, never test."""
    summaries = results["models"]
    best = max(summaries, key=lambda item: item["validation_ranking"]["average_precision"] or -1.0)["key"]
    if best != results["best_model"]:
        raise ValueError("Audit selection differs from the unchanged lab's validation recommendation")
    choices = {}
    for model in models:
        native = float(model.thresholds[model.default_index])
        summary = next(item for item in summaries if item["key"] == model.key)
        if summary["default_index"] != model.default_index:
            raise ValueError("Model default index differs from its validation summary")
        if native != summary["validation_curve"][model.default_index]["threshold"]:
            raise ValueError("Native default threshold differs from validation")
        choices[model.key] = {"default_index": model.default_index, "threshold": native,
                              "thresholds": list(model.thresholds), "features": list(model.features),
                              "artefact_type": model.artefact_type}
    return {"selected_model": best, "selection_rule": "highest validation average precision only",
            "threshold_rule": "native default: maximum validation F1 only", "models": choices,
            "internal_test_used_for_selection": False, "final_used_for_selection": False}


@dataclass
class FinalPopulation:
    frame: pd.DataFrame
    labels: np.ndarray
    categories: pd.Series
    report: dict


def map_final(raw: pd.DataFrame, mapping: dict, currency: str) -> FinalPopulation:
    """Use target-blind monitoring normalization first; targets only annotate evaluation."""
    check_sample_labels(raw, mapping)
    monitored = map_for_monitoring(raw, mapping)
    accepted = monitored.frame
    matching = accepted.currency.eq(currency)
    frame = accepted.loc[matching].reset_index(drop=True)
    positions = frame.source_ordinal.to_numpy(dtype=np.int64) - 1
    values = raw[mapping["label_column"]].astype(str).str.strip().to_numpy()[positions]
    labels = np.where(values == "", np.nan, np.where(values == "1", 1.0, 0.0))
    categories = pd.Series(raw[mapping["category_column"]].astype(str).str.strip().to_numpy()[positions])
    return FinalPopulation(frame, labels, categories, {
        **monitored.report, "model_currency": currency,
        "observations_skipped_other_currency": int((~matching).sum()),
        "observations_scored": len(frame), "known_label_rows": int(np.isfinite(labels).sum()),
        "unresolved_label_rows": int((~np.isfinite(labels)).sum()),
        "positive_rows": int(np.nansum(labels)), "negative_rows": int((labels == 0).sum()),
        "labels_in_feature_frame": False,
    })


def metric_counts(labels: np.ndarray, scores: np.ndarray, threshold: float) -> dict:
    known = np.isfinite(labels)
    y, predictions = labels[known], scores[known]
    if not len(y):
        return {"rows": 0, "positive_rows": 0, "tp": 0, "fp": 0, "fn": 0, "tn": 0,
                "precision": None, "recall": None, "f1": None, "average_precision": None,
                "roc_auc": None, "flagged": 0, "flag_rate": None,
                "unresolved_rows": len(labels)}
    result = point(predictions, y, threshold)
    return {**result, **ranking(predictions, y), "rows": len(y), "positive_rows": int(y.sum()),
            "flag_rate": float(result["flagged"] / len(y)),
            "unresolved_rows": int((~known).sum())}


def scenario_cohorts(raw_dir: Path, refs: pd.Series, accounts: pd.Series) -> pd.Series:
    """Descriptive reference-negative cohorts, never fit/selection inputs."""
    def read(name):
        with (raw_dir / name).open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    owner = {row["source_account_id"]: row["owner_source_id"] for row in read("accounts.csv")}
    labels = read("labels.csv")
    controls = {row["entity_source_id"] for row in labels if row["label_type"] == "CONTROL_CLEAN"}
    by_scenario = {}
    for row in labels:
        by_scenario.setdefault(row["scenario_id"], []).append(row)
    by_ref = {}
    for row in read("label_transactions.csv"):
        by_ref.setdefault(row["source_transaction_reference"], []).extend(by_scenario.get(row["scenario_id"], []))
    cohorts = []
    for ref, account in zip(refs, accounts):
        entity = owner.get(account)
        own = [row for row in by_ref.get(ref, []) if row["entity_source_id"] == entity]
        if entity in controls:
            tag = "control_cohort"
        elif any(row["label_type"] == "INJECTED_POSITIVE" and row["pattern_code"] not in DEFAULT_PATTERNS for row in own):
            tag = "other_pattern_injected_reference_negative"
        elif any(row["label_type"] == "EDGE_CASE" and row["pattern_code"] in DEFAULT_PATTERNS for row in own):
            tag = "fraud_pattern_lookalike_or_boundary"
        elif any(row["label_type"] == "EDGE_CASE" for row in own):
            tag = "other_pattern_edge_case"
        elif any(row["label_type"] == "INJECTED_POSITIVE" and row["pattern_code"] in DEFAULT_PATTERNS for row in own):
            tag = "fraud_pattern_scenario_rows"
        else:
            tag = "other_background_or_unlabelled_rows"
        cohorts.append(tag)
    return pd.Series(cohorts)


def evaluate_fixed(population: FinalPopulation, models: list, vocabulary: dict, selection: dict,
                   amount_quartiles: list[float], cohorts: pd.Series) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """Score fixed text/thresholds on final-episode history, without fitting anything."""
    features = build_features(population.frame, "FRAUD", vocabulary)
    order = pd.Series(np.arange(len(population.frame)), index=population.frame.transaction_ref)
    features = features.set_index(order.loc[features.transaction_ref].to_numpy()).sort_index()
    frame, y = population.frame, population.labels
    amount = frame.amount.to_numpy(dtype=float)
    q25, q50, q75 = amount_quartiles
    slices = {
        "all": np.ones(len(frame), dtype=bool), "small_amount_train_q25": amount <= q25,
        "amount_q1": amount <= q25, "amount_q2": (amount > q25) & (amount <= q50),
        "amount_q3": (amount > q50) & (amount <= q75), "amount_q4": amount > q75,
        "no_baseline": features.NO_BASELINE.to_numpy() == 1,
        "baseline_available": features.NO_BASELINE.to_numpy() == 0,
        "history_under_30_days": features.HISTORY_DAYS.to_numpy() < 30,
        "history_at_least_30_days": features.HISTORY_DAYS.to_numpy() >= 30,
        "no_prior_observations": features.PRIOR_HISTORY_COUNT.to_numpy() == 0,
        "prior_count_1_to_29": (features.PRIOR_HISTORY_COUNT.to_numpy() > 0) & (features.PRIOR_HISTORY_COUNT.to_numpy() < 30),
        "prior_count_at_least_30": features.PRIOR_HISTORY_COUNT.to_numpy() >= 30,
    }
    cached = frame[["transaction_ref", "account_number", "transaction_date", "amount", "currency", "source_ordinal"]].copy()
    cached["label"] = y
    cached["pattern"] = population.categories.to_numpy()
    cached["negative_cohort"] = cohorts.to_numpy()
    for name in ("NO_BASELINE", "HISTORY_DAYS", "PRIOR_HISTORY_COUNT"):
        cached[name] = features[name].to_numpy()
    reports = {}
    candidates = [(model.key, selection["models"][model.key], model) for model in models]
    if "amount_only" in selection.get("diagnostic_baselines", {}):
        candidates.append(("amount_only", selection["diagnostic_baselines"]["amount_only"], None))
    for key, choice, model in candidates:
        threshold = choice["threshold"]
        if model is not None and (float(model.thresholds[model.default_index]) != threshold
                or list(model.features) != choice["features"]):
            raise ValueError("Final model/default differs from frozen selection")
        values = (amount.copy() if model is None else
                  np.asarray(score(model.key, model.model_text, features.loc[:, model.features]), dtype=float))
        if values.shape != (len(frame),) or not np.isfinite(values).all():
            raise ValueError("Final scores must be finite and align with every monitored observation")
        cached[f"score_{key}"] = values
        pattern_recall = {}
        for pattern in DEFAULT_PATTERNS:
            mask = (population.categories.to_numpy() == pattern) & (y == 1)
            count, caught = int(mask.sum()), int((mask & (values >= threshold)).sum())
            pattern_recall[pattern] = {"positive_rows": count, "caught": caught, "missed": count - caught,
                                       "recall": caught / count if count else None}
        negatives = {}
        for cohort in sorted(cohorts.unique()):
            mask = (cohorts.to_numpy() == cohort) & (y == 0)
            count, flagged = int(mask.sum()), int((mask & (values >= threshold)).sum())
            negatives[cohort] = {"reference_negative_rows": count, "flagged_reference_negatives": flagged,
                                 "reference_negative_flag_rate": flagged / count if count else None}
        reports[key] = {"threshold": threshold,
                             "slices": {name: metric_counts(y[mask], values[mask], threshold) for name, mask in slices.items()},
                             "pattern_recall": pattern_recall, "negative_cohorts": negatives}
    return reports, cached, features


def run(out: Path) -> dict:
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    expected: dict[str, str] = {}
    for relative in CODE_PATHS:
        seal(expected, BACKEND / relative)
    software = {name: importlib.import_module(name).__version__ for name in
                ("numpy", "pandas", "pyarrow", "sklearn", "lightgbm")}
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=BACKEND, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    protocol = {"version": VERSION, "state": "DRAFT", "created_at_utc": now(), "git_revision": revision,
                "source_rows_each": ROWS, "min_per_pattern": MIN_PER_PATTERN,
                "evaluation_scope": "fresh offline fit of unchanged current lab; no ACTIVE database artifact is loaded",
                "train_source_seed": TRAIN_SOURCE_SEED, "final_source_seed": FINAL_SOURCE_SEED,
                "train_reference_date": TRAIN_REFERENCE.isoformat(), "final_reference_date": FINAL_REFERENCE.isoformat(),
                "generator_version": GENERATOR_VERSION, "model_seed": SEED, "train_share": TRAIN_SHARE,
                "feature_groups": GROUPS, "model_keys": MODEL_KEYS, "positive_patterns": list(DEFAULT_PATTERNS),
                "diagnostic_baseline": "amount_only: raw amount>=validation-maxF1 cutoff; frozen before fresh final; excluded from lab winner choice",
                "final_features": "accepted final-episode observations only; frozen TRAIN vocabulary; no training-study history",
                "source_independence": "fresh stochastic draw, shared generator/recipes and repeated identifier templates",
                "final_currency": "raw source IDR; no FX", "selection": "highest validation AP, native validation-maxF1 defaults",
                "internal_test_status": "development evidence already exposed by the existing lab; not a sealed final",
                "software": software, "python": platform.python_version(), "limitations": LIMITATIONS}
    write_json(out / "protocol.json", protocol, expected)
    print("Generating new development source; final draw does not exist", flush=True)
    source = generate_draw(out / "source_raw", out / "source.csv", seed=TRAIN_SOURCE_SEED, reference=TRAIN_REFERENCE)
    for path in sorted((out / "source_raw").iterdir()):
        if path.is_file():
            seal(expected, path)
    seal(expected, out / "source.csv")
    write_json(out / "source_summary.json", source, expected)
    raw = read_csv_bytes((out / "source.csv").read_bytes())
    mapping = fixed_mapping(raw)
    write_json(out / "mapping.json", mapping, expected)
    mapped = apply_mapping(raw, mapping)
    if not mapped.report["ready"]:
        raise ValueError("Current lab sample does not meet training requirements")
    write_json(out / "source_validation.json", mapped.report, expected)
    verify_hashes(expected)
    print("Training unchanged current lab: all eight groups and three native candidates", flush=True)
    outcome = train_run(mapped.frame, mapped.labels, mapped.categories,
                        feature_groups=GROUPS, model_keys=MODEL_KEYS, train_share=TRAIN_SHARE, seed=SEED)
    verify_hashes(expected)
    parts = chronological_split(mapped.frame, TRAIN_SHARE)
    _, vocabulary = build_matrix(mapped.frame, parts == "train")
    if vocabulary != outcome.results["features"]["vocabulary"]:
        raise ValueError("Stored preprocessing does not reproduce unchanged TRAIN-only vocabulary")
    quartiles = mapped.frame.loc[parts == "train", "amount"].astype(float).quantile([.25, .5, .75]).tolist()
    if quartiles[0] != vocabulary["small_amount_threshold"]:
        raise ValueError("Small-amount cutoff differs from native TRAIN vocabulary")
    selection = choose_defaults(outcome.results, outcome.models)
    val_mask = parts == "validation"
    val_amounts = mapped.frame.loc[val_mask, "amount"].to_numpy(dtype=float)
    baseline_threshold = best_f1_threshold(val_amounts, mapped.labels[val_mask])
    selection["diagnostic_baselines"] = {"amount_only": {
        "threshold": baseline_threshold, "threshold_rule": "max F1 on validation raw amounts only",
        "features": ["AMOUNT"], "not_eligible_for_lab_winner": True,
        "validation": metric_counts(mapped.labels[val_mask], val_amounts, baseline_threshold),
    }}
    write_json(out / "development_results.json", outcome.results, expected)
    write_json(out / "split_diagnostics.json", split_diagnostics(mapped.frame, parts), expected)
    write_json(out / "preprocessing.json", {"vocabulary": vocabulary, "amount_quartiles_train": quartiles,
                                            "requested_groups": GROUPS, "used_features": outcome.results["features"]["used"]}, expected)
    models_dir = out / "models"
    models_dir.mkdir()
    for model in outcome.models:
        path = models_dir / f"{model.key}.txt"
        write_text(path, model.model_text, expected)
        write_json(models_dir / f"{model.key}_contract.json", selection["models"][model.key], expected)
    write_json(out / "selection.json", selection, expected)
    verify_hashes(expected)
    frozen = {"frozen_at_utc": now(), "files": dict(expected), "final_draw_not_created": True,
              "selection": selection, "final_retuning_forbidden": True}
    write_json(out / "frozen.json", frozen)
    frozen_hash = sha256(out / "frozen.json")
    verify_frozen(frozen, out / "frozen.json", frozen_hash)
    final_started = now()
    print(f"Frozen winner {selection['selected_model']}; only now generating fresh final", flush=True)
    final_source = generate_draw(out / "final_raw", out / "final.csv", seed=FINAL_SOURCE_SEED, reference=FINAL_REFERENCE)
    final_expected: dict[str, str] = {}
    for path in [out / "final.csv", *sorted((out / "final_raw").iterdir())]:
        if path.is_file():
            seal(final_expected, path)
    verify_frozen(frozen, out / "frozen.json", frozen_hash)
    verify_hashes(final_expected)
    final_raw = read_csv_bytes((out / "final.csv").read_bytes())
    frozen_mapping = json.loads((out / "mapping.json").read_text())
    frozen_preprocessing = json.loads((out / "preprocessing.json").read_text())
    frozen_selection = json.loads((out / "selection.json").read_text())
    population = map_final(final_raw, frozen_mapping, frozen_preprocessing["vocabulary"]["currency"])
    cohorts = scenario_cohorts(out / "final_raw", population.frame.transaction_ref, population.frame.account_number)
    final, cached, features = evaluate_fixed(population, outcome.models, frozen_preprocessing["vocabulary"],
                                            frozen_selection, frozen_preprocessing["amount_quartiles_train"], cohorts)
    verify_hashes(final_expected)
    cached.to_parquet(out / "final_scores.parquet", index=False)
    seal(final_expected, out / "final_scores.parquet")
    features.to_parquet(out / "final_features.parquet", index=False)
    seal(final_expected, out / "final_features.parquet")
    write_json(out / "final_source_summary.json", final_source, final_expected)
    write_json(out / "final_validation.json", population.report, final_expected)
    verify_frozen(frozen, out / "frozen.json", frozen_hash)
    verify_hashes(final_expected)
    shared_accounts = set(mapped.frame.account_number) & set(population.frame.account_number)
    report = {"version": VERSION, "state": "DRAFT", "completed_at_utc": now(), "protocol": protocol,
              "source_validation": mapped.report, "development_results": outcome.results,
              "split_diagnostics": split_diagnostics(mapped.frame, parts), "selection": selection,
              "final_validation": population.report, "final": final,
              "amount_quartiles_train": quartiles, "selected_final": final[selection["selected_model"]],
              "shared_account_identifier_tokens": len(shared_accounts),
              "shared_identifier_tokens_are_not_proof_of_customer_identity": True,
              "frozen": frozen, "frozen_manifest_sha256": frozen_hash,
              "final_generation_started_at_utc": final_started,
              "freeze_precedes_final_generation": frozen["frozen_at_utc"] < final_started,
              "final_retuned": False, "final_artifact_hashes": dict(final_expected),
              "database_registration_or_activation": False, "limitations": LIMITATIONS}
    write_json(out / "report.json", report)
    print(json.dumps({"report": str(out / "report.json"), "selected": selection["selected_model"],
                      "final": {key: result["slices"]["all"] for key, result in final.items()}}, indent=2), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="NEW isolated ignored output root")
    args = parser.parse_args()
    run(args.out)


if __name__ == "__main__":
    main()
