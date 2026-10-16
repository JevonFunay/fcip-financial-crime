"""Controlled offline retraining; unsupervised fits, label-informed selection.

Never registers/promotes models or connects to the application database.
All candidate fits precede synthetic validation-label access. A declared
selection rule is frozen before a new simulated final draw is generated.
The original heldout source parents/recipes are known from prior research:
the new draw is not independent real-world crime validation.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import platform
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .features import fit_vocabulary
from .generate import generate, source_partitions
from .model import EXPLANATION_METHOD, fit_model, predict_model
from .pipeline import CAVEAT, _evaluation_labels, _metrics, distribution, load_observations, sha256, write_json
from .research_features import (REPRESENTATION_VERSION, build_experiment_features,
                                feature_definitions, feature_names)

UNITS = ("AML", "FRAUD")
DEFAULT_FINAL_SEED = 2026101601
RECALL_RETENTION = 0.80  # Research comparison choice, not an approved operating point.
REFERENCE = "mixed_full_256__auto"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(**value) -> None:
    print(json.dumps(value, default=str), flush=True)


def _json_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def verify_frozen(frozen: dict) -> None:
    for path, expected in frozen["files"].items():
        if not Path(path).is_file() or sha256(Path(path)) != expected:
            raise ValueError(f"Frozen experiment input/artefact changed: {path}")


def _verify_previous(previous_run: Path, manifests: dict, *, include_answers: bool = False) -> None:
    for split, manifest in manifests.items():
        names = ["transactions.csv", "lineage.csv"]
        if split == "validation" and include_answers:
            names.append("ground_truth.csv")
        for name in names:
            if sha256(previous_run / split / name) != manifest["files"][name]["sha256"]:
                raise ValueError(f"Previous {split}/{name} differs from its manifest")
        if sha256(previous_run / split / "fit_profile.json") != manifest["fit_profile_sha256"]:
            raise ValueError(f"Previous {split} fit profile differs from its manifest")


def _candidate_specs(samples: tuple[int, ...]) -> list[dict]:
    specs = [{"name": f"{origin}_full_256", "origin": origin,
              "representation": "full", "max_samples": 256} for origin in ("source_only", "mixed")]
    specs += [{"name": f"{origin}_portable_{count}", "origin": origin,
               "representation": "portable", "max_samples": count}
              for origin in ("source_only", "mixed", "balanced_origin") for count in samples]
    return specs


def _fit_candidates(source_train: pd.DataFrame, generated_train: pd.DataFrame,
                    models_out: Path, protocol: dict) -> dict:
    """No target, validation frame, evaluation sidecar or test input accepted."""
    combined = pd.concat([source_train, generated_train], ignore_index=True)
    source_accounts = set(source_train.account_number)
    rng = np.random.default_rng(protocol["seed"])
    count = min(len(source_train), len(generated_train))
    balanced_reference = pd.concat([source_train.iloc[rng.choice(len(source_train), count, replace=False)],
                                   generated_train.iloc[rng.choice(len(generated_train), count, replace=False)]],
                                  ignore_index=True)
    references = {"source_only": source_train, "mixed": combined, "balanced_origin": balanced_reference}
    vocabularies = {name: fit_vocabulary(frame) for name, frame in references.items()}
    cards, cache = {}, {}
    for spec in protocol["candidate_specs"]:
        origin, representation = spec["origin"], spec["representation"]
        observations = source_train if origin == "source_only" else combined
        vocabulary = vocabularies[origin]
        for unit in UNITS:
            key = (origin, representation, unit)
            if key not in cache:
                frame = build_experiment_features(observations, unit, vocabulary, representation)
                if origin == "balanced_origin":
                    source_mask = frame.account_number.isin(source_accounts).to_numpy()
                    source_indices, generated_indices = np.flatnonzero(source_mask), np.flatnonzero(~source_mask)
                    n = min(len(source_indices), len(generated_indices))
                    # Sample feature rows AFTER building complete histories.
                    indices = np.sort(np.r_[rng.choice(source_indices, n, replace=False),
                                            rng.choice(generated_indices, n, replace=False)])
                    frame = frame.iloc[indices].reset_index(drop=True)
                cache[key] = frame
            frame = cache[key]
            names = feature_names(unit, representation)
            started = time.perf_counter()
            text, train_scores = fit_model(frame, names, seed=protocol["seed"],
                                           estimators=protocol["estimators"], max_samples=spec["max_samples"])
            folder = models_out / spec["name"] / unit.lower()
            folder.mkdir(parents=True)
            (folder / "model.txt").write_text(text, encoding="utf-8")
            thresholds = {"auto": 0.5}
            if representation == "portable":
                thresholds.update({f"train_q{q:g}": float(np.quantile(train_scores, q))
                                   for q in protocol["threshold_quantiles"]})
            source_units = int(frame.account_number.isin(source_accounts).sum())
            card = {"state": "DRAFT", "registration_compatible": False,
                    "version": f"research_{protocol['run_id']}_{spec['name']}_{unit.lower()}",
                    "unit": unit, "origin": origin, "representation": representation,
                    "representation_version": REPRESENTATION_VERSION, "trained_at": _now(),
                    "training_kind": "unsupervised", "targets_used": False,
                    "feature_names": names, "feature_definitions": feature_definitions(unit, representation),
                    "vocabulary": vocabulary, "fit_units": len(frame),
                    "source_fit_units": source_units, "generated_fit_units": len(frame) - source_units,
                    "source_input_observations": len(source_train),
                    "generated_history_observations": 0 if origin == "source_only" else len(generated_train),
                    "hyperparameters": {"n_estimators": protocol["estimators"],
                                        "max_samples": spec["max_samples"], "seed": protocol["seed"]},
                    "threshold_candidates": thresholds, "model_sha256": sha256(folder / "model.txt"),
                    "score_kind": "anomaly_score", "explanation_method": EXPLANATION_METHOD,
                    "training_review_load": {name: distribution(train_scores, threshold)
                                             for name, threshold in thresholds.items()},
                    "caveat": CAVEAT}
            card["definition_hash"] = _json_hash({"model_sha256": card["model_sha256"], "features": names,
                                                  "vocabulary": vocabulary, "representation": representation,
                                                  "version": REPRESENTATION_VERSION, "thresholds": thresholds})
            write_json(folder / "model_card.json", card)
            cards[(spec["name"], unit)] = {"folder": folder, "card": card}
            _log(phase="candidate_fit", candidate=spec["name"], unit=unit, fit_units=len(frame),
                 source_fit_units=source_units, targets_used=False, seconds=round(time.perf_counter() - started, 2))
            del train_scores
        # Cache only the current data/view (two units); score/model caches are bounded.
        for key in list(cache):
            if key[:2] != (origin, representation):
                del cache[key]
        gc.collect()
    return cards


def _metrics_with_f1(labels, scores, threshold) -> dict:
    metrics = _metrics(labels, scores, threshold)
    denominator = 2 * metrics["tp"] + metrics["fp"] + metrics["fn"]
    metrics["f1"] = 2 * metrics["tp"] / denominator if denominator else 0.0
    return metrics


def _evaluate_predictions(observations: pd.DataFrame, features: pd.DataFrame, scores: np.ndarray,
                          unit: str, threshold: float, truth: pd.DataFrame | None, small_cutoff: float) -> dict:
    result = distribution(scores, threshold)
    if truth is None:
        result.update(precision=None, recall=None, average_precision=None, roc_auc=None,
                      reason="No verified target labels; flags measure review load only.")
        return result
    labels = _evaluation_labels(observations, features, unit, truth)
    small = pd.to_numeric(labels.amount).to_numpy(dtype=float) <= small_cutoff
    result["synthetic_stress"] = _metrics_with_f1(labels.label.to_numpy(), scores, threshold)
    result["small_amount"] = _metrics_with_f1(labels.label.to_numpy()[small], scores[small], threshold)
    result["small_amount"]["cutoff"] = small_cutoff
    return result


def evaluate_candidates(cards: dict, validation: pd.DataFrame, source_validation: pd.DataFrame,
                        truth_path: Path, small_cutoff: float, out: Path) -> dict:
    """All models already exist; only here are validation answer files opened."""
    truth = pd.read_csv(truth_path)
    results = {unit: {} for unit in UNITS}
    out.mkdir(parents=True)
    for (name, unit), entry in cards.items():
        card, folder = entry["card"], entry["folder"]
        text = (folder / "model.txt").read_text()
        features = build_experiment_features(validation, unit, card["vocabulary"], card["representation"])
        scores = predict_model(text, features)
        source_features = build_experiment_features(source_validation, unit, card["vocabulary"], card["representation"])
        source_scores = predict_model(text, source_features)
        for threshold_name, threshold in card["threshold_candidates"].items():
            identifier = f"{name}__{threshold_name}"
            results[unit][identifier] = {
                "candidate": name, "threshold_name": threshold_name, "threshold": threshold,
                "origin": card["origin"], "representation": card["representation"],
                "synthetic": _evaluate_predictions(validation, features, scores, unit, threshold, truth, small_cutoff),
                "source_unlabelled": _evaluate_predictions(source_validation, source_features, source_scores,
                                                            unit, threshold, None, small_cutoff)}
        features[["account_number", "transaction_ref", "as_of"]].assign(score=scores).to_parquet(
            out / f"{name}_{unit.lower()}.parquet", index=False)
        _log(phase="validation_scored", candidate=name, unit=unit)
        del features, source_features, scores, source_scores
        gc.collect()
    return results


def select_candidates(validation_results: dict, protocol: dict) -> dict:
    selected = {}
    retention = protocol["selection"]["minimum_reference_tp_retention"]
    for unit, candidates in validation_results.items():
        reference = candidates[REFERENCE]
        base = reference["synthetic"]["synthetic_stress"]
        base_small = reference["synthetic"]["small_amount"]
        minimum_tp, minimum_small_tp = math.ceil(retention * base["tp"]), math.ceil(retention * base_small["tp"])
        eligible = []
        exclusions = {}
        for identifier, result in candidates.items():
            metrics, small = result["synthetic"]["synthetic_stress"], result["synthetic"]["small_amount"]
            reasons = []
            if metrics["tp"] < minimum_tp:
                reasons.append("insufficient total stress coverage versus reference")
            if small["tp"] < minimum_small_tp:
                reasons.append("insufficient small-amount stress coverage versus reference")
            if result["source_unlabelled"]["units_flagged"] > reference["source_unlabelled"]["units_flagged"]:
                reasons.append("increased unlabelled-source review load")
            if reasons:
                exclusions[identifier] = reasons
            else:
                eligible.append(identifier)
        if not eligible:
            raise ValueError("Reference must remain eligible; invalid selection inputs")
        winner = sorted(eligible, key=lambda identifier: (
            -candidates[identifier]["synthetic"]["synthetic_stress"]["f1"],
            -(candidates[identifier]["synthetic"]["synthetic_stress"]["average_precision"] or 0.0),
            candidates[identifier]["source_unlabelled"]["units_flagged"], identifier))[0]
        selected[unit] = {"selected": winner, "reference": REFERENCE,
                          "selected_configuration": candidates[winner],
                          "minimum_total_tp": minimum_tp, "minimum_small_amount_tp": minimum_small_tp,
                          "eligible": eligible, "excluded": exclusions}
    return selected


def _parents_for_features(features: pd.DataFrame, lineage_path: Path) -> np.ndarray:
    lineage = pd.read_csv(lineage_path, usecols=["account_number", "source_account_id"])
    if lineage.groupby("account_number").source_account_id.nunique().max() != 1:
        raise ValueError("Generated account has ambiguous source ancestry")
    mapping = lineage.drop_duplicates("account_number").set_index("account_number").source_account_id
    parents = features.account_number.map(mapping)
    if parents.isna().any():
        raise ValueError("Missing source ancestry for confidence calculation")
    return parents.to_numpy()


def clustered_intervals(labels, selected_flags, reference_flags, parents, *, replicates: int, seed: int) -> dict:
    """Paired source-parent bootstrap; clones/rows are never independent units."""
    if replicates <= 0:
        return {"replicates": 0, "reason": "Disabled explicitly"}
    labels = np.asarray(labels, dtype=bool)
    parents, codes = np.unique(np.asarray(parents), return_inverse=True)
    counts = []
    for flags in (selected_flags, reference_flags):
        flags = np.asarray(flags, dtype=bool)
        counts.append(np.column_stack([np.bincount(codes, weights=mask, minlength=len(parents))
                                       for mask in (flags & labels, flags & ~labels, ~flags & labels)]))
    rng = np.random.default_rng(seed)
    values = {metric: {"selected": [], "reference": [], "paired_delta": []}
              for metric in ("precision", "recall", "f1")}
    for _ in range(replicates):
        index = rng.integers(0, len(parents), len(parents))
        scores = []
        for group in counts:
            tp, fp, fn = group[index].sum(axis=0)
            scores.append({"precision": tp / (tp + fp) if tp + fp else np.nan,
                           "recall": tp / (tp + fn) if tp + fn else np.nan,
                           "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else np.nan})
        for metric in values:
            for name, value in (("selected", scores[0][metric]), ("reference", scores[1][metric]),
                                ("paired_delta", scores[0][metric] - scores[1][metric])):
                if np.isfinite(value):
                    values[metric][name].append(float(value))
    return {"cluster": "original source parent account", "clusters": len(parents),
            "replicates": replicates, "confidence": 0.95,
            "intervals": {metric: {name: {"low": float(np.quantile(values, .025)) if values else None,
                                          "high": float(np.quantile(values, .975)) if values else None,
                                          "valid_replicates": len(values)} for name, values in entries.items()}
                          for metric, entries in values.items()}}


def evaluate_final(cards: dict, selection: dict, final: pd.DataFrame, truth_path: Path, lineage_path: Path,
                   small_cutoff: float, out: Path, *, replicates: int, seed: int) -> dict:
    truth = pd.read_csv(truth_path)
    results = {}
    out.mkdir(parents=True)
    for unit in UNITS:
        chosen = selection[unit]
        comparisons, arrays = {}, {}
        for label, identifier in (("selected", chosen["selected"]), ("reference", REFERENCE)):
            name, threshold_name = identifier.split("__")
            entry = cards[(name, unit)]
            card, folder = entry["card"], entry["folder"]
            features = build_experiment_features(final, unit, card["vocabulary"], card["representation"])
            scores = predict_model((folder / "model.txt").read_text(), features)
            threshold = card["threshold_candidates"][threshold_name]
            comparisons[label] = {"candidate": identifier,
                                  **_evaluate_predictions(final, features, scores, unit, threshold, truth, small_cutoff)}
            labels = _evaluation_labels(final, features, unit, truth)
            arrays[label] = (labels, scores >= threshold, _parents_for_features(features, lineage_path))
            features[["account_number", "transaction_ref", "as_of"]].assign(score=scores,
                                                                               flagged=scores >= threshold).to_parquet(
                out / f"{unit.lower()}_{label}_scores.parquet", index=False)
            del scores, features
        labels, flags, parents = arrays["selected"]
        reference_labels, reference_flags, reference_parents = arrays["reference"]
        keys = ["transaction_ref"] if unit == "FRAUD" else ["account_number", "as_of"]
        if not labels[keys].equals(reference_labels[keys]) or not np.array_equal(parents, reference_parents):
            raise ValueError("Final paired comparison rows are not aligned")
        comparisons["paired_uncertainty"] = clustered_intervals(labels.label, flags, reference_flags, parents,
                                                                 replicates=replicates, seed=seed)
        results[unit] = comparisons
        _log(phase="fresh_final_evaluated", unit=unit, selected=chosen["selected"],
             selected_metrics=comparisons["selected"]["synthetic_stress"],
             reference_metrics=comparisons["reference"]["synthetic_stress"])
        gc.collect()
    return results


def run(source: Path, previous_run: Path, out: Path, models_out: Path, *, test_rows: int = 200000,
        seed: int = 20261015, final_seed: int = DEFAULT_FINAL_SEED, reference_date: date = date(2026, 10, 16),
        estimators: int = 100, max_samples_options: tuple[int, ...] = (256, 2048),
        threshold_quantiles: tuple[float, ...] = (.95, .97, .99, .995), bootstrap_replicates: int = 300) -> dict:
    if out.exists() or models_out.exists():
        raise FileExistsError("Choose new experiment/model roots; existing outputs are immutable")
    old_train = json.loads((previous_run / "train" / "manifest.json").read_text())
    old_validation = json.loads((previous_run / "validation" / "manifest.json").read_text())
    known_seeds = {old_train["seed"], old_validation["seed"], old_train["seed"] + 2}
    if final_seed in known_seeds:
        raise ValueError("Final draw seed must differ from all known previous split seeds")
    if (not max_samples_options or len(set(max_samples_options)) != len(max_samples_options)
            or any(type(x) is not int or not 2 <= x <= 1000000 for x in max_samples_options)
            or any(not 0 < q < 1 for q in threshold_quantiles)
            or not 1 <= estimators <= 512 or bootstrap_replicates < 0 or test_rows < 1000
            or type(seed) is not int or not 0 <= seed < 2**32
            or type(final_seed) is not int or not 0 <= final_seed < 2**32):
        raise ValueError("Invalid declared experiment parameters")
    partitions = source_partitions(source)
    protected_paths = [source, previous_run / "train" / "transactions.csv", previous_run / "train" / "fit_profile.json",
                       previous_run / "train" / "manifest.json", previous_run / "validation" / "transactions.csv",
                       previous_run / "validation" / "ground_truth.csv", previous_run / "validation" / "manifest.json",
                       previous_run / "validation" / "fit_profile.json",
                       previous_run / "train" / "lineage.csv", previous_run / "validation" / "lineage.csv"]
    if sha256(source) != old_train["source_sha256"] or sha256(source) != old_validation["source_sha256"]:
        raise ValueError("Source checksum differs from immutable previous generation")
    _verify_previous(previous_run, {"train": old_train, "validation": old_validation})
    out.mkdir(parents=True)
    models_out.mkdir(parents=True)
    protocol = {"schema_version": 1, "run_id": out.name, "started_at": _now(), "seed": seed,
                "final_seed": final_seed, "reference_date": reference_date.isoformat(), "test_rows": test_rows,
                "estimators": estimators, "candidate_specs": _candidate_specs(max_samples_options),
                "library_versions": {"python": platform.python_version(), "numpy": np.__version__,
                                     "pandas": pd.__version__, "sklearn": __import__("sklearn").__version__},
                "threshold_quantiles": list(threshold_quantiles), "bootstrap_replicates": bootstrap_replicates,
                "selection": {"reference": REFERENCE, "minimum_reference_tp_retention": RECALL_RETENTION,
                              "small_amount_definition": "Original source TRAIN amount q25, identical across all candidate evaluations",
                              "source_review_load": "must not exceed mixed full auto reference",
                              "rank": ["maximum synthetic validation F1", "maximum average precision",
                                       "minimum unlabelled-source flagged count", "lexical identifier"],
                              "status": "Research comparison rule; no production quality/approval claim"},
                "fit_uses_targets": False, "selection_uses_synthetic_validation_labels": True,
                "source_partitions": {key: {"accounts": len(value), "ids_sha256": _json_hash(value)}
                                      for key, value in partitions.items()},
                # The validation-answer digest comes from its manifest. Do not
                # open answer bytes (even for hashing) until all fits finish.
                "protected_input_checksums": {str(path.resolve()):
                                              (old_validation["files"]["ground_truth.csv"]["sha256"]
                                               if path == previous_run / "validation" / "ground_truth.csv" else sha256(path))
                                              for path in protected_paths},
                "caveat": CAVEAT,
                "independence": "Fresh stochastic test draw; source test parents/recipes are known from prior research. Not independent real-crime validation.",
                "serving": "Offline research only. No database registration, promotion or CF-05 activation."}
    code_paths = [Path(__file__), Path(__file__).with_name("research_features.py"),
                  Path(__file__).with_name("features.py"), Path(__file__).with_name("generate.py"),
                  Path(__file__).with_name("model.py"), Path(__file__).with_name("standardize.py"),
                  Path(__file__).with_name("pipeline.py")]
    protocol["code_checksums"] = {str(path.resolve()): sha256(path) for path in code_paths}
    write_json(out / "protocol.json", protocol)
    _log(phase="protocol_persisted", protocol_sha256=sha256(out / "protocol.json"))
    original, source_profile = load_observations(source, "kaggle_bank_v1")
    source_train = original[original.account_number.isin(partitions["train"])].copy()
    source_validation = original[original.account_number.isin(partitions["validation"])].copy()
    del original
    generated_train, _ = load_observations(previous_run / "train" / "transactions.csv")
    validation, _ = load_observations(previous_run / "validation" / "transactions.csv")
    small_cutoff = fit_vocabulary(source_train)["small_amount_threshold"]
    cards = _fit_candidates(source_train, generated_train, models_out, protocol)
    del generated_train
    gc.collect()
    verify_frozen({"files": {**protocol["protected_input_checksums"], **protocol["code_checksums"]}})
    _verify_previous(previous_run, {"train": old_train, "validation": old_validation}, include_answers=True)
    validation_results = evaluate_candidates(cards, validation, source_validation,
                                             previous_run / "validation" / "ground_truth.csv", small_cutoff,
                                             out / "validation_scores")
    selection = select_candidates(validation_results, protocol)
    verify_frozen({"files": {**protocol["protected_input_checksums"], **protocol["code_checksums"]}})
    selection_record = {"selected_at": _now(), "selection_uses_synthetic_validation_labels": True,
                        "small_amount_cutoff": small_cutoff, "small_amount_reference": "Original source TRAIN amount q25, fixed across every candidate",
                        "units": selection}
    write_json(out / "validation.json", validation_results)
    write_json(out / "selection.json", selection_record)
    all_paths = [out / "protocol.json", out / "selection.json", out / "validation.json", *protected_paths, *code_paths]
    all_paths += [entry["folder"] / name for entry in cards.values() for name in ("model.txt", "model_card.json")]
    frozen = {"models_frozen_at": _now(), "files": {str(path.resolve()): sha256(path) for path in all_paths}}
    write_json(out / "frozen.json", frozen)
    verify_frozen(frozen)
    _log(phase="selection_frozen", selections={unit: result["selected"] for unit, result in selection.items()})
    final_started_at = _now()
    generate(source, out / "final_test", "test", test_rows, final_seed,
             fit_profile=previous_run / "train" / "fit_profile.json", reference_date=reference_date)
    verify_frozen(frozen)
    final, final_normalization = load_observations(out / "final_test" / "transactions.csv")
    final_results = evaluate_final(cards, selection, final, out / "final_test" / "ground_truth.csv",
                                   out / "final_test" / "lineage.csv", small_cutoff, out / "final_scores",
                                   replicates=bootstrap_replicates, seed=final_seed)
    verify_frozen(frozen)
    report = {"schema_version": 1, "completed_at": _now(), "state": "DRAFT", "caveat": CAVEAT,
              "fit_uses_targets": False, "selection_uses_synthetic_validation_labels": True,
              "test_generated_after_selection_and_freeze": True,
              "models_frozen_at": frozen["models_frozen_at"], "test_generation_started_at": final_started_at,
              "final_seed": final_seed, "final_test_rows": len(final),
              "final_manifest_sha256": sha256(out / "final_test" / "manifest.json"),
              "final_normalization": final_normalization, "source_profile": source_profile,
              "source_training_rows": len(source_train), "source_validation_rows": len(source_validation),
              "generated_training_rows": old_train["requested_rows"], "generated_validation_rows": len(validation),
              "small_amount_cutoff": small_cutoff, "selection": selection,
              "validation": validation_results, "fresh_final": final_results,
              "frozen_artefact_checksums": frozen, "protocol_sha256": sha256(out / "protocol.json"),
              "no_database_registration_or_promotion": True, "independence": protocol["independence"]}
    write_json(out / "report.json", report)
    _log(phase="complete", report=str(out / "report.json"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--previous-run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--models-out", type=Path, required=True)
    parser.add_argument("--test-rows", type=int, default=200000)
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--final-seed", type=int, default=DEFAULT_FINAL_SEED)
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date(2026, 10, 16))
    parser.add_argument("--estimators", type=int, default=100)
    parser.add_argument("--max-samples", type=int, nargs="+", default=[256, 2048])
    parser.add_argument("--bootstrap-replicates", type=int, default=300)
    args = parser.parse_args()
    run(args.source, args.previous_run, args.out, args.models_out, test_rows=args.test_rows,
        seed=args.seed, final_seed=args.final_seed, reference_date=args.reference_date,
        estimators=args.estimators, max_samples_options=tuple(args.max_samples),
        bootstrap_replicates=args.bootstrap_replicates)


if __name__ == "__main__":
    main()
