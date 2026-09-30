"""The two supervised models and the pieces training and evaluation share.

- **Split**: 20% of the training dataset's entities are held out for early
  stopping and choosing thresholds; the test dataset is only touched in stage
  3. The split is by scenario (a ring's members stay on one side) and
  stratified by label, deterministic from SPLIT_SEED.
- **LightGBM** (the model): feature contributions come from TreeSHAP
  (`pred_contrib`), exact and additive on the log-odds scale (ADR-007,
  NFR-14). Stored as LightGBM's text format, never pickled.
- **Logistic regression** (a comparator): signed-log, standardised features
  with missing indicators; contribution = coefficient x standardised value.
  Stored as JSON.
- **Canary**: the same model trained on shuffled labels, ten times. A path
  from the true labels into training other than the target (weights,
  ordering, a split that tells) pulls the shuffled models' ROC-AUC far from
  0.5, in either direction: weights computed from the true labels, for one,
  teach the model that true-positive regions are negative (AUC ~0.1). It
  cannot catch a leaking *feature* (shuffling breaks that link too); the
  loader's allow-list tests do. Without leakage the AUC still sits a little
  *below* 0.5 (0.41-0.43 at full): true positives live in a few sparse
  regions, where a leaf of 50 rows at a 1% rate usually draws no random
  positive and is pushed down. Pass: the mean of the ten within
  CANARY_BAND, and (in training) the real model above every shuffle, a
  permutation test at 1/11. Single shuffles vary a lot; on Small, with a
  handful of validation positives, the check is noise.
- **Threshold**: the one maximising F1 on the validation split among those
  that flag no control-clean entity there (TRD §10.6, T-DET-CONTROL-01: a
  single alert on the control cohort fails).
- **Weights**: every owned pattern carries the same total weight among the
  positives, so P04's thousands of burst rows do not drown P05's forty
  reactivations.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.ml.feature_set import AML_FEATURES, FRAUD_FEATURES
from app.ml.labels import Truth

SPLIT_SEED = 20260930
VALIDATION_SHARE = 0.20
FEATURES = {"aml": AML_FEATURES, "fraud": FRAUD_FEATURES}
CATEGORICAL = ("CHANNEL_CODE", "TXN_TYPE_CODE")  # codes, not quantities (one-hot for the linear model)
LINEAR_CLIP = 10.0  # standardised inputs clipped: a rare feature's few non-zero rows sit 40+ sd out

GBM_PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "metric": "average_precision",
    "seed": SPLIT_SEED,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 4,
    "verbose": -1,
}
MAX_ROUNDS = 2000
EARLY_STOPPING = 100
CANARY_SHUFFLES = 10
CANARY_ROUNDS = 50
CANARY_BAND = (0.30, 0.70)


# --- split -----------------------------------------------------------------------------------


def _unit_hash(key: str) -> str:
    return hashlib.sha256(f"{SPLIT_SEED}:{key}".encode()).hexdigest()


def split_entities(truth: Truth, entities: list[str], participants: set[str]) -> dict[str, str]:
    """entity -> "train" or "validation"."""
    group_of: dict[str, str] = {}
    stratum_of: dict[str, str] = {}
    for (scenario_id, entity), label in truth.labels.items():
        group_of[entity] = scenario_id
        stratum_of[scenario_id] = f"{label.kind}:{label.pattern}"
    groups: dict[str, set[str]] = {}
    for entity in entities:
        key = group_of.get(entity, entity)
        if key not in stratum_of:
            stratum_of[key] = ("control" if entity in truth.control
                               else "participant" if entity in participants else "background")
        groups.setdefault(key, set()).add(entity)
    by_stratum: dict[str, list[str]] = {}
    for key in groups:
        by_stratum.setdefault(stratum_of[key], []).append(key)
    split: dict[str, str] = {}
    for keys in by_stratum.values():
        keys.sort(key=_unit_hash)
        held_out = set(keys[:round(len(keys) * VALIDATION_SHARE)])
        for key in keys:
            for entity in groups[key]:
                split[entity] = "validation" if key in held_out else "train"
    return split


def pattern_balanced_weights(y: np.ndarray, pattern: pd.Series) -> np.ndarray:
    weights = np.ones(len(y))
    positive = y == 1
    counts = pattern[positive].value_counts()
    if len(counts):
        for code, count in counts.items():
            weights[positive & (pattern == code).to_numpy()] = positive.sum() / (len(counts) * count)
    return weights


# --- LightGBM --------------------------------------------------------------------------------


def train_gbm(X: pd.DataFrame, y: np.ndarray, w: np.ndarray, X_val: pd.DataFrame, y_val: np.ndarray,
              rounds: int | None = None):
    """Early stopping on validation average precision, unless `rounds` is fixed."""
    import lightgbm as lgb

    train_set = lgb.Dataset(X, label=y, weight=w, free_raw_data=False)
    if rounds is not None:
        return lgb.train(GBM_PARAMS, train_set, num_boost_round=rounds)
    valid_set = lgb.Dataset(X_val, label=y_val, reference=train_set, free_raw_data=False)
    return lgb.train(GBM_PARAMS, train_set, num_boost_round=MAX_ROUNDS, valid_sets=[valid_set],
                     callbacks=[lgb.early_stopping(EARLY_STOPPING, verbose=False)])


def canary(X: pd.DataFrame, y: np.ndarray, X_val: pd.DataFrame, y_val: np.ndarray,
           weights: np.ndarray | None = None) -> dict:
    aucs = []
    for shuffle in range(CANARY_SHUFFLES):
        shuffled = np.random.default_rng(SPLIT_SEED + shuffle).permutation(y)
        model = train_gbm(X, shuffled, np.ones(len(y)) if weights is None else weights, X_val, y_val,
                          rounds=CANARY_ROUNDS)
        aucs.append(round(ranking_metrics(gbm_scores(model, X_val), y_val)["roc_auc"], 3))
    mean = round(float(np.mean(aucs)), 3)
    return {"roc_auc": aucs, "mean": mean, "passed": CANARY_BAND[0] <= mean <= CANARY_BAND[1]}


def gbm_scores(booster, X: pd.DataFrame) -> np.ndarray:
    return booster.predict(X, num_iteration=booster.best_iteration or None)


def gbm_contributions(booster, X: pd.DataFrame) -> np.ndarray:
    """TreeSHAP on the log-odds scale: one column per feature plus the bias;
    each row sums to the model's raw score."""
    return booster.predict(X, num_iteration=booster.best_iteration or None, pred_contrib=True)


# --- logistic regression (comparator) --------------------------------------------------------


@dataclass
class LinearModel:
    columns: list[str]
    categories: dict[str, list[float]]
    means: dict[str, float]
    stds: dict[str, float]
    design: list[str]
    coef: list[float]
    intercept: float

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        blocks = []
        for column in self.columns:
            values = frame[column].to_numpy(dtype=float)
            if column in self.categories:
                blocks += [(values == c).astype(float) for c in self.categories[column]]
                continue
            missing = np.isnan(values)
            signed = np.sign(values) * np.log1p(np.abs(values))
            standard = (signed - self.means[column]) / self.stds[column]
            blocks.append(np.where(missing, 0.0, np.clip(standard, -LINEAR_CLIP, LINEAR_CLIP)))
            blocks.append(missing.astype(float))
        return np.column_stack(blocks)

    def scores(self, frame: pd.DataFrame) -> np.ndarray:
        z = self.transform(frame) @ np.array(self.coef) + self.intercept
        return 1.0 / (1.0 + np.exp(-z))

    def contributions(self, frame: pd.DataFrame) -> np.ndarray:
        return self.transform(frame) * np.array(self.coef)

    def to_json(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_json(cls, data: dict) -> "LinearModel":
        return cls(**data)


def train_linear(X: pd.DataFrame, y: np.ndarray, w: np.ndarray) -> LinearModel:
    from sklearn.linear_model import LogisticRegression

    columns = list(X.columns)
    categories, means, stds, design = {}, {}, {}, []
    for column in columns:
        values = X[column].to_numpy(dtype=float)
        if column in CATEGORICAL:
            categories[column] = sorted(float(v) for v in np.unique(values[~np.isnan(values)]))
            design += [f"{column}={c:g}" for c in categories[column]]
            continue
        signed = np.sign(values) * np.log1p(np.abs(values))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            mean, std = float(np.nanmean(signed)), float(np.nanstd(signed))
        means[column] = mean if np.isfinite(mean) else 0.0
        stds[column] = std if np.isfinite(std) and std > 0 else 1.0
        design += [column, f"{column}_MISSING"]
    model = LinearModel(columns, categories, means, stds, design, [], 0.0)
    fitted = LogisticRegression(C=1.0, max_iter=5000).fit(model.transform(X), y, sample_weight=w)
    model.coef = [float(c) for c in fitted.coef_[0]]
    model.intercept = float(fitted.intercept_[0])
    return model


# --- thresholds and metrics ------------------------------------------------------------------


def choose_threshold(scores: np.ndarray, y: np.ndarray, control: np.ndarray) -> dict:
    """Max F1 among thresholds that flag no control-clean unit. Units with a
    NaN score (no baseline for the anomaly comparator) never flag."""
    from sklearn.metrics import precision_recall_curve

    known = ~np.isnan(scores)
    labelled = known & ~np.isnan(y)
    floor = float(np.nanmax(scores[control & known])) if (control & known).any() else -np.inf
    precision, recall, thresholds = precision_recall_curve(y[labelled], scores[labelled])
    best = {"threshold": float(np.nextafter(floor, np.inf)) if np.isfinite(floor) else 0.5, "f1": 0.0,
            "precision": 0.0, "recall": 0.0, "control_floor": floor}
    for p, r, t in zip(precision[:-1], recall[:-1], thresholds):
        if t <= floor:
            continue
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        if f1 > best["f1"]:
            best.update(threshold=float(t), f1=float(f1), precision=float(p), recall=float(r))
    return best


def ranking_metrics(scores: np.ndarray, y: np.ndarray) -> dict:
    from sklearn.metrics import average_precision_score, roc_auc_score

    labelled = ~np.isnan(y)
    s = np.where(np.isnan(scores), -1.0, scores)[labelled]  # no score ranks last
    t = y[labelled]
    if t.min() == t.max():
        return {"average_precision": None, "roc_auc": None}
    return {"average_precision": float(average_precision_score(t, s)), "roc_auc": float(roc_auc_score(t, s))}


def definition_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode())
        digest.update(b"\x00")
    return digest.hexdigest()


def canonical(data) -> str:
    return json.dumps(data, sort_keys=True, default=str)
