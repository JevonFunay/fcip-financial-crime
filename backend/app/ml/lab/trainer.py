"""Train, score and compare the lab's candidate models on one mapped dataset.

Protocol (fixed in this prototype, recorded in every result):

- Chronological split: the earliest `train_share` of rows train, the next half
  of the remainder validates, the latest rows test. Later data never trains a
  model that is judged on earlier data.
- Supervised models see labels from the training rows only; LightGBM's early
  stopping and every model's default threshold use the validation rows.
- Isolation Forest never sees a label: labels are used only to pick its default
  threshold on validation and to measure it, like the others.
- Test rows are scored once, after everything is fixed. The threshold slider in
  the UI shows validation figures; test figures are read for the chosen
  threshold only when a model is saved.

Artefacts are text (JSON / LightGBM text), never pickle, like the rest of FCIP.
"""

from __future__ import annotations

import json
import math
import time
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.ml.behavioral.model import fit_model, predict_model
from app.ml.lab.features import build_matrix, definition, selected_features

SEED = 20261026
MODELS: dict[str, dict] = {
    "logistic_regression": {
        "name": "Logistic Regression", "kind": "supervised", "artefact_type": "LAB_LOGISTIC_JSON",
        "description": "Model linier sederhana dan mudah dijelaskan: setiap fitur punya satu bobot.",
        "score_kind": "supervised_probability", "default": True,
    },
    "lightgbm": {
        "name": "LightGBM", "kind": "supervised", "artefact_type": "LAB_LIGHTGBM_TEXT",
        "description": "Gradient boosting pohon keputusan; biasanya paling akurat untuk data tabular.",
        "score_kind": "supervised_probability", "default": True,
    },
    "isolation_forest": {
        "name": "Isolation Forest", "kind": "unsupervised", "artefact_type": "LAB_ISOLATION_FOREST_JSON",
        "description": "Tanpa label: menandai transaksi yang paling tidak biasa (pendekatan model FCIP saat ini).",
        "score_kind": "isolation_score", "default": True,
    },
}
LOGISTIC_FORMAT = "fcip_lab_logistic_json_v1"
LIGHTGBM_PARAMS = {
    "objective": "binary", "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 40,
    "feature_fraction": 0.9, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
    "metric": "average_precision", "verbosity": -1, "deterministic": True, "force_col_wise": True,
    "num_threads": 4,
}
LIGHTGBM_MAX_ROUNDS = 400
LIGHTGBM_EARLY_STOPPING = 40


class LabTrainingError(ValueError):
    """The run cannot be trained as configured; the message is shown to the user."""


@dataclass
class TrainedModel:
    key: str
    artefact_type: str
    model_text: str
    features: list[str]
    thresholds: list[float]
    test_curve: list[dict]
    default_index: int


@dataclass
class TrainingOutcome:
    results: dict
    models: list[TrainedModel]


# --- splitting ---------------------------------------------------------------------------------

def chronological_split(frame: pd.DataFrame, train_share: float) -> np.ndarray:
    if not 0.5 <= train_share <= 0.8:
        raise LabTrainingError("Porsi data latih harus antara 50% dan 80%.")
    order = np.lexsort((frame.transaction_ref.to_numpy().astype(str), frame.transaction_date.astype("int64").to_numpy()))
    n = len(frame)
    n_train = int(round(n * train_share))
    n_val = int(round((n - n_train) / 2))
    part = np.empty(n, dtype=object)
    part[order[:n_train]] = "train"
    part[order[n_train:n_train + n_val]] = "validation"
    part[order[n_train + n_val:]] = "test"
    return part


def _split_summary(frame: pd.DataFrame, y: np.ndarray, mask: np.ndarray) -> dict:
    dates = frame.transaction_date[mask]
    return {"rows": int(mask.sum()), "positives": int(y[mask].sum()),
            "date_from": dates.min().isoformat() if len(dates) else None,
            "date_to": dates.max().isoformat() if len(dates) else None}


# --- models ------------------------------------------------------------------------------------

def _signed_log(a: np.ndarray) -> np.ndarray:
    return np.sign(a) * np.log1p(np.abs(a))


def train_logistic(X: pd.DataFrame, y: np.ndarray, seed: int = SEED) -> str:
    from sklearn.linear_model import LogisticRegression

    array = X.to_numpy(dtype=float)
    medians = np.array([float(np.nanmedian(c)) if np.isfinite(c).any() else 0.0 for c in array.T])
    t = _signed_log(np.where(np.isnan(array), medians, array))
    means, scales = t.mean(axis=0), t.std(axis=0)
    scales[scales == 0] = 1.0
    fitted = LogisticRegression(C=1.0, class_weight="balanced", max_iter=3000, random_state=seed)
    with warnings.catch_warnings():
        # sklearn 1.5.2 passes an option newer SciPy no longer knows; harmless.
        warnings.filterwarnings("ignore", message="Unknown solver options")
        fitted.fit((t - means) / scales, y)
    payload = {"format": LOGISTIC_FORMAT, "features": list(X.columns), "medians": medians.tolist(),
               "means": means.tolist(), "scales": scales.tolist(), "coef": fitted.coef_[0].tolist(),
               "intercept": float(fitted.intercept_[0]),
               "preprocessing": "train-only median imputation; signed log1p; train-only standardisation",
               "class_weight": "balanced", "C": 1.0, "seed": seed}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def score_logistic(model_text: str, X: pd.DataFrame) -> np.ndarray:
    m = json.loads(model_text)
    if m.get("format") != LOGISTIC_FORMAT:
        raise ValueError("Unsupported logistic model format")
    array = X.loc[:, m["features"]].to_numpy(dtype=float)
    medians, means, scales = (np.asarray(m[k], dtype=float) for k in ("medians", "means", "scales"))
    z = (_signed_log(np.where(np.isnan(array), medians, array)) - means) / scales
    return 1.0 / (1.0 + np.exp(-(z @ np.asarray(m["coef"], dtype=float) + m["intercept"])))


def train_lightgbm(X: pd.DataFrame, y: np.ndarray, X_val: pd.DataFrame, y_val: np.ndarray, seed: int = SEED) -> str:
    import lightgbm as lgb

    positives = float(y.sum())
    params = {**LIGHTGBM_PARAMS, "seed": seed, "scale_pos_weight": (len(y) - positives) / max(positives, 1.0)}
    train_set = lgb.Dataset(X, label=y, free_raw_data=False)
    valid_set = lgb.Dataset(X_val, label=y_val, reference=train_set, free_raw_data=False)
    booster = lgb.train(params, train_set, num_boost_round=LIGHTGBM_MAX_ROUNDS, valid_sets=[valid_set],
                        callbacks=[lgb.early_stopping(LIGHTGBM_EARLY_STOPPING, verbose=False)])
    return booster.model_to_string(num_iteration=booster.best_iteration or None)


def score_lightgbm(model_text: str, X: pd.DataFrame) -> np.ndarray:
    import lightgbm as lgb

    booster = lgb.Booster(model_str=model_text)
    return booster.predict(X.loc[:, booster.feature_name()])


def score(model_key: str, model_text: str, X: pd.DataFrame) -> np.ndarray:
    if model_key == "logistic_regression":
        return score_logistic(model_text, X)
    if model_key == "lightgbm":
        return score_lightgbm(model_text, X)
    if model_key == "isolation_forest":
        return predict_model(model_text, X)
    raise ValueError(f"Unknown model {model_key}")


def _importance(model_key: str, model_text: str, top: int = 8) -> list[dict]:
    if model_key == "logistic_regression":
        m = json.loads(model_text)
        weights = np.asarray(m["coef"], dtype=float)
        order = np.argsort(-np.abs(weights))[:top]
        total = float(np.abs(weights).sum()) or 1.0
        return [{"feature": m["features"][i], "share": float(abs(weights[i]) / total),
                 "direction": "naik" if weights[i] > 0 else "turun", "definition": definition(m["features"][i])}
                for i in order]
    if model_key == "lightgbm":
        import lightgbm as lgb

        booster = lgb.Booster(model_str=model_text)
        gain = booster.feature_importance(importance_type="gain")
        names = booster.feature_name()
        total = float(gain.sum()) or 1.0
        order = np.argsort(-gain)[:top]
        return [{"feature": names[i], "share": float(gain[i] / total), "direction": None,
                 "definition": definition(names[i])} for i in order if gain[i] > 0]
    return []  # Isolation Forest: no label-based importance to report


# --- metrics -----------------------------------------------------------------------------------

def point(scores: np.ndarray, y: np.ndarray, threshold: float) -> dict:
    flagged = scores >= threshold
    tp = int((flagged & (y == 1)).sum())
    fp = int((flagged & (y == 0)).sum())
    fn = int((~flagged & (y == 1)).sum())
    tn = int((~flagged & (y == 0)).sum())
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else 0.0
    return {"threshold": float(threshold), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "flagged": tp + fp,
            "precision": precision, "recall": recall, "f1": f1,
            "flagged_per_10k": float(10_000 * (tp + fp) / len(y)) if len(y) else 0.0}


def ranking(scores: np.ndarray, y: np.ndarray) -> dict:
    from sklearn.metrics import average_precision_score, roc_auc_score

    if y.min() == y.max():
        return {"average_precision": None, "roc_auc": None}
    return {"average_precision": float(average_precision_score(y, scores)), "roc_auc": float(roc_auc_score(y, scores))}


def best_f1_threshold(scores: np.ndarray, y: np.ndarray) -> float:
    from sklearn.metrics import precision_recall_curve

    precision, recall, thresholds = precision_recall_curve(y, scores)
    precision, recall = precision[:-1], recall[:-1]
    denominator = precision + recall
    f1 = np.divide(2 * precision * recall, denominator, out=np.zeros_like(denominator), where=denominator > 0)
    return float(thresholds[int(np.argmax(f1))])


def candidate_thresholds(val_scores: np.ndarray, default: float) -> np.ndarray:
    levels = np.unique(np.concatenate([np.linspace(0.0, 0.9, 19), np.linspace(0.9, 0.99, 19),
                                       1 - np.logspace(-2, -4, 21)]))
    return np.unique(np.concatenate([np.quantile(val_scores, levels), [default]]))


# --- the run -----------------------------------------------------------------------------------

def train_run(frame: pd.DataFrame, y: np.ndarray, categories: pd.Series | None, *, feature_groups: list[str],
              model_keys: list[str], train_share: float, seed: int = SEED) -> TrainingOutcome:
    started = time.perf_counter()
    if not model_keys:
        raise LabTrainingError("Pilih minimal satu model.")
    unknown = set(model_keys) - set(MODELS)
    if unknown:
        raise LabTrainingError(f"Model tidak dikenal: {', '.join(sorted(unknown))}")
    try:
        requested = selected_features(feature_groups)
    except ValueError as exc:
        raise LabTrainingError(str(exc)) from exc
    if not requested:
        raise LabTrainingError("Pilih minimal satu kelompok fitur.")

    part = chronological_split(frame, train_share)
    masks = {name: part == name for name in ("train", "validation", "test")}
    for name, label in (("train", "latih"), ("validation", "validasi"), ("test", "uji")):
        positives = int(y[masks[name]].sum())
        if positives == 0 or positives == int(masks[name].sum()):
            raise LabTrainingError(f"Data {label} harus berisi baris positif dan negatif; ubah porsi data latih "
                                   f"atau gunakan dataset dengan rentang waktu label yang lebih luas.")

    feature_started = time.perf_counter()
    matrix, vocabulary = build_matrix(frame, masks["train"])
    feature_seconds = time.perf_counter() - feature_started
    train_matrix = matrix.loc[masks["train"], requested]
    unusable = [c for c in requested if train_matrix[c].isna().all()
                or (train_matrix[c].notna().all() and train_matrix[c].nunique() <= 1)]
    features = [c for c in requested if c not in unusable]
    if not features:
        raise LabTrainingError("Semua fitur yang dipilih kosong atau konstan di dataset ini; pilih kelompok fitur lain.")

    X = {name: matrix.loc[mask, features] for name, mask in masks.items()}
    Y = {name: y[mask] for name, mask in masks.items()}
    test_categories = categories[masks["test"]].to_numpy() if categories is not None else None

    trained: list[TrainedModel] = []
    summaries: list[dict] = []
    for key in model_keys:
        spec = MODELS[key]
        model_started = time.perf_counter()
        if key == "logistic_regression":
            model_text = train_logistic(X["train"], Y["train"], seed)
        elif key == "lightgbm":
            model_text = train_lightgbm(X["train"], Y["train"], X["validation"], Y["validation"], seed)
        else:
            model_text, _ = fit_model(X["train"], features, seed=seed)
        val_scores = score(key, model_text, X["validation"])
        test_scores = score(key, model_text, X["test"])
        default = best_f1_threshold(val_scores, Y["validation"])
        thresholds = candidate_thresholds(val_scores, default)
        default_index = int(np.flatnonzero(thresholds == default)[0])
        validation_curve = [point(val_scores, Y["validation"], t) for t in thresholds]
        test_curve = [point(test_scores, Y["test"], t) for t in thresholds]
        test_at_default = {**test_curve[default_index], **ranking(test_scores, Y["test"])}
        by_category = None
        if test_categories is not None:
            flagged = test_scores >= default
            by_category = []
            for value in sorted({c for c in test_categories[Y["test"] == 1]}):
                rows = (Y["test"] == 1) & (test_categories == value)
                by_category.append({"category": value or "(kosong)", "positives": int(rows.sum()),
                                    "caught": int((rows & flagged).sum())})
        summaries.append({
            "key": key, "name": spec["name"], "kind": spec["kind"], "description": spec["description"],
            "seconds": round(time.perf_counter() - model_started, 2),
            "default_index": default_index,
            "validation_ranking": ranking(val_scores, Y["validation"]),
            "validation_curve": validation_curve,
            "test": test_at_default,
            "test_by_category": by_category,
            "top_features": _importance(key, model_text),
        })
        trained.append(TrainedModel(key, spec["artefact_type"], model_text, features,
                                    [float(t) for t in thresholds], test_curve, default_index))

    best = max(summaries, key=lambda s: (s["validation_ranking"]["average_precision"] or -1.0))["key"]
    results = {
        "protocol": {
            "split": "chronological", "train_share": train_share,
            "validation_share": round((1 - train_share) / 2, 4), "test_share": round((1 - train_share) / 2, 4),
            "default_threshold": "max F1 on validation", "best_model_rule": "highest validation average precision",
            "seed": seed, "unit": "FRAUD: one row per transaction, features as of that transaction",
        },
        "split": {name: _split_summary(frame, y, mask) for name, mask in masks.items()},
        "features": {"requested_groups": feature_groups, "used": features, "unusable": unusable,
                     "definitions": {f: definition(f) for f in features}, "feature_seconds": round(feature_seconds, 2),
                     "vocabulary": vocabulary},
        "positive_rate_test": float(Y["test"].mean()),
        "models": summaries,
        "best_model": best,
        "seconds": round(time.perf_counter() - started, 2),
    }
    for model in trained:
        model.thresholds = [t if math.isfinite(t) else 0.0 for t in model.thresholds]
    return TrainingOutcome(results=results, models=trained)
