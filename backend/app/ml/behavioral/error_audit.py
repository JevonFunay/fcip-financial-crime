"""Validation-only error accounting for frozen research scores; never a fit.

AML labels and all slice counts use one account-week per model. Weekly recipe
taxonomy prioritizes stress, then lookalike, then background; multiple recipes
at the same priority are one mixed_recipes unit, never duplicated rows.
Source observations have no verified targets: their flags are review load.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .features import build_features
from .research_features import build_experiment_features
from .standardize import normalize_csv

DIMENSIONS = {
    "recipe": ["recipe"], "label_type": ["label_type"], "amount": ["amount_slice"],
    "history_support": ["history_support"], "old_reference": ["old_reference"],
    "portable_reference": ["portable_reference"], "device": ["device_novelty"],
    "login": ["login_bucket"], "context_login": ["context_novelty", "login_bucket"],
    "precision": ["precision"], "recipe_amount": ["recipe", "amount_slice"],
    "recipe_history": ["recipe", "history_support"],
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _keys(unit: str) -> list[str]:
    return ["account_number", "transaction_ref", "as_of"] if unit == "FRAUD" else ["account_number", "as_of"]


def _joined_truth(observations: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    required = {"transaction_ref", "label", "label_type", "scenario"}
    if not required <= set(truth) or truth.transaction_ref.duplicated().any():
        raise ValueError("Validation truth requires unique references and explicit labels/recipe types")
    if set(observations.transaction_ref) != set(truth.transaction_ref):
        raise ValueError("Validation answers must cover exactly the observations")
    if not truth.label.isin([0, 1]).all() or truth[["label_type", "scenario"]].isna().any().any():
        raise ValueError("Only explicit synthetic0/1 labels and nonmissing types/recipes are accepted")
    if (not truth.label_type.isin(["synthetic_background", "synthetic_lookalike", "synthetic_stress"]).all()
            or not truth.label.eq(1).eq(truth.label_type.eq("synthetic_stress")).all()):
        raise ValueError("Synthetic label types and0/1 interventions disagree")
    joined = observations.merge(truth, on="transaction_ref", suffixes=("", "_truth"), validate="one_to_one")
    if "account_number_truth" in joined and not joined.account_number.eq(joined.account_number_truth).all():
        raise ValueError("Validation truth account identities disagree with observations")
    return joined


def _novelty(value: pd.Series) -> pd.Series:
    return pd.Series(np.where(value.isna(), "unknown", np.where(value > 0, "new", "seen")), index=value.index)


def _recipe(values: pd.Series) -> str:
    names = values.astype(str).unique()
    return str(names[0]) if len(names) == 1 else "mixed_recipes"


def unit_annotations(observations: pd.DataFrame, truth: pd.DataFrame, unit: str,
                     vocabulary: dict, small_cutoff: float) -> pd.DataFrame:
    """Annotate fixed validation units without changing scores or parameters."""
    unit = unit.upper()
    if unit not in {"AML", "FRAUD"}:
        raise ValueError("unit must be AML or FRAUD")
    joined = _joined_truth(observations, truth)
    joined["amount"] = pd.to_numeric(joined.amount).astype(float)
    joined["transaction_date"] = pd.to_datetime(joined.transaction_date, utc=True)
    full = build_features(observations, unit, vocabulary)
    portable = build_experiment_features(observations, unit, vocabulary, "portable")
    keys = _keys(unit)
    if unit == "FRAUD":
        joined["as_of"] = joined.transaction_date
        taxonomy = joined[[*keys, "label", "label_type", "scenario", "amount", "timestamp_precision"]].rename(
            columns={"scenario": "recipe", "timestamp_precision": "precision"})
        login = full.LOGIN_ATTEMPTS
        device = full.DEVICE_FIRST_USE
        context = full[["DEVICE_FIRST_USE", "IP_FIRST_USE", "LOCATION_FIRST_USE"]].max(axis=1)
        old_reference = full.AMOUNT_OVER_PRIOR_MEAN
        portable_reference = portable.AMOUNT_OVER_LAST30_PRIOR_MEAN
    else:
        dates = joined.transaction_date.dt.floor("D")
        joined["as_of"] = dates - pd.to_timedelta(dates.dt.dayofweek, unit="D") + pd.Timedelta(days=7) - pd.Timedelta(seconds=1)
        joined["_priority"] = np.where(joined.label.eq(1), 2,
                                        np.where(joined.label_type.eq("synthetic_lookalike"), 1, 0))
        groups = joined.groupby(keys, sort=True)
        taxonomy = groups.agg(label=("label", "max"), amount=("amount", "mean"),
                              _priority=("_priority", "max"),
                              precision=("timestamp_precision", _recipe)).reset_index()
        taxonomy["precision"] = taxonomy.precision.replace({"mixed_recipes": "mixed_precision"})
        preferred = joined[joined._priority.eq(groups._priority.transform("max"))]
        recipes = preferred.groupby(keys, sort=True).scenario.agg(_recipe).reset_index(name="recipe")
        taxonomy = taxonomy.merge(recipes, on=keys, validate="one_to_one")
        taxonomy["label_type"] = taxonomy.pop("_priority").map(
            {0: "synthetic_background", 1: "synthetic_lookalike", 2: "synthetic_stress"})
        login = full.LOGIN_ATTEMPTS_MAX_W
        device = full.DEVICE_NEW_SHARE_W
        context = full[["DEVICE_NEW_SHARE_W", "IP_NEW_SHARE_W", "LOCATION_NEW_SHARE_W"]].max(axis=1)
        old_reference = full.COUNT_OVER_PRIOR_WEEKLY_MEAN
        portable_reference = portable.TICKET_W_OVER_LAST30_PRIOR_MEAN
    out = full[[*keys]].merge(taxonomy, on=keys, validate="one_to_one", how="left")
    if len(out) != len(full) or out.label.isna().any():
        raise ValueError("Validation taxonomy does not align with feature units")
    # Both builders return the identical public unit ordering.
    if not full[keys].equals(portable[keys]):
        raise ValueError("Full and portable observation units disagree")
    out["amount_slice"] = np.where(out.amount <= small_cutoff, "small", "other")
    out["history_support"] = np.select(
        [full.PRIOR_HISTORY_COUNT < 30, full.HISTORY_DAYS < 30],
        ["under_30_prior", "under_30_days"], default="supported")
    out["old_reference"] = np.where(old_reference.notna(), "available", "unavailable")
    out["portable_reference"] = np.where(portable_reference.notna(), "available", "unavailable")
    out["device_novelty"] = _novelty(device)
    out["context_novelty"] = _novelty(context)
    out["login_bucket"] = np.where(login.isna(), "unknown", np.where(login <= 1, "at_most_one", "two_or_more"))
    return out


def attach_scores(annotations: pd.DataFrame, scores: pd.DataFrame, unit: str, threshold: float) -> pd.DataFrame:
    """Require a complete one-to-one score join, independent of row ordering."""
    keys = _keys(unit)
    values = scores[[*keys, "score"]].copy()
    values["as_of"] = pd.to_datetime(values.as_of, utc=True)
    if values.duplicated(keys).any() or len(values) != len(annotations):
        raise ValueError("Validation score units must be unique and complete")
    out = annotations.merge(values, on=keys, how="outer", indicator=True, validate="one_to_one")
    if not out._merge.eq("both").all() or not np.isfinite(out.score).all():
        raise ValueError("Validation score keys/values do not match accepted observations")
    out = out.drop(columns="_merge")
    out["flagged"] = out.score >= threshold
    out["tp"] = out.flagged & out.label.eq(1)
    out["fp"] = out.flagged & out.label.eq(0)
    out["fn"] = ~out.flagged & out.label.eq(1)
    out["tn"] = ~out.flagged & out.label.eq(0)
    return out


def _counts(frame: pd.DataFrame) -> dict:
    counts = {key: int(frame[key].sum()) for key in ("tp", "fp", "fn", "tn")}
    tp, fp, fn, tn = (counts[key] for key in ("tp", "fp", "fn", "tn"))
    return {"units": len(frame), **counts, "flagged": tp + fp,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "zero_label_flag_rate": fp / (fp + tn) if fp + tn else None}


def aggregate_slices(frame: pd.DataFrame, unit: str, role: str, candidate: str) -> pd.DataFrame:
    """Each dimension partitions the units; dimensions must not be added together."""
    totals = _counts(frame)
    rows = []
    for dimension, columns in DIMENSIONS.items():
        for key, group in frame.groupby(columns, dropna=False, sort=True):
            key = key if isinstance(key, tuple) else (key,)
            row = {"unit": unit, "role": role, "candidate": candidate, "dimension": dimension,
                   "slice": " | ".join(map(str, key)), **_counts(group)}
            row["share_of_all_fp"] = row["fp"] / totals["fp"] if totals["fp"] else 0.
            row["share_of_all_fn"] = row["fn"] / totals["fn"] if totals["fn"] else 0.
            rows.append(row)
    return pd.DataFrame(rows)


def run(validation_dir: Path, research_run: Path, models_root: Path, out: Path) -> dict:
    """Read only validation inputs/cached scores; never open final paths."""
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite audit output: {out}")
    manifest = json.loads((validation_dir / "manifest.json").read_text())
    if manifest.get("split") != "validation":
        raise ValueError("Error audit requires an explicitly declared validation split")
    for name in ("transactions.csv", "ground_truth.csv"):
        if _sha(validation_dir / name) != manifest["files"][name]["sha256"]:
            raise ValueError(f"Validation {name} differs from its manifest")
    normalized = normalize_csv(validation_dir / "transactions.csv", "canonical")
    if not normalized.quarantine.empty:
        raise ValueError("Validation observations failed canonical normalization")
    observations = normalized.frame
    truth = pd.read_csv(validation_dir / "ground_truth.csv")
    selection = json.loads((research_run / "selection.json").read_text())
    validation_results = json.loads((research_run / "validation.json").read_text())
    cutoff = float(selection["small_amount_cutoff"])
    tables, models, evidence = [], {}, {}
    for unit in ("AML", "FRAUD"):
        models[unit] = {}
        for role, identifier in (("selected", selection["units"][unit]["selected"]),
                                 ("reference", selection["units"][unit]["reference"])):
            name, threshold_name = identifier.split("__")
            card_path = models_root / name / unit.lower() / "model_card.json"
            card = json.loads(card_path.read_text())
            threshold = card["threshold_candidates"][threshold_name]
            annotations = unit_annotations(observations, truth, unit, card["vocabulary"], cutoff)
            score_path = research_run / "validation_scores" / f"{name}_{unit.lower()}.parquet"
            scored = attach_scores(annotations, pd.read_parquet(score_path), unit, threshold)
            counts = _counts(scored)
            expected = validation_results[unit][identifier]["synthetic"]["synthetic_stress"]
            if any(counts[key] != expected[key] for key in ("tp", "fp", "fn", "tn")):
                raise ValueError("Cached validation scores do not reproduce declared validation results")
            models[unit][role] = {"candidate": identifier, "threshold": threshold, **counts,
                                  "source_unlabelled_review_load": {
                                      key: validation_results[unit][identifier]["source_unlabelled"][key]
                                      for key in ("units", "units_flagged", "flag_rate")}}
            tables.append(aggregate_slices(scored, unit, role, identifier))
            evidence[f"{unit}_{role}_card"] = _sha(card_path)
            evidence[f"{unit}_{role}_validation_scores"] = _sha(score_path)
    table = pd.concat(tables, ignore_index=True)
    report = {"scope": "VALIDATION ONLY; no final datasets, scores or results read", "fit_performed": False,
              "model_or_threshold_changes": False, "small_amount_cutoff_usd": cutoff,
              "label_scope": "Explicit synthetic intervention labels; zero labels are assumed background/lookalikes, not verified legitimate banking activity.",
              "aml_taxonomy": "One account-week; stress takes priority over lookalike over background. Multiple same-priority recipes become mixed_recipes. Counts never duplicate a week.",
              "slice_scope": "Each dimension partitions the units once; totals across different dimensions must not be added. Associations are not ablation or causal importance.",
              "login_slice": "At most one versus two-or-more observed attempts; descriptive slice, not a risk rule.",
              "models": models, "evidence_sha256": {**evidence, "validation_transactions": _sha(validation_dir / "transactions.csv"),
                                                        "validation_truth": _sha(validation_dir / "ground_truth.csv"),
                                                        "validation_manifest": _sha(validation_dir / "manifest.json"),
                                                        "validation_results": _sha(research_run / "validation.json"),
                                                        "selection": _sha(research_run / "selection.json"),
                                                        "audit_code": _sha(Path(__file__))}}
    report["cached_score_scope"] = "Cached score hashes recorded; complete score joins and declared global confusion counts verified. Scores were not recomputed."
    report["annotation_code_sha256"] = {name: _sha(Path(__file__).with_name(name))
                                         for name in ("features.py", "research_features.py", "standardize.py")}
    report["recipe_errors"] = {}
    for unit in ("AML", "FRAUD"):
        report["recipe_errors"][unit] = {}
        for role in ("selected", "reference"):
            subset = table[(table.unit == unit) & (table.role == role) & (table.dimension == "recipe")]
            report["recipe_errors"][unit][role] = [
                {"recipe": row.slice, **{key: int(getattr(row, key)) for key in ("units", "tp", "fp", "fn", "tn")}}
                for row in subset.itertuples()]
    out.mkdir(parents=True)
    table.to_csv(out / "slices.csv", index=False)
    (out / "audit.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    lines = ["# Validation-only error audit", "", report["label_scope"], "", report["aml_taxonomy"], "",
             "Associations below do not establish causal feature importance. Source flags remain review load only.", ""]
    for unit in ("AML", "FRAUD"):
        for role in ("selected", "reference"):
            totals = models[unit][role]
            lines += [f"## {unit} {role}", "", f"Units {totals['units']}; TP {totals['tp']}, FP {totals['fp']}, FN {totals['fn']}, TN {totals['tn']}.", "",
                      "| Recipe | FP | FN | Zero-label flag rate | Positive-label recall |", "|---|---:|---:|---:|---:|"]
            subset = table[(table.unit == unit) & (table.role == role) & (table.dimension == "recipe")]
            for row in subset.sort_values(["fn", "fp"], ascending=False).itertuples():
                rate = f"{row.zero_label_flag_rate:.2%}" if pd.notna(row.zero_label_flag_rate) else "n/a"
                recall = f"{row.recall:.2%}" if pd.notna(row.recall) else "n/a"
                lines.append(f"| {row.slice} | {row.fp} | {row.fn} | {rate} | {recall} |")
            lines.append("")
    lines += ["See slices.csv for amount, history/reference support, device/context/login and timestamp-precision partitions.",
              "No model fitting, threshold selection, final-data access, promotion or database operation occurred.", ""]
    (out / "audit.md").write_text("\n".join(lines))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-dir", type=Path, required=True)
    parser.add_argument("--research-run", type=Path, required=True)
    parser.add_argument("--models-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.validation_dir, args.research_run, args.models_root, args.out)
    print(json.dumps({"scope": result["scope"], "output": str(args.out), "models": result["models"]}))


if __name__ == "__main__":
    main()
