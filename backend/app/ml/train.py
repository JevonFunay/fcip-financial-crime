"""Stage 2: train the AML and Fraud models on the training dataset.

    python -m app.ml.train ml_data/train_full_s20260923            # -> ml_models/

For each model (AML per entity-week, Fraud per transaction) it writes, under
`ml_models/<model>_gbm_v1/` (ignored by git, reproducible from the seed):

    model.txt          LightGBM, text format
    linear.json        the logistic-regression comparator
    model_card.json    TRD §10.5 envelope + provenance, parameters, thresholds,
                       validation metrics, canary
    split.csv          which training entities were held out for validation

Only the training dataset is read. Thresholds for the model and for every
comparator are chosen on the validation split; the test dataset is for
stage 3 and nothing here looks at it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml import comparators
from app.ml.feature_set import FEATURE_SET_VERSION, PARAMETERS
from app.ml.labels import OWNS, aml_labels, fraud_labels, load_truth, locate
from app.ml.models import (
    CANARY_BAND,
    CANARY_ROUNDS,
    CANARY_SHUFFLES,
    EARLY_STOPPING,
    FEATURES,
    GBM_PARAMS,
    MAX_ROUNDS,
    SPLIT_SEED,
    VALIDATION_SHARE,
    LinearModel,
    canonical,
    choose_threshold,
    definition_hash,
    gbm_scores,
    pattern_balanced_weights,
    canary,
    ranking_metrics,
    split_entities,
    train_gbm,
    train_linear,
)
from app.ml.reference import REFERENCE_PATH

MODEL_VERSION = {"aml": "aml_gbm_v1", "fraud": "fraud_gbm_v1"}
UNIT = {"aml": "one (entity, calendar week), as of Sunday 23:59:59 Asia/Jakarta",
        "fraud": "one transaction, as of that transaction"}
CAVEAT = ("Synthetic data only (TRD §10.6, FRD L-01): every figure is measured on generated data whose "
          "prevalence is an artefact of the generator. Not an estimate of production detection performance.")


def load_matrices(dataset_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    folder = dataset_dir / f"features_{FEATURE_SET_VERSION}"
    build = json.loads((folder / "build.json").read_text())
    reference = json.loads(REFERENCE_PATH.read_text())["definition_hash"]
    if build.get("mcc_ticket_reference_sha256") != reference:
        raise SystemExit(f"{folder} was built with another MCC reference; rebuild the features first")
    return pd.read_parquet(folder / "aml.parquet"), pd.read_parquet(folder / "fraud.parquet"), build


def prepare(dataset_dir: Path):
    """Features, labels and (for the training dataset) the split, row-aligned."""
    aml, fraud, build = load_matrices(dataset_dir)
    truth = load_truth(dataset_dir)
    located = locate(truth, fraud)
    labels = {"aml": aml_labels(truth, aml, fraud, located), "fraud": fraud_labels(truth, fraud, located)}
    return {"aml": aml, "fraud": fraud}, labels, truth, located, build


def comparator_scores(model: str, frames: dict[str, pd.DataFrame]) -> dict[str, np.ndarray]:
    """Scores of the non-learned comparators, for any dataset."""
    frame = frames[model]
    rules = comparators.rule_flags(model, frame, OWNS[model])
    out = {"rules": rules.any(axis=1).to_numpy(dtype=float)}
    out["anomaly"] = (comparators.anomaly_score(frames["aml"]) if model == "aml"
                      else comparators.anomaly_score_for_transactions(frames["aml"], frames["fraud"]))
    return out


def scenario_detection(labels: pd.DataFrame, entities: pd.Series, flagged: np.ndarray) -> pd.DataFrame:
    """One row per positive label (scenario, entity): detected if any of its
    units is flagged (TRD §10.6: alerts matching an INJECTED_POSITIVE label)."""
    positive = labels.group.isin(["positive", "other_positive"])
    frame = pd.DataFrame({"scenario_id": labels.scenario_id[positive], "entity": entities[positive],
                          "pattern": labels.pattern[positive], "group": labels.group[positive],
                          "flagged": flagged[positive.to_numpy()]})
    return frame.groupby(["scenario_id", "entity", "pattern", "group"], as_index=False).flagged.max()


def _at_threshold(scores: np.ndarray, y: np.ndarray, threshold: float) -> dict:
    labelled = ~np.isnan(y)
    flagged = np.nan_to_num(scores, nan=-np.inf) >= threshold
    tp = int((flagged & (y == 1)).sum())
    fp = int((flagged & (y == 0)).sum())
    fn = int((~flagged & (y == 1)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": int((labelled & ~flagged & (y == 0)).sum()),
            "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}


def train_model(model: str, dataset_dir: Path, frames, labels, truth, located, build, out_root: Path) -> dict:
    started = time.perf_counter()
    frame, label = frames[model], labels[model]
    columns = list(FEATURES[model])
    entities = sorted(set(frames["fraud"].entity_id))
    split = split_entities(truth, entities, located.participants)
    side = frame.entity_id.map(split).fillna("train").to_numpy()
    y_all = label.y.to_numpy()
    trainable = ~np.isnan(y_all)
    tr, va = trainable & (side == "train"), trainable & (side == "validation")
    X, y = frame.loc[tr, columns], y_all[tr]
    w = pattern_balanced_weights(y, label.pattern[tr].reset_index(drop=True))
    X_val, y_val = frame.loc[va, columns], y_all[va]

    booster = train_gbm(X, y, w, X_val, y_val)
    linear = train_linear(X, y, w)

    # Every scorer is judged, and its threshold chosen, on the same validation rows.
    validation = side == "validation"
    control = (label.group == "control").to_numpy()[validation]
    y_v = y_all[validation]
    X_v = frame.loc[validation, columns]
    scores = {"gbm": gbm_scores(booster, X_v), "linear": linear.scores(X_v)}
    scores.update({k: v[validation] for k, v in comparator_scores(model, frames).items()})
    report = {}
    for name, s in scores.items():
        chosen = {"threshold": 0.5} if name == "rules" else choose_threshold(s, y_v, control)
        flagged = np.nan_to_num(s, nan=-np.inf) >= chosen["threshold"]
        detection = scenario_detection(label[validation], frame.entity_id[validation], flagged)
        owned = detection[detection.group == "positive"]
        report[name] = {
            "threshold": chosen["threshold"],
            **ranking_metrics(s, y_v),
            **_at_threshold(s, y_v, chosen["threshold"]),
            "control_units_flagged": int((flagged & control).sum()),
            "scenario_recall": {p: f"{int(g.flagged.sum())}/{len(g)}" for p, g in owned.groupby("pattern")},
        }

    # Canary: the same model on shuffled labels must find nothing (see models.py).
    shuffled = canary(X, y, X_val, y_val)

    version = MODEL_VERSION[model]
    out = out_root / version
    out.mkdir(parents=True, exist_ok=True)
    model_text = booster.model_to_string()
    (out / "model.txt").write_text(model_text, encoding="utf-8")
    (out / "linear.json").write_text(json.dumps(linear.to_json(), indent=1) + "\n", encoding="utf-8")
    pd.DataFrame({"entity": list(split), "split": list(split.values())}).sort_values("entity").to_csv(
        out / "split.csv", index=False)
    thresholds = {name: r["threshold"] for name, r in report.items()}
    reference_hash = json.loads(REFERENCE_PATH.read_text())["definition_hash"]
    groups = label.group[side == "train"].value_counts().to_dict()
    card = {
        # TRD §10.5 envelope. ANOMALY_MODEL: this model takes the place of TRD
        # C-07, supervised by the mentor's directive (PROJECT_CONTEXT §8).
        "artefact_type": "ANOMALY_MODEL",
        "version": version,
        "definition_hash": definition_hash(model_text, canonical(columns), FEATURE_SET_VERSION, reference_hash,
                                           canonical(thresholds["gbm"])),
        "state": "DRAFT",
        "created_by": "app.ml.train",
        "submitted_by": None,
        "approved_by": None,
        "approved_at": None,
        "simulation_reference": None,
        "effective_from": None,
        "superseded_by": None,
        "caveat": CAVEAT,
        "model": {
            "unit": UNIT[model],
            "algorithm": f"LightGBM {__import__('lightgbm').__version__}, binary, TreeSHAP contributions",
            "owns": list(OWNS[model]),
            "threshold": thresholds["gbm"],
            "threshold_rule": "max F1 on the validation split, flagging no control-clean unit (TRD §10.6)",
            "best_iteration": booster.best_iteration,
            "hyperparameters": {**GBM_PARAMS, "max_rounds": MAX_ROUNDS, "early_stopping": EARLY_STOPPING},
            "weights": "positives weighted so each owned pattern carries equal total weight; negatives 1",
        },
        "feature_set": {"version": FEATURE_SET_VERSION, "features": columns,
                        "parameters": {p.name: p.value for p in PARAMETERS},
                        "mcc_ticket_reference": reference_hash},
        "training_data": {
            **{k: build["dataset"][k] for k in ("generator_version", "profile", "seed", "manifest_sha256")},
            "labels_sha256": hashlib.sha256((dataset_dir / "labels.csv").read_bytes()).hexdigest(),
            "split": {"seed": SPLIT_SEED, "validation_share": VALIDATION_SHARE, "by": "scenario, stratified by label",
                      "train_units": int(tr.sum()), "validation_units": int(va.sum()),
                      "train_positives": int(y.sum()), "validation_positives": int(y_val.sum())},
            "training_units_by_group": {k: int(v) for k, v in groups.items()},
        },
        "validation": report,
        "canary": {"description": f"same model, {CANARY_ROUNDS} rounds, training labels shuffled "
                                  f"{CANARY_SHUFFLES} times; ROC-AUC on validation. A label path other than the "
                                  f"target pulls it far from 0.5 either way; pass: mean in {CANARY_BAND} and the "
                                  f"real model above every shuffle (see models.py)",
                   "validation_positives": int(y_val.sum()), **shuffled,
                   "model_roc_auc": report["gbm"]["roc_auc"],
                   "model_above_every_shuffle": report["gbm"]["roc_auc"] > max(shuffled["roc_auc"]),
                   "passed": shuffled["passed"] and report["gbm"]["roc_auc"] > max(shuffled["roc_auc"])},
        "comparators": {"rules": comparators.RULES_NOTE,
                        "anomaly": "TRD §10.2 composite, ASSUMPTION AS-05 (equal weights, |z| clipped at 5)"},
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.perf_counter() - started, 1),
    }
    (out / "model_card.json").write_text(json.dumps(card, indent=2, default=str) + "\n", encoding="utf-8")
    return card


def load_model(out_root: Path, model: str):
    import lightgbm as lgb

    folder = out_root / MODEL_VERSION[model]
    card = json.loads((folder / "model_card.json").read_text())
    booster = lgb.Booster(model_file=str(folder / "model.txt"))
    linear = LinearModel.from_json(json.loads((folder / "linear.json").read_text()))
    return booster, linear, card


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 2: train the AML and Fraud models.")
    parser.add_argument("dataset", type=Path, help="the training dataset, with its features built")
    parser.add_argument("--out", type=Path, default=Path("ml_models"))
    parser.add_argument("--model", choices=("aml", "fraud"), action="append")
    args = parser.parse_args()
    frames, labels, truth, located, build = prepare(args.dataset)
    for model in args.model or ("aml", "fraud"):
        card = train_model(model, args.dataset, frames, labels, truth, located, build, args.out)
        v = card["validation"]
        print(f"{card['version']}: {card['seconds']}s, best iteration {card['model']['best_iteration']}, "
              f"canary mean ROC-AUC {card['canary']['mean']} (max {max(card['canary']['roc_auc'])}, "
              f"{'passed' if card['canary']['passed'] else 'FAILED'})")
        for name, r in v.items():
            ap = "-" if r["average_precision"] is None else f"{r['average_precision']:.3f}"
            print(f"  {name:8s} AP {ap:>6}  P {r['precision']:.3f}  R {r['recall']:.3f}  F1 {r['f1']:.3f}  "
                  f"control flagged {r['control_units_flagged']}  recall {r['scenario_recall']}")


if __name__ == "__main__":
    main()
