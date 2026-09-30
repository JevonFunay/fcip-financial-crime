"""Stage 3: evaluate the models on the test dataset, which nothing before has read.

    python -m app.ml.evaluate ml_data/test_full_s20261001 --train ml_data/train_full_s20260923 \\
        --markdown ../docs/ml/evaluation_v1.md

Every scorer uses the threshold chosen on the validation split in stage 2;
nothing is tuned here. Reported (TRD §10.6, PROJECT_CONTEXT §8):

- per model and scorer: ranking quality (AP, ROC-AUC) and the confusion at
  the threshold, over the units a model is trained on
- recall per pattern, per INJECTED_POSITIVE label (TRD §10.6), with a 95%
  Wilson interval, against SM-05's 90%; per domain; and each model's recall
  on the patterns the other one owns
- false positives kept apart: look-alikes and boundary cases per pattern, the
  control cohort (T-DET-CONTROL-01: one alert fails), background alert load;
  participants and post-scenario units reported on their own
- Fraud: how long a takeover runs before the first flag, alerts per 1,000
  transactions, flags on ordinary phone changes and on each ATO look-alike kind
- slices: under 30 days of history, and units without a baseline
- robustness (with --train): each owned pattern left out of training, and each
  pattern's own features removed, retrained on the training dataset and
  measured here. Recall that survives is behaviour learnt; recall that
  collapses was a recipe or a single feature

Everything is synthetic (TRD §10.6, FRD L-01): precision reflects the
generator's prevalence, and no figure here is production detection performance.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.feature_set import FEATURES as FEATURE_DEFS
from app.ml.labels import DOMAIN, OWNS
from app.ml.models import (
    FEATURES,
    choose_threshold,
    gbm_contributions,
    gbm_scores,
    pattern_balanced_weights,
    ranking_metrics,
    split_entities,
    train_gbm,
)
from app.ml.train import CAVEAT, _at_threshold, comparator_scores, load_model, prepare, scenario_detection

SCORERS = ("gbm", "linear", "rules", "anomaly")
SCORER_NAMES = {"gbm": "LightGBM", "linear": "Logistic regression", "rules": "FRD default rules",
                "anomaly": "TRD §10.2 anomaly score"}
TARGET_RECALL = 0.90  # TRD §10.6 / SM-05
WEEKS_IN_PERIOD = 26


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def _recall_entry(detected: int, total: int) -> dict:
    low, high = wilson(detected, total)
    return {"detected": detected, "total": total, "recall": detected / total if total else None,
            "ci95": [round(low, 3), round(high, 3)]}


def _edge_notes(dataset_dir: Path) -> dict[tuple[str, str], str]:
    with (dataset_dir / "labels.csv").open(encoding="utf-8") as handle:
        return {(r["scenario_id"], r["entity_source_id"]): r["note"] for r in csv.DictReader(handle)}


def score_test(model: str, models_dir: Path, frames: dict) -> tuple[dict, dict]:
    booster, linear, card = load_model(models_dir, model)
    X = frames[model][card["feature_set"]["features"]]
    scores = {"gbm": gbm_scores(booster, X), "linear": linear.scores(X), **comparator_scores(model, frames)}
    thresholds = {name: card["validation"][name]["threshold"] for name in SCORERS}
    return scores, {"thresholds": thresholds, "card": card, "booster": booster}


def evaluate_model(model: str, dataset_dir: Path, models_dir: Path, frames, labels, truth) -> dict:
    frame, label = frames[model], labels[model]
    scores, loaded = score_test(model, models_dir, frames)
    y = label.y.to_numpy()
    group = label.group.to_numpy()
    entity = frame.entity_id
    notes = _edge_notes(dataset_dir)
    out = {"units": int(len(frame)), "units_by_group": label.group.value_counts().to_dict(), "scorers": {}}

    for name in SCORERS:
        s = scores[name]
        threshold = loaded["thresholds"][name]
        flagged = np.nan_to_num(s, nan=-np.inf) >= threshold
        result = {"threshold": threshold, **ranking_metrics(s, y), **_at_threshold(s, y, threshold)}

        detection = scenario_detection(label, entity, flagged)
        owned = detection[detection.group == "positive"]
        result["recall_by_pattern"] = {p: _recall_entry(int(g.flagged.sum()), len(g))
                                       for p, g in owned.groupby("pattern")}
        result["recall_owned"] = _recall_entry(int(owned.flagged.sum()), len(owned))
        other = detection[detection.group == "other_positive"]
        result["recall_other_model_patterns"] = {p: _recall_entry(int(g.flagged.sum()), len(g))
                                                 for p, g in other.groupby("pattern")}

        # False positives, kept apart.
        look = pd.DataFrame({"scenario_id": label.scenario_id, "entity": entity, "pattern": label.pattern,
                             "kind": label.kind, "flagged": flagged})[group == "lookalike"]
        look = look.groupby(["scenario_id", "entity", "pattern", "kind"], as_index=False).flagged.max()
        result["lookalikes_flagged"] = {
            f"{p} {k}": {"flagged": int(g.flagged.sum()), "total": len(g)}
            for (p, k), g in look.groupby(["pattern", "kind"])}
        control = group == "control"
        result["control"] = {"entities_flagged": int(entity[control & flagged].nunique()),
                             "entities": int(entity[control].nunique()),
                             "units_flagged": int((control & flagged).sum())}
        background = group == "background"
        result["background"] = {"units": int(background.sum()), "units_flagged": int((background & flagged).sum()),
                                "entities_flagged": int(entity[background & flagged].nunique())}
        if model == "aml":
            result["background"]["flags_per_week"] = round(result["background"]["units_flagged"] / WEEKS_IN_PERIOD, 1)
            result["alerts_per_week_all"] = round(int(flagged.sum()) / WEEKS_IN_PERIOD, 1)
        else:
            result["background"]["flags_per_1000"] = round(1000 * result["background"]["units_flagged"]
                                                           / max(1, result["background"]["units"]), 2)
            result["alerts_per_1000_all"] = round(1000 * int(flagged.sum()) / len(frame), 2)
        for excluded in ("participant", "post_scenario", "screening"):
            rows = group == excluded
            result[f"{excluded}_flagged"] = {"entities_flagged": int(entity[rows & flagged].nunique()),
                                             "entities": int(entity[rows].nunique())}

        # Slices: short history, no baseline.
        short = frame.HISTORY_DAYS.to_numpy() < 30
        baseline_col = "Z_TXN_VALUE_W" if model == "aml" else "AMOUNT_OVER_OWN_P95"
        no_baseline = frame[baseline_col].isna().to_numpy()
        result["slices"] = {}
        for slice_name, mask in (("history_under_30_days", short), (f"no_baseline ({baseline_col} NaN)", no_baseline)):
            pos, neg = mask & (y == 1), mask & (y == 0)
            result["slices"][slice_name] = {
                "positive_units": int(pos.sum()),
                "positive_units_flagged": int((pos & flagged).sum()),
                "negative_units": int(neg.sum()),
                "negative_units_flagged": int((neg & flagged).sum()),
            }

        if model == "fraud":
            result["fraud"] = _fraud_specific(frame, label, flagged, notes)
        out["scorers"][name] = result

    if model in ("aml", "fraud"):
        out["top_features"] = _top_features(loaded["booster"], frame[loaded["card"]["feature_set"]["features"]],
                                            y)
    return out


def _fraud_specific(frame: pd.DataFrame, label: pd.DataFrame, flagged: np.ndarray, notes: dict) -> dict:
    ato = (label.group == "positive").to_numpy() & (label.pattern == "ATO").to_numpy()
    latency_minutes, rows_before = [], []
    missed = 0
    for scenario, idx in pd.Series(np.flatnonzero(ato)).groupby(label.scenario_id.to_numpy()[ato]):
        order = idx.to_numpy()[np.argsort(frame.ts.to_numpy()[idx.to_numpy()])]
        hits = np.flatnonzero(flagged[order])
        if not len(hits):
            missed += 1
            continue
        first = frame.ts.to_numpy()[order[0]]
        latency_minutes.append((frame.ts.to_numpy()[order[hits[0]]] - first) / 60)
        rows_before.append(int(hits[0]))
    phone_change = (label.group == "background").to_numpy() & (frame.DEVICE_FIRST_USE == 1).to_numpy()
    kinds = {}
    edges = (label.group == "lookalike").to_numpy() & (label.pattern == "ATO").to_numpy()
    for (scenario, entity), g in pd.DataFrame({"scenario_id": label.scenario_id[edges], "entity": frame.entity_id[edges],
                                               "flagged": flagged[edges]}).groupby(["scenario_id", "entity"]):
        kind = notes.get((scenario, entity), "?").split(":")[0]
        entry = kinds.setdefault(kind, {"flagged": 0, "total": 0})
        entry["total"] += 1
        entry["flagged"] += int(g.flagged.any())
    return {
        "ato_detected_scenarios": len(latency_minutes), "ato_missed_scenarios": missed,
        "ato_latency_minutes_median": round(float(np.median(latency_minutes)), 1) if latency_minutes else None,
        "ato_latency_minutes_max": round(float(np.max(latency_minutes)), 1) if latency_minutes else None,
        "ato_rows_before_first_flag_median": float(np.median(rows_before)) if rows_before else None,
        "ato_first_row_flagged_share": round(float(np.mean([r == 0 for r in rows_before])), 3) if rows_before else None,
        "ordinary_phone_changes": {"rows": int(phone_change.sum()), "flagged": int((phone_change & flagged).sum())},
        "ato_lookalikes_by_kind": kinds,
    }


def _top_features(booster, X: pd.DataFrame, y: np.ndarray, n: int = 12) -> list[tuple[str, float]]:
    """Mean |TreeSHAP| over the test positives: what the model leans on (ADR-007)."""
    positives = X[y == 1]
    if not len(positives):
        return []
    contributions = np.abs(gbm_contributions(booster, positives))[:, :-1].mean(axis=0)
    order = np.argsort(contributions)[::-1][:n]
    return [(X.columns[i], round(float(contributions[i]), 3)) for i in order]


# --- robustness: leave one pattern out, remove a pattern's own features ------------------------


def _pattern_features(pattern: str, columns: list[str]) -> list[str]:
    tagged = {f.code for f in FEATURE_DEFS if pattern in [p.strip() for p in f.patterns.replace("(", ",").split(",")]}
    return [c for c in columns if c in tagged]


def robustness(model: str, train_dir: Path, test_frames, test_labels, train_prepared) -> dict:
    frames, labels, truth, located, _ = train_prepared
    frame, label = frames[model], labels[model]
    columns = list(FEATURES[model])
    split = split_entities(truth, sorted(set(frames["fraud"].entity_id)), located.participants)
    side = frame.entity_id.map(split).fillna("train").to_numpy()
    y_all = label.y.to_numpy()
    test_frame, test_label = test_frames[model], test_labels[model]
    results = {}
    for pattern in OWNS[model]:
        for experiment in ("leave_out", "ablate"):
            keep = ~np.isnan(y_all)
            cols = columns
            if experiment == "leave_out":
                keep &= ~((label.group == "positive") & (label.pattern == pattern)).to_numpy()
            else:
                dropped = _pattern_features(pattern, columns)
                cols = [c for c in columns if c not in dropped]
            tr, va = keep & (side == "train"), keep & (side == "validation")
            w = pattern_balanced_weights(y_all[tr], label.pattern[tr].reset_index(drop=True))
            booster = train_gbm(frame.loc[tr, cols], y_all[tr], w, frame.loc[va, cols], y_all[va])
            validation = side == "validation"
            v_scores = gbm_scores(booster, frame.loc[validation, cols])
            v_y = np.where(keep[validation], y_all[validation], np.nan)
            chosen = choose_threshold(v_scores, v_y, (label.group == "control").to_numpy()[validation])
            t_scores = gbm_scores(booster, test_frame[cols])
            flagged = t_scores >= chosen["threshold"]
            detection = scenario_detection(test_label, test_frame.entity_id, flagged)
            target = detection[(detection.group == "positive") & (detection.pattern == pattern)]
            others = detection[(detection.group == "positive") & (detection.pattern != pattern)]
            lookalike = (test_label.group == "lookalike").to_numpy() & (test_label.pattern == pattern).to_numpy()
            results.setdefault(pattern, {})[experiment] = {
                "recall": _recall_entry(int(target.flagged.sum()), len(target)),
                "recall_other_patterns": _recall_entry(int(others.flagged.sum()), len(others)),
                "lookalike_units_flagged": int((lookalike & flagged).sum()),
                "threshold": chosen["threshold"],
                **({"features_removed": _pattern_features(pattern, columns)} if experiment == "ablate" else {}),
            }
    return results


# --- report ----------------------------------------------------------------------------------


def _fmt_recall(entry: dict) -> str:
    if not entry or entry["total"] == 0:
        return "-"
    return f"{entry['detected']}/{entry['total']} ({entry['recall']:.0%}, {entry['ci95'][0]:.0%}-{entry['ci95'][1]:.0%})"


def render(report: dict) -> str:
    lines = [
        "# Model evaluation, stage 3",
        "",
        f"> **{CAVEAT}**",
        "",
        f"Test dataset: generator {report['test_data']['generator_version']}, profile "
        f"{report['test_data']['profile']}, seed {report['test_data']['seed']} (unseen until this stage). Models "
        f"trained on seed {report['train_seed']}; every threshold was chosen on the training seed's validation "
        "split. Generated by `python -m app.ml.evaluate`; do not edit by hand.",
        "",
    ]
    for model in ("aml", "fraud"):
        m = report[model]
        unit = "entity-week" if model == "aml" else "transaction"
        lines += [f"## {model.upper()} model (one row per {unit}; owns {', '.join(OWNS[model])})", "",
                  "| Scorer | AP | ROC-AUC | Precision | Recall | F1 | Look-alike units flagged | Control entities flagged | "
                  + ("Background flags / week |" if model == "aml" else "Background flags / 1,000 |"),
                  "|---|---|---|---|---|---|---|---|---|"]
        for name in SCORERS:
            s = m["scorers"][name]
            look = sum(v["flagged"] for v in s["lookalikes_flagged"].values())
            look_total = sum(v["total"] for v in s["lookalikes_flagged"].values())
            bg = s["background"].get("flags_per_week", s["background"].get("flags_per_1000"))
            ap = "-" if s["average_precision"] is None else f"{s['average_precision']:.3f}"
            auc = "-" if s["roc_auc"] is None else f"{s['roc_auc']:.3f}"
            lines.append(f"| {SCORER_NAMES[name]} | {ap} | {auc} | {s['precision']:.3f} | {s['recall']:.3f} | "
                         f"{s['f1']:.3f} | {look}/{look_total} labels | {s['control']['entities_flagged']}/"
                         f"{s['control']['entities']} | {bg} |")
        lines += ["", f"Recall per INJECTED_POSITIVE label, 95% Wilson interval (target {TARGET_RECALL:.0%}, SM-05):", "",
                  "| Pattern | Domain | " + " | ".join(SCORER_NAMES[n] for n in SCORERS) + " |",
                  "|---|---|" + "---|" * len(SCORERS)]
        for pattern in OWNS[model]:
            cells = [_fmt_recall(m["scorers"][n]["recall_by_pattern"].get(pattern, {})) for n in SCORERS]
            lines.append(f"| {pattern} | {DOMAIN.get(pattern, '-')} | " + " | ".join(cells) + " |")
        cells = [_fmt_recall(m["scorers"][n]["recall_owned"]) for n in SCORERS]
        lines.append("| **all owned** | | " + " | ".join(cells) + " |")
        other = m["scorers"]["gbm"]["recall_other_model_patterns"]
        if other:
            lines += ["", "Cross-coverage, LightGBM on the patterns the other model owns: "
                      + ", ".join(f"{p} {_fmt_recall(e)}" for p, e in sorted(other.items())) + "."]
        g = m["scorers"]["gbm"]
        lines += ["", "LightGBM false positives by look-alike kind: "
                  + ", ".join(f"{k} {v['flagged']}/{v['total']}" for k, v in sorted(g["lookalikes_flagged"].items()))
                  + "."]
        lines += ["", "LightGBM on units left out of training: "
                  + ", ".join(f"{k.replace('_flagged', '')} {v['entities_flagged']}/{v['entities']} entities"
                              for k, v in g.items() if k.endswith("_flagged") and isinstance(v, dict)
                              and "entities" in v) + "."]
        lines += ["", "Slices (LightGBM): " + "; ".join(
            f"{name}: positives {v['positive_units_flagged']}/{v['positive_units']} flagged, negatives "
            f"{v['negative_units_flagged']}/{v['negative_units']}" for name, v in g["slices"].items()) + "."]
        if model == "fraud":
            f = g["fraud"]
            lines += ["", f"Takeovers (LightGBM): {f['ato_detected_scenarios']} detected, {f['ato_missed_scenarios']} "
                      f"missed; first flag after a median {f['ato_latency_minutes_median']} minutes (max "
                      f"{f['ato_latency_minutes_max']}), the first takeover row itself flagged in "
                      f"{f['ato_first_row_flagged_share']:.0%} of them. Ordinary phone changes flagged: "
                      f"{f['ordinary_phone_changes']['flagged']}/{f['ordinary_phone_changes']['rows']} rows. "
                      "ATO look-alikes flagged by kind: " + ", ".join(
                          f"{k} {v['flagged']}/{v['total']}" for k, v in sorted(f["ato_lookalikes_by_kind"].items()))
                      + f". All alerts: {g['alerts_per_1000_all']} per 1,000 transactions."]
        else:
            lines += ["", f"All LightGBM flags: {g['alerts_per_week_all']} entity-weeks per week."]
        lines += ["", "Top features (mean |TreeSHAP| on test positives): "
                  + ", ".join(f"`{c}` {v}" for c, v in m.get("top_features", [])) + ".", ""]
    if "robustness" in report:
        lines += ["## Robustness: behaviour or recipe?", "",
                  "Each owned pattern left out of training entirely (does the model still flag behaviour it never "
                  "saw labelled?), and each pattern's own features removed (does it depend on one feature?). "
                  "Retrained on the training seed, thresholds from validation, measured on the test seed.", "",
                  "| Model | Pattern | Full model | Pattern left out | Its features removed | Features removed |",
                  "|---|---|---|---|---|---|"]
        for model in ("aml", "fraud"):
            for pattern, r in report["robustness"][model].items():
                full = report[model]["scorers"]["gbm"]["recall_by_pattern"].get(pattern, {})
                removed = ", ".join(f"`{c}`" for c in r["ablate"]["features_removed"]) or "-"
                lines.append(f"| {model.upper()} | {pattern} | {_fmt_recall(full)} | {_fmt_recall(r['leave_out']['recall'])} "
                             f"| {_fmt_recall(r['ablate']['recall'])} | {removed} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 3: evaluate the models on the test dataset.")
    parser.add_argument("dataset", type=Path, help="the test dataset, with its features built")
    parser.add_argument("--models", type=Path, default=Path("ml_models"))
    parser.add_argument("--train", type=Path, default=None, help="training dataset: run the robustness tests")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--markdown", type=Path, default=None)
    args = parser.parse_args()
    started = time.perf_counter()
    frames, labels, truth, located, build = prepare(args.dataset)
    report = {"caveat": CAVEAT,
              "test_data": {k: build["dataset"][k] for k in ("generator_version", "profile", "seed", "manifest_sha256")}}
    for model in ("aml", "fraud"):
        report[model] = evaluate_model(model, args.dataset, args.models, frames, labels, truth)
    report["train_seed"] = json.loads((args.models / "aml_gbm_v1" / "model_card.json").read_text())[
        "training_data"]["seed"]
    if args.train:
        prepared = prepare(args.train)
        report["robustness"] = {model: robustness(model, args.train, frames, labels, prepared)
                                for model in ("aml", "fraud")}
    report["seconds"] = round(time.perf_counter() - started, 1)
    out_json = args.json or args.models / "evaluation_v1.json"
    out_json.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    text = render(report)
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
