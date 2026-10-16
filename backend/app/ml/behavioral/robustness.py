"""Offline source-aligned experiments with recall-preserving grouped selection.

Fits never accept targets. Synthetic labels are used only after all fits for
explicitly label-informed validation selection. A fresh simulated final draw is
generated only after configuration, inputs and models have been frozen. This
module has no application database, registration or promotion path.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd

from .experiments import (_log, _metrics_with_f1, _now,
                          _parents_for_features, clustered_intervals, verify_frozen)
from .features import fit_vocabulary
from .generate import source_partitions
from .model import fit_model, predict_model
from .pipeline import CAVEAT, _evaluation_labels, distribution, load_observations, sha256, write_json
from .research_features import build_experiment_features, feature_definitions

UNITS = ("AML", "FRAUD")
REFERENCE = "mixed_full_256__auto"
FINAL_SEED = 2026101602
META = ["account_number", "transaction_ref", "as_of"]


def parent_groups(parents: list[str], seed: int, groups: int = 3) -> dict[str, int]:
    """A source parent and ALL its generated descendants stay in one group."""
    parents = sorted(set(parents))
    if len(parents) < groups or groups < 2:
        raise ValueError("Grouped validation needs at least two nonempty parent groups")
    order = np.random.default_rng(seed).permutation(parents)
    return {str(parent): i for i, part in enumerate(np.array_split(order, groups)) for parent in part}


def strict_select(results: dict) -> dict:
    """Higher precision never compensates for losing reference true positives.

    Overall, low-amount, every heldout parent group and every stress recipe
    must each retain at least the reference TP count. The same units/labels
    make this a recall comparison. Source labels do not exist, so their flags
    are constrained only as review workload. Reference is always a fallback.
    """
    selected = {}
    for unit, candidates in results.items():
        reference = candidates[REFERENCE]
        eligible, excluded = [], {}
        for name, candidate in candidates.items():
            failures = []
            for slice_name, metric in reference["slices"].items():
                current = candidate["slices"].get(slice_name)
                if current is None or current["tp"] < metric["tp"]:
                    failures.append(f"reference TP coverage lost in {slice_name}")
            if candidate["slices"]["all"]["fp"] > reference["slices"]["all"]["fp"]:
                failures.append("more overall false positives than reference")
            if candidate["source_unlabelled"]["units_flagged"] > reference["source_unlabelled"]["units_flagged"]:
                failures.append("more unlabelled-source review flags than reference")
            if failures:
                excluded[name] = failures
            else:
                eligible.append(name)
        if REFERENCE not in eligible:
            raise ValueError("Reference must be eligible for its own comparison")
        winner = min(eligible, key=lambda name: (
            -(candidates[name]["slices"]["all"]["precision"] or 0.0),
            -(candidates[name]["slices"]["all"]["average_precision"] or 0.0),
            candidates[name]["source_unlabelled"]["units_flagged"], name))
        selected[unit] = {"selected": winner, "reference": REFERENCE,
                          "eligible": eligible, "excluded": excluded,
                          "improvement_found": winner != REFERENCE,
                          "selected_configuration": candidates[winner]}
    return selected


def _labels(observations, features, unit, truth, lineage, cutoff, groups=None):
    labels = _evaluation_labels(observations, features, unit, truth)
    labels["parent"] = _parents_for_features(features, lineage)
    labels["small"] = pd.to_numeric(labels.amount).to_numpy(dtype=float) <= cutoff
    known = observations[["transaction_ref", "account_number", "transaction_date"]].merge(
        truth.drop(columns=["account_number"], errors="ignore"), on="transaction_ref", validate="one_to_one")
    if unit == "FRAUD":
        side = features[["transaction_ref"]].merge(known, on="transaction_ref", validate="one_to_one")
        for recipe in sorted(truth.loc[truth.label.eq(1), "scenario"].unique()):
            labels[f"recipe:{recipe}"] = (side.label.eq(1) & side.scenario.eq(recipe)).to_numpy()
        if "intraday_observable" in side:
            labels["intraday_observable"] = side.intraday_observable.to_numpy(dtype=bool)
    else:
        timestamp = pd.to_datetime(known.transaction_date, utc=True)
        known["as_of"] = timestamp.dt.floor("D") - pd.to_timedelta(timestamp.dt.dayofweek, unit="D") + pd.Timedelta(days=7) - pd.Timedelta(seconds=1)
        for recipe in sorted(truth.loc[truth.label.eq(1), "scenario"].unique()):
            column = f"recipe:{recipe}"
            known[column] = known.label.eq(1) & known.scenario.eq(recipe)
            weekly = known.groupby(["account_number", "as_of"], as_index=False)[column].max()
            labels[column] = features[["account_number", "as_of"]].merge(
                weekly, on=["account_number", "as_of"], validate="one_to_one")[column].to_numpy()
    if groups is not None:
        labels["group"] = labels.parent.map(groups)
        if labels.group.isna().any():
            raise ValueError("A validation descendant has no declared source-parent group")
    return labels


def _slices(labels, scores, threshold):
    masks = {"all": np.ones(len(labels), dtype=bool), "small": labels.small.to_numpy()}
    if "group" in labels:
        for group in sorted(labels.group.unique()):
            masks[f"parent_group:{group}"] = labels.group.eq(group).to_numpy()
            masks[f"small_parent_group:{group}"] = (labels.group.eq(group) & labels.small).to_numpy()
    for column in labels:
        if column.startswith("recipe:"):
            # Recipe recall only; do not duplicate normal account-weeks across
            # transactions or assign normal observations an invented recipe.
            masks[column] = labels[column].to_numpy(dtype=bool)
    if "intraday_observable" in labels:
        masks["day_only"] = ~labels.intraday_observable.to_numpy()
        masks["intraday_observable"] = labels.intraday_observable.to_numpy()
    return {name: _metrics_with_f1(labels.label.to_numpy()[mask], scores[mask], threshold)
            for name, mask in masks.items()}


def _view(observations, unit, vocabulary, representation, cache):
    # Reuse portable inputs and add the registered AML prior-gap observation.
    base = "portable" if representation == "relative" else representation
    if base not in cache:
        cache[base] = build_experiment_features(observations, unit, vocabulary, base)
    frame = cache[base]
    if representation == "relative":
        from .robustness_features import build_relative
        if "relative" not in cache:
            cache["relative"] = build_relative(observations, unit, vocabulary, frame)
        return cache["relative"]
    return frame


def fit_candidates(source_train, generated_train, models_out, protocol):
    """There is deliberately no label/validation argument to estimator fitting."""
    combined = pd.concat([source_train, generated_train], ignore_index=True)
    vocabulary = fit_vocabulary(combined)
    cards = {}
    for unit in UNITS:
        cache = {}
        for spec in protocol["candidate_specs"]:
            frame = _view(combined, unit, vocabulary, spec["representation"], cache)
            names = [name for name in frame if name not in META]
            text, scores = fit_model(frame, names, seed=protocol["seed"], estimators=protocol["estimators"],
                                     max_samples=spec["max_samples"])
            thresholds = {"auto": .5}
            if spec["representation"] != "full":
                thresholds.update({f"train_q{q:g}": float(np.quantile(scores, q)) for q in protocol["threshold_quantiles"]})
            folder = models_out / spec["name"] / unit.lower()
            folder.mkdir(parents=True)
            (folder / "model.txt").write_text(text, encoding="utf-8")
            if spec["representation"] == "relative":
                from .robustness_features import feature_definitions as relative_definitions
                definitions = relative_definitions(unit)
            else:
                definitions = feature_definitions(unit, spec["representation"])
            card = {"state": "DRAFT", "registration_compatible": False, "unit": unit,
                    "training_kind": "unsupervised", "targets_used": False, "fit_units": len(frame),
                    "source_training_observations": len(source_train), "generated_training_observations": len(generated_train),
                    "specification": spec, "vocabulary": vocabulary, "feature_names": names,
                    "feature_definitions": {name: definitions[name] for name in names},
                    "threshold_candidates": thresholds, "model_sha256": sha256(folder / "model.txt"),
                    "training_review_load": {name: distribution(scores, threshold) for name, threshold in thresholds.items()},
                    "caveat": CAVEAT}
            write_json(folder / "model_card.json", card)
            cards[(spec["name"], unit)] = {"folder": folder, "card": card,
                                           "card_sha256": sha256(folder / "model_card.json")}
            _log(phase="candidate_fit", candidate=spec["name"], unit=unit, fit_units=len(frame), targets_used=False)
        del cache
        gc.collect()
    return cards


def evaluate_validation(cards, validation, source_validation, truth_path, lineage, cutoff, groups, out):
    truth = pd.read_csv(truth_path)  # Every candidate has already been fitted.
    results = {unit: {} for unit in UNITS}
    out.mkdir(parents=True)
    for unit in UNITS:
        cache, source_cache = {}, {}
        for (name, card_unit), entry in cards.items():
            if card_unit != unit:
                continue
            card = entry["card"]
            view = card["specification"]["representation"]
            frame = _view(validation, unit, card["vocabulary"], view, cache)
            source_frame = _view(source_validation, unit, card["vocabulary"], view, source_cache)
            text = (entry["folder"] / "model.txt").read_text()
            scores, source_scores = predict_model(text, frame), predict_model(text, source_frame)
            labels = _labels(validation, frame, unit, truth, lineage, cutoff, groups)
            frame[META].assign(score=scores).to_parquet(out / f"{name}_{unit.lower()}.parquet", index=False)
            for threshold_name, threshold in card["threshold_candidates"].items():
                results[unit][f"{name}__{threshold_name}"] = {
                    "candidate": name, "threshold_name": threshold_name, "threshold": threshold,
                    "slices": _slices(labels, scores, threshold),
                    "source_unlabelled": {**distribution(source_scores, threshold), "precision": None, "recall": None,
                                          "reason": "No verified source targets; review workload only."}}
            _log(phase="grouped_validation_scored", candidate=name, unit=unit)
        del cache, source_cache
        gc.collect()
    return results


def evaluate_final(cards, selection, final, truth_path, lineage, cutoff, out, replicates, seed):
    truth = pd.read_csv(truth_path)
    results = {}
    out.mkdir(parents=True)
    for unit in UNITS:
        cache, arrays, comparisons = {}, {}, {}
        for label, identifier in (("selected", selection[unit]["selected"]), ("reference", REFERENCE)):
            name, threshold_name = identifier.split("__")
            entry = cards[(name, unit)]
            card = entry["card"]
            frame = _view(final, unit, card["vocabulary"], card["specification"]["representation"], cache)
            scores = predict_model((entry["folder"] / "model.txt").read_text(), frame)
            threshold = card["threshold_candidates"][threshold_name]
            labels = _labels(final, frame, unit, truth, lineage, cutoff)
            comparisons[label] = {"candidate": identifier, **distribution(scores, threshold),
                                  "slices": _slices(labels, scores, threshold)}
            arrays[label] = (labels, scores >= threshold)
            frame[META].assign(score=scores, flagged=scores >= threshold).to_parquet(out / f"{unit.lower()}_{label}.parquet", index=False)
        labels, flags = arrays["selected"]
        ref_labels, ref_flags = arrays["reference"]
        keys = ["transaction_ref"] if unit == "FRAUD" else ["account_number", "as_of"]
        if not labels[keys].equals(ref_labels[keys]) or not labels.parent.equals(ref_labels.parent):
            raise ValueError("Final paired comparison alignment failed")
        comparisons["paired_uncertainty"] = clustered_intervals(labels.label, flags, ref_flags, labels.parent,
                                                                 replicates=replicates, seed=seed)
        comparisons["final_recall_guard_passed"] = all(
            comparisons["selected"]["slices"][key]["tp"] >= value["tp"]
            for key, value in comparisons["reference"]["slices"].items())
        results[unit] = comparisons
        _log(phase="fresh_final_scored", unit=unit, selected=comparisons["selected"]["slices"]["all"],
             reference=comparisons["reference"]["slices"]["all"], recall_guard=comparisons["final_recall_guard_passed"])
        del cache, arrays
        gc.collect()
    return results


def run(source: Path, out: Path, models_out: Path, *, train_rows=1000000, validation_rows=200000,
        test_rows=200000, seed=2026101620, final_seed=FINAL_SEED, estimators=100, bootstrap_replicates=300):
    from .generate_v2 import generate
    if out.exists() or models_out.exists():
        raise FileExistsError("Choose new output roots; existing research outputs are immutable")
    if min(train_rows, validation_rows, test_rows) < 1000 or not 1 <= estimators <= 512 or bootstrap_replicates < 0:
        raise ValueError("Invalid run size, estimator count or bootstrap count")
    if final_seed in {seed, seed + 1, 20261015, 20261016, 20261017, 2026101601}:
        raise ValueError("Final seed must be a fresh draw distinct from previous known splits")
    partitions = source_partitions(source)
    groups = parent_groups(partitions["validation"], seed)
    out.mkdir(parents=True)
    models_out.mkdir(parents=True)
    code_paths = [Path(__file__).with_name(name) for name in (
        "robustness.py", "robustness_features.py", "generate_v2.py", "generate.py", "research_features.py",
        "features.py", "model.py", "standardize.py", "pipeline.py", "experiments.py")]
    protocol = {"schema_version": 1, "started_at": _now(), "seed": seed, "final_seed": final_seed,
                "train_rows": train_rows, "validation_rows": validation_rows, "test_rows": test_rows,
                "estimators": estimators, "threshold_quantiles": [.90, .95, .97, .99],
                "candidate_specs": [
                    {"name": "mixed_full_256", "representation": "full", "max_samples": 256},
                    {"name": "mixed_portable_2048", "representation": "portable", "max_samples": 2048},
                    {"name": "mixed_relative_2048", "representation": "relative", "max_samples": 2048}],
                "parent_validation_groups": groups, "validation_group_kind": "Three heldout parent groups; one TRAIN-only fit, not k-fold refits",
                "fit_uses_targets": False, "selection_uses_synthetic_validation_labels": True,
                "selection": {"reference": REFERENCE, "reference_tp_retention": 1.0,
                              "slices": "all, source-TRAIN-q25 low amounts, every parent group, low amounts per group, every stress recipe, clock-observability slices where available",
                              "workload": "overall synthetic FP and unlabelled source flags must not increase",
                              "rank": ["maximum precision at retained reference recall", "maximum average precision", "minimum source flags", "lexical ID"],
                              "fallback": "Reference; a failed improvement is an allowed result"},
                "independence": "Account-disjoint source parents, same source calendar spans; NOT a chronological holdout. Fresh simulated draw from known parent pool/recipe families, not independent real-world crime validation.",
                "library_versions": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__, "sklearn": __import__("sklearn").__version__},
                "source_sha256": sha256(source), "code_checksums": {str(p.resolve()): sha256(p) for p in code_paths},
                "serving": "DRAFT offline research, no database registration, promotion or CF-05 change", "caveat": CAVEAT}
    write_json(out / "protocol.json", protocol)
    initial_protocol_sha256 = sha256(out / "protocol.json")
    _log(phase="protocol_persisted", sha256=initial_protocol_sha256)
    train_manifest = generate(source, out / "train", "train", train_rows, seed)
    validation_manifest = generate(source, out / "validation", "validation", validation_rows, seed + 1,
                                   fit_profile=out / "train" / "fit_profile.json")
    protected = {str(source.resolve()): protocol["source_sha256"], **protocol["code_checksums"],
                 str((out / "protocol.json").resolve()): initial_protocol_sha256}
    for split, manifest in (("train", train_manifest), ("validation", validation_manifest)):
        for name in ("manifest.json", "fit_profile.json", "transactions.csv", "lineage.csv"):
            path = out / split / name
            protected[str(path.resolve())] = sha256(path)
    # Do not open TRAIN answers at all. Do not open validation answers before
    # fits, even for hashing; the generator's manifest supplies the expectation.
    protected[str((out / "validation" / "ground_truth.csv").resolve())] = validation_manifest["files"]["ground_truth.csv"]["sha256"]
    source_frame, source_profile = load_observations(source, "kaggle_bank_v1")
    source_train = source_frame[source_frame.account_number.isin(partitions["train"])].copy()
    source_validation = source_frame[source_frame.account_number.isin(partitions["validation"])].copy()
    del source_frame
    generated, train_profile = load_observations(out / "train" / "transactions.csv")
    cutoff = fit_vocabulary(source_train)["small_amount_threshold"]
    cards = fit_candidates(source_train, generated, models_out, protocol)
    for entry in cards.values():
        protected[str((entry["folder"] / "model.txt").resolve())] = entry["card"]["model_sha256"]
        protected[str((entry["folder"] / "model_card.json").resolve())] = entry["card_sha256"]
    del generated
    gc.collect()
    verify_frozen({"files": protected})
    validation, validation_profile = load_observations(out / "validation" / "transactions.csv")
    results = evaluate_validation(cards, validation, source_validation, out / "validation" / "ground_truth.csv",
                                  out / "validation" / "lineage.csv", cutoff, groups, out / "validation_scores")
    selection = strict_select(results)
    write_json(out / "validation.json", results)
    write_json(out / "selection.json", {"selected_at": _now(), "units": selection, "small_amount_cutoff": cutoff})
    verify_frozen({"files": protected})
    protected.update({str(p.resolve()): sha256(p) for p in [out / "selection.json", out / "validation.json"]})
    frozen = {"models_frozen_at": _now(), "files": protected}
    write_json(out / "frozen.json", frozen)
    verify_frozen(frozen)
    _log(phase="selection_frozen", selected={unit: value["selected"] for unit, value in selection.items()})
    final_started = _now()
    final_manifest = generate(source, out / "final_test", "test", test_rows, final_seed,
                              fit_profile=out / "train" / "fit_profile.json")
    verify_frozen(frozen)
    final, final_profile = load_observations(out / "final_test" / "transactions.csv")
    final_results = evaluate_final(cards, selection, final, out / "final_test" / "ground_truth.csv",
                                   out / "final_test" / "lineage.csv", cutoff, out / "final_scores", bootstrap_replicates, final_seed)
    verify_frozen(frozen)
    report = {"schema_version": 1, "state": "DRAFT", "completed_at": _now(), "caveat": CAVEAT,
              "fit_uses_targets": False, "selection_uses_synthetic_validation_labels": True,
              "generated_rows": {"train": train_rows, "validation": validation_rows, "final": test_rows},
              "source_training_rows": len(source_train), "source_validation_rows": len(source_validation),
              "small_amount_cutoff": cutoff, "selection": selection, "validation": results, "fresh_final": final_results,
              "models_frozen_at": frozen["models_frozen_at"], "final_generation_started_at": final_started,
              "final_seed": final_seed, "test_generated_after_selection_and_freeze": True,
              "final_manifest_sha256": sha256(out / "final_test" / "manifest.json"),
              "normalization": {"source": source_profile, "train": train_profile, "validation": validation_profile, "final": final_profile},
              "frozen_artefact_checksums": frozen, "protocol_sha256": sha256(out / "protocol.json"),
              "independence": protocol["independence"], "no_database_registration_or_promotion": True}
    write_json(out / "report.json", report)
    _log(phase="complete", report=str(out / "report.json"))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--models-out", required=True, type=Path)
    parser.add_argument("--train-rows", default=1000000, type=int)
    parser.add_argument("--validation-rows", default=200000, type=int)
    parser.add_argument("--test-rows", default=200000, type=int)
    parser.add_argument("--seed", default=2026101620, type=int)
    parser.add_argument("--final-seed", default=FINAL_SEED, type=int)
    parser.add_argument("--estimators", default=100, type=int)
    parser.add_argument("--bootstrap-replicates", default=300, type=int)
    args = parser.parse_args()
    run(**vars(args))


if __name__ == "__main__":
    main()
