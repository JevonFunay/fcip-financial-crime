"""Evaluate the repaired Lab on new coverage data and a sealed future draw.

No application database, registry or activation is touched. All candidate
defaults and the diagnostic/reference arms are fixed before fresh final data
exists. Same-generator synthetic coverage is not verified financial crime.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import platform
import subprocess
import tarfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.lab.features import FEATURE_GROUPS, build_matrix
from app.ml.lab.mapping import apply_mapping, read_csv_bytes
from app.ml.lab.trainer import SEED, TrainedModel, best_f1_threshold, chronological_split, train_run
from app.scripts import audit_current_lab as shared
from app.scripts import generate_lab_dataset as data

VERSION = "lab_repair_quality_audit_v1"
BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
SOURCE_ROWS = 250_000
FINAL_ROWS = 100_000
SOURCE_SEED = 2026110403
FINAL_SEED = 2026110404
SOURCE_START = date(2024, 1, 1)
FINAL_START = date(2027, 1, 1)
TRAIN_SHARE = .6
MIN_ACTUAL_TRAIN = 100_000
GROUPS = [group["key"] for group in FEATURE_GROUPS]
MODEL_KEYS = ["logistic_regression", "lightgbm", "isolation_forest"]
PREVIOUS = BACKEND / "ml_data/lab_quality_s20261104"
PREVIOUS_REPORT_SHA = "7f78a400abae76561b653bf233a7eba68d2a16a6e86d1b56924a699eb00394d1"
PREVIOUS_ARCHIVE_SHA = "bcd5924db75d7683cf7957ce1a92244fa35023eab147a18f2b6287bb320a98af"
CODE_PATHS = sorted(set(shared.CODE_PATHS + [
    "app/scripts/audit_lab_repair.py", "app/scripts/generate_lab_dataset.py",
    "app/ml/behavioral/research_evidence.py",
]))
LIMITATIONS = [
    "Synthetic intervention/background labels, not adjudicated real fraud, money laundering or production prevalence.",
    "All final arms are fixed before generation; final results do not select, repair or retune a model.",
    "The previous model is scored on the SAME new final, not compared with percentages from its different old final.",
    "The two generator draws share recipes; identifier namespace separation does not establish independent real customers.",
    "Benign controls include exact observable counterfactuals and matching event prefixes; unobserved intent is not recoverable perfectly.",
    "Chronological splits can share prior account history and episode fragments; they are not independent-case holdouts.",
    "P05 has >=100-day inactivity, so its prior90-day amount references can be missing even when lifetime history is available.",
    "Episode any-hit recall means one positive event flag, not all rows detected, confirmed crime, or prevention.",
    "First-flag delay is conditional on detected episodes; missed episodes are separately counted, not assigned zero delay.",
    "Class-weighted supervised outputs are uncalibrated scores; no live ACTIVE artifact or application alert count is measured.",
]


def pin_previous(expected: dict[str, str]) -> dict:
    """Use original expectations, including an additive archived code snapshot."""
    report_path = PREVIOUS / "report.json"
    if shared.sha256(report_path) != PREVIOUS_REPORT_SHA:
        raise ValueError("The original previous report no longer matches its pinned checksum")
    report = json.loads(report_path.read_text())
    frozen_path = PREVIOUS / "frozen.json"
    if shared.sha256(frozen_path) != report["frozen_manifest_sha256"]:
        raise ValueError("The previous frozen manifest changed")
    required = [PREVIOUS / "models/lightgbm.txt", PREVIOUS / "models/lightgbm_contract.json",
                PREVIOUS / "preprocessing.json"]
    for path in required:
        original = report["frozen"]["files"].get(str(path.resolve()))
        if original is None or shared.sha256(path) != original:
            raise ValueError(f"Previous reference artifact differs from its original expectation: {path}")
        expected[str(path.resolve())] = original
    archive_dir = PREVIOUS / "source_snapshot_0382821"
    snapshot_path, archive_path = archive_dir / "snapshot.json", archive_dir / "source.tar"
    snapshot = json.loads(snapshot_path.read_text())
    if (snapshot["original_report_sha256"] != PREVIOUS_REPORT_SHA
            or snapshot["original_frozen_manifest_sha256"] != report["frozen_manifest_sha256"]
            or snapshot["archive_sha256"] != PREVIOUS_ARCHIVE_SHA
            or shared.sha256(archive_path) != PREVIOUS_ARCHIVE_SHA):
        raise ValueError("Previous source archive does not match its declared original provenance")
    original_code = {str(Path(name).relative_to(ROOT)): wanted
                     for name, wanted in report["frozen"]["files"].items()
                     if Path(name).is_relative_to(BACKEND / "app")}
    if not original_code or snapshot["protected_code"] != original_code:
        raise ValueError("Previous source snapshot must contain the complete original protected code map")
    with tarfile.open(archive_path) as archive:
        for relative, wanted in snapshot["protected_code"].items():
            member = archive.extractfile(relative)
            if member is None or hashlib.sha256(member.read()).hexdigest() != wanted:
                raise ValueError(f"Archived original code changed: {relative}")
            original_path = str((ROOT / relative).resolve())
            if report["frozen"]["files"].get(original_path) != wanted:
                raise ValueError("Source snapshot does not reproduce the original frozen code expectation")
    feature_path = BACKEND / "app/ml/behavioral/features.py"
    if shared.sha256(feature_path) != snapshot["protected_code"]["backend/app/ml/behavioral/features.py"]:
        raise ValueError("Matched previous model requires the original unchanged behavioral feature code")
    for path in (report_path, frozen_path, snapshot_path, archive_path):
        shared.seal(expected, path)
    contract = json.loads(required[1].read_text())
    preprocessing = json.loads(required[2].read_text())
    model = TrainedModel("lightgbm", contract["artefact_type"], required[0].read_text(),
                         contract["features"], contract["thresholds"], [], contract["default_index"])
    if model.thresholds[model.default_index] != contract["threshold"]:
        raise ValueError("Previous native default threshold contract differs")
    return {"model": model, "contract": contract, "preprocessing": preprocessing,
            "report_sha256": PREVIOUS_REPORT_SHA, "source_commit": snapshot["source_commit"],
            "frozen_manifest_sha256": report["frozen_manifest_sha256"],
            "source_date_to": report["source_validation"]["date_to"]}


def assert_validation_only(outcome) -> None:
    if "positive_rate_test" in outcome.results:
        raise ValueError("Source TEST prevalence must remain hidden before selection")
    if outcome.results.get("split", {}).get("test", {}).get("positives") is not None:
        raise ValueError("Source TEST positive count leaked through the native comparison")
    for summary in outcome.results["models"]:
        if any(key.startswith("test") for key in summary):
            raise ValueError("Native comparison must contain validation results only")
    if any(model.test_curve for model in outcome.models):
        raise ValueError("Native training computed source TEST predictions before selection")


def lineage_for(frame: pd.DataFrame, folder: Path) -> pd.DataFrame:
    lineage = pd.read_csv(folder / "lineage.csv", keep_default_na=False)
    if not lineage.transaction_ref.is_unique:
        raise ValueError("Lineage identifiers must be unique")
    aligned = lineage.set_index("transaction_ref").loc[frame.transaction_ref].reset_index()
    if not np.array_equal(aligned.transaction_ref, frame.transaction_ref):
        raise ValueError("Final lineage does not align with accepted observations")
    return aligned


def negative_cohorts(lineage: pd.DataFrame) -> pd.Series:
    values = np.where(lineage.role.eq("history"), "history",
             np.where(lineage.case_label.eq("background"), "ordinary_background",
             np.where(lineage.case_label.eq("benign_lookalike"),
                      np.where(lineage.exact_counterfactual.eq(1), "benign_exact_event", "benign_other_event"),
                      "positive_scenario_event")))
    return pd.Series(values)


def episode_results(cache: pd.DataFrame, lineage: pd.DataFrame, scenarios: list[dict],
                    scores: np.ndarray, threshold: float, small_cutoff: float) -> dict:
    joined = cache[["transaction_ref", "transaction_date", "amount", "label"]].copy()
    if not np.array_equal(joined.transaction_ref, lineage.transaction_ref):
        raise ValueError("Episode metadata does not match cached observations")
    for column in ("scenario_id", "pattern", "role", "case_label", "exact_counterfactual"):
        joined[column] = lineage[column].to_numpy()
    joined["flag"] = scores >= threshold
    positive, benign = {}, {}
    small = {}
    for pattern in data.PATTERNS:
        y = joined.label.to_numpy()
        mask = joined.pattern.eq(pattern).to_numpy() & (y == 1) & (joined.amount.to_numpy(float) <= small_cutoff)
        count, caught = int(mask.sum()), int((mask & joined.flag.to_numpy()).sum())
        small[pattern] = {"positive_rows": count, "caught": caught, "missed": count - caught,
                          "recall": caught / count if count else None}
        for label, output in (("simulated_intervention", positive), ("benign_lookalike", benign)):
            declared = [case for case in scenarios if case["pattern"] == pattern and case["case_label"] == label]
            assessed = hits = fully = unassessed = 0
            delays = []
            for case in declared:
                rows = joined[joined.scenario_id.eq(case["scenario_id"]) & joined.role.eq("event")]
                rows = rows[np.isfinite(rows.label)]
                if label == "simulated_intervention":
                    rows = rows[rows.label.eq(1)]
                else:
                    rows = rows[rows.label.eq(0)]
                if rows.empty:
                    unassessed += 1
                    continue
                assessed += 1
                flagged = rows[rows.flag]
                hits += int(len(flagged) > 0)
                fully += int(rows.flag.all() and len(rows) == case["event_rows"])
                if len(flagged):
                    delays.append(float((flagged.transaction_date.min() - rows.transaction_date.min()).total_seconds()))
            output[pattern] = {
                "declared_episodes": len(declared), "assessed_episodes": assessed, "unassessed_episodes": unassessed,
                "episodes_with_any_event_flag": hits, "episodes_with_all_declared_event_rows_flagged": fully,
                "any_event_flag_rate": hits / assessed if assessed else None,
                "episodes_without_event_flag": assessed - hits,
                "detected_episode_first_flag_delay_seconds_median": float(np.median(delays)) if delays else None,
                "detected_episode_first_flag_delay_seconds_mean": float(np.mean(delays)) if delays else None,
            }
    return {"positive_episodes": positive, "benign_episodes": benign, "small_positive_pattern_recall": small,
            "definition": "Any positive EVENT row flag is one episode hit. Benign rates use negative EVENT rows. Delay is conditional on detected episodes; history flags do not count as event hits."}


def matched_comparison(selected: dict, reference: dict) -> dict:
    left, right = selected["slices"]["all"], reference["slices"]["all"]
    if (left["rows"], left["positive_rows"]) != (right["rows"], right["positive_rows"]):
        raise ValueError("Reference and selected model do not share an evaluation population")
    guards = {"all": left["tp"] >= right["tp"],
              "small_amount": selected["slices"]["small_amount_train_q25"]["tp"] >= reference["slices"]["small_amount_train_q25"]["tp"]}
    for pattern in data.PATTERNS:
        a, b = selected["pattern_recall"][pattern], reference["pattern_recall"][pattern]
        guards[pattern] = a["caught"] >= b["caught"] if a["positive_rows"] else None
    gain = left["precision"] - right["precision"] if left["precision"] is not None and right["precision"] is not None else None
    return {"same_final_population": True, "precision_delta_percentage_points": gain * 100 if gain is not None else None,
            "tp_delta": left["tp"] - right["tp"], "fp_delta": left["fp"] - right["fp"],
            "reference_tp_count_guards": guards, "overall_fp_does_not_increase": left["fp"] <= right["fp"],
            "matched_gain_with_reference_coverage": bool(gain is not None and gain > 0 and left["fp"] <= right["fp"] and all(v is not False for v in guards.values())),
            "production_approval": False, "final_selection_or_retuning": False}


def run(out: Path) -> dict:
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    expected: dict[str, str] = {}
    for relative in CODE_PATHS:
        shared.seal(expected, BACKEND / relative)
    previous = pin_previous(expected)
    protocol = {
        "version": VERSION, "state": "DRAFT", "created_at_utc": shared.now(),
        "source": {"rows": SOURCE_ROWS, "seed": SOURCE_SEED, "start": SOURCE_START.isoformat(), "days": 720},
        "final": {"rows": FINAL_ROWS, "seed": FINAL_SEED, "start": FINAL_START.isoformat(), "days": 720},
        "generator": data.VERSION, "train_share": TRAIN_SHARE, "model_seed": SEED,
        "minimum_actual_train_rows": MIN_ACTUAL_TRAIN,
        "models": MODEL_KEYS, "feature_groups": GROUPS,
        "selection": "native highest validation AP; native validation max-F1 defaults",
        "source_test": "fixed-generator label-support metadata inspected; no source TEST predictions, ranking or curves evaluated",
        "diagnostics": "validation-fixed amount-only and pinned old LightGBM; excluded from candidate selection",
        "reference": {key: previous[key] for key in
                      ("report_sha256", "source_commit", "frozen_manifest_sha256", "source_date_to")},
        "reference_slice_policy": "same new-source TRAIN amount quartiles for matched evaluation; old model retains its own feature vocabulary",
        "software": {name: importlib.import_module(name).__version__ for name in
                     ("numpy", "pandas", "pyarrow", "sklearn", "lightgbm")},
        "python": platform.python_version(), "limitations": LIMITATIONS,
    }
    try:
        protocol["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        protocol["git_head"] = "unknown"
    shared.write_json(out / "protocol.json", protocol, expected)
    print("Generating fixed new coverage source; final does not exist", flush=True)
    source_folder = out / "source"
    source_manifest = data.generate_dataset(source_folder, rows=SOURCE_ROWS, seed=SOURCE_SEED, start=SOURCE_START)
    for path in sorted(source_folder.iterdir()):
        if path.is_file():
            shared.seal(expected, path)
    raw = read_csv_bytes((source_folder / "transactions.csv").read_bytes())
    if len(raw) != SOURCE_ROWS or source_manifest["rows"] != SOURCE_ROWS:
        raise ValueError("Source row count differs from the predeclared study")
    mapping = source_manifest["coverage"]["mapping"]
    mapped = apply_mapping(raw, mapping)
    parts = chronological_split(mapped.frame, TRAIN_SHARE)
    if int((parts == "train").sum()) < MIN_ACTUAL_TRAIN:
        raise ValueError("The repaired study must have at least 100,000 actual TRAIN observations")
    shared.write_json(out / "mapping.json", mapping, expected)
    shared.write_json(out / "source_validation.json", mapped.report, expected)
    shared.verify_hashes(expected)
    print("Training repaired native Lab, validation-only results", flush=True)
    outcome = train_run(mapped.frame, mapped.labels, mapped.categories,
                        feature_groups=GROUPS, model_keys=MODEL_KEYS, train_share=TRAIN_SHARE, seed=SEED)
    assert_validation_only(outcome)
    shared.verify_hashes(expected)
    _, vocabulary = build_matrix(mapped.frame, parts == "train")
    if vocabulary != outcome.results["features"]["vocabulary"]:
        raise ValueError("Native TRAIN-only vocabulary does not reproduce")
    quartiles = mapped.frame.loc[parts == "train", "amount"].astype(float).quantile([.25, .5, .75]).tolist()
    selection = shared.choose_defaults(outcome.results, outcome.models)
    val_mask = parts == "validation"
    values = mapped.frame.loc[val_mask, "amount"].to_numpy(dtype=float)
    threshold = best_f1_threshold(values, mapped.labels[val_mask])
    selection["diagnostic_baselines"] = {"amount_only": {"threshold": threshold,
        "threshold_rule": "validation max-F1 on amount only", "features": ["AMOUNT"],
        "not_eligible_for_lab_winner": True,
        "validation": shared.metric_counts(mapped.labels[val_mask], values, threshold)}}
    shared.write_json(out / "validation_results.json", outcome.results, expected)
    shared.write_json(out / "preprocessing.json", {"vocabulary": vocabulary, "amount_quartiles_train": quartiles,
                                                   "used_features": outcome.results["features"]["used"]}, expected)
    shared.write_json(out / "selection.json", selection, expected)
    model_folder = out / "models"
    model_folder.mkdir()
    for model in outcome.models:
        shared.write_text(model_folder / f"{model.key}.txt", model.model_text, expected)
        shared.write_json(model_folder / f"{model.key}_contract.json", selection["models"][model.key], expected)
    shared.verify_hashes(expected)
    frozen = {"frozen_at_utc": shared.now(), "files": dict(expected), "selection": selection,
              "fresh_final_not_created": True, "source_test_not_scored": True}
    shared.write_json(out / "frozen.json", frozen)
    frozen_sha = shared.sha256(out / "frozen.json")
    shared.verify_frozen(frozen, out / "frozen.json", frozen_sha)
    final_started = shared.now()
    print(f"Frozen validation winner {selection['selected_model']}; generating fresh final now", flush=True)
    final_folder = out / "final"
    final_manifest = data.generate_dataset(final_folder, rows=FINAL_ROWS, seed=FINAL_SEED, start=FINAL_START)
    final_expected: dict[str, str] = {}
    for path in sorted(final_folder.iterdir()):
        if path.is_file():
            shared.seal(final_expected, path)
    shared.verify_frozen(frozen, out / "frozen.json", frozen_sha)
    shared.verify_hashes(final_expected)
    final_raw = read_csv_bytes((final_folder / "transactions.csv").read_bytes())
    if len(final_raw) != FINAL_ROWS or final_manifest["rows"] != FINAL_ROWS:
        raise ValueError("Final row count differs from the predeclared study")
    frozen_mapping = json.loads((out / "mapping.json").read_text())
    frozen_preprocessing = json.loads((out / "preprocessing.json").read_text())
    frozen_selection = json.loads((out / "selection.json").read_text())
    frozen_models = []
    for key, contract in frozen_selection["models"].items():
        stored_contract = json.loads((out / "models" / f"{key}_contract.json").read_text())
        if stored_contract != contract:
            raise ValueError("Sealed native model contract differs from sealed selection")
        frozen_models.append(TrainedModel(key, contract["artefact_type"],
            (out / "models" / f"{key}.txt").read_text(), contract["features"],
            contract["thresholds"], [], contract["default_index"]))
    # Reload the pinned previous artifacts as well, never mutable draft state.
    previous = pin_previous({})
    population = shared.map_final(final_raw, frozen_mapping, vocabulary["currency"])
    previous_source_end = pd.to_datetime(previous["source_date_to"], utc=True)
    latest_source_end = max(mapped.frame.transaction_date.max(), previous_source_end)
    if population.frame.transaction_date.min() <= latest_source_end:
        raise ValueError("Every fresh final observation must follow both source calendars, including the previous reference")
    aligned = lineage_for(population.frame, final_folder)
    cohorts = negative_cohorts(aligned)
    final, cache, features = shared.evaluate_fixed(population, frozen_models, frozen_preprocessing["vocabulary"],
                                                 frozen_selection, frozen_preprocessing["amount_quartiles_train"], cohorts)
    old_model = previous["model"]
    old_selection = {"models": {"lightgbm": previous["contract"]}}
    old_reports, old_cache, old_features = shared.evaluate_fixed(population, [old_model],
        previous["preprocessing"]["vocabulary"], old_selection, frozen_preprocessing["amount_quartiles_train"], cohorts)
    if not np.array_equal(cache.transaction_ref, old_cache.transaction_ref):
        raise ValueError("Previous and repaired models do not score identical final rows")
    final["previous_lightgbm"] = old_reports["lightgbm"]
    cache["score_previous_lightgbm"] = old_cache["score_lightgbm"].to_numpy()
    scenarios = json.loads((final_folder / "scenarios.json").read_text())
    for key, metrics in final.items():
        metrics["episodes"] = episode_results(cache, aligned, scenarios, cache[f"score_{key}"].to_numpy(),
                                               metrics["threshold"], quartiles[0])
    shared.verify_hashes(final_expected)
    for name, frame in (("final_scores.parquet", cache), ("final_features.parquet", features),
                        ("previous_final_features.parquet", old_features)):
        path = out / name
        frame.to_parquet(path, index=False)
        shared.seal(final_expected, path)
    shared.write_json(out / "final_validation.json", population.report, final_expected)
    channels = {"source_previous_vocab_unseen_fraction": float((~mapped.frame.channel.isin(previous["preprocessing"]["vocabulary"]["channels"])).mean())}
    channels["final_previous_vocab_unseen_fraction"] = float((~population.frame.channel.isin(previous["preprocessing"]["vocabulary"]["channels"])).mean())
    shared.verify_frozen(frozen, out / "frozen.json", frozen_sha)
    shared.verify_hashes(final_expected)
    report = {
        "version": VERSION, "state": "DRAFT", "completed_at_utc": shared.now(), "protocol": protocol,
        "source_validation": mapped.report, "source_coverage": source_manifest["coverage"],
        "source_counterfactual_diagnostic": source_manifest["counterfactual_diagnostic"],
        "selection": selection, "validation_results": outcome.results,
        "final_validation": population.report, "final_coverage": final_manifest["coverage"],
        "final_counterfactual_diagnostic": final_manifest["counterfactual_diagnostic"],
        "final": final, "selected_final": final[selection["selected_model"]],
        "matched_previous_comparison": matched_comparison(final[selection["selected_model"]], final["previous_lightgbm"]),
        "channel_compatibility": channels, "amount_quartiles_train": quartiles,
        "frozen": frozen, "frozen_manifest_sha256": frozen_sha,
        "final_generation_started_at_utc": final_started,
        "freeze_precedes_final_generation": frozen["frozen_at_utc"] < final_started,
        "entire_source_precedes_final": True, "source_internal_test_evaluated": False,
        "previous_entire_source_precedes_final": True,
        "source_test_label_support_inspected": True,
        "source_internal_test_prediction_metrics_evaluated": False,
        "final_expected_hashes": dict(final_expected), "final_retuned": False,
        "database_registration_or_activation": False, "limitations": LIMITATIONS,
    }
    shared.write_json(out / "report.json", report)
    print(json.dumps({"report": str(out / "report.json"), "selected": selection["selected_model"],
                      "final": {key: metrics["slices"]["all"] for key, metrics in final.items()}}, indent=2), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.out)


if __name__ == "__main__":
    main()
