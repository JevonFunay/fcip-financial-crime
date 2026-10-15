"""Unsupervised Isolation Forest, stored as validated JSON rather than pickle.

Scores are isolation scores (higher means more isolated), not probabilities of
fraud. The scorer reconstructs only numeric sklearn tree state after checking
every index. Training, offline evaluation and the app share this implementation.
Feature explanations are median-replacement score differences, not TreeSHAP,
not additive, and not a causal account of an observed transaction.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.tree import _tree

FORMAT = "fcip_isolation_forest_json_v1"
ARTEFACT_TYPE = "ISOLATION_FOREST_JSON"
EXPLANATION_METHOD = "median_replacement_score_difference"
DEFAULT_THRESHOLD = 0.5  # IF auto reference; FCIP flags inclusively >=, not fraud prevalence.


def _path_adjustment(count: np.ndarray) -> np.ndarray:
    count = np.asarray(count, dtype=float)
    out = np.zeros_like(count)
    out[count == 2] = 1.0
    mask = count > 2
    out[mask] = 2 * (np.log(count[mask] - 1) + np.euler_gamma) - 2 * (count[mask] - 1) / count[mask]
    return out


def _numeric(frame: pd.DataFrame, features: list[str]) -> np.ndarray:
    missing = set(features) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing model features: {sorted(missing)}")
    array = frame.loc[:, features].to_numpy(dtype=float)
    if np.isinf(array).any():
        raise ValueError("Infinite feature values are not valid model input")
    return array


def _transform(array: np.ndarray, medians: np.ndarray) -> np.ndarray:
    # Monotone signed-log limits numeric range without clipping a genuine
    # extreme into an ordinary value. Missingness has explicit feature flags.
    filled = np.where(np.isnan(array), medians, array)
    return (np.sign(filled) * np.log1p(np.abs(filled))).astype(np.float32)


def fit_model(frame: pd.DataFrame, features: list[str], *, seed: int,
              estimators: int = 100, max_samples: int = 256) -> tuple[str, np.ndarray]:
    """Fit without accepting a target, sidecar or validation frame."""
    if len(frame) < 2 or not features or len(features) != len(set(features)):
        raise ValueError("Training requires two rows and unique numeric feature names")
    array = _numeric(frame, features)
    medians = np.array([float(np.nanmedian(c)) if np.isfinite(c).any() else 0.0 for c in array.T])
    X = _transform(array, medians)
    forest = IsolationForest(n_estimators=estimators, max_samples=max_samples,
                             contamination="auto", max_features=1.0,
                             random_state=seed, n_jobs=4)
    forest.fit(X)
    trees = []
    for estimator, mapping in zip(forest.estimators_, forest.estimators_features_):
        t = estimator.tree_
        global_features = np.where(t.feature >= 0, mapping[np.maximum(t.feature, 0)], -2)
        trees.append({"left": t.children_left.tolist(), "right": t.children_right.tolist(),
                      "feature": global_features.tolist(), "threshold": t.threshold.tolist(),
                      "samples": t.n_node_samples.tolist()})
    payload = {"format": FORMAT, "features": features, "medians": medians.tolist(),
               "preprocessing": "train-only median imputation; signed log1p; explicit missing flags",
               "max_samples": int(forest.max_samples_), "estimators": estimators,
               "seed": seed, "sklearn_version": __import__("sklearn").__version__, "trees": trees}
    model_text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    # A serialization check against the actual fitting implementation; fail
    # before publishing if scoring on the stored artefact would differ.
    check = min(4096, len(frame))
    expected = -forest.score_samples(X[:check])
    actual = predict_model(model_text, frame.iloc[:check])
    if not np.allclose(expected, actual, atol=1e-12, rtol=1e-12):
        raise ValueError("JSON scorer differs from sklearn IsolationForest")
    return model_text, -forest.score_samples(X)


class Predictor:
    def __init__(self, payload: dict[str, Any]):
        if not isinstance(payload, dict) or payload.get("format") != FORMAT:
            raise ValueError("Unsupported isolation model format")
        features = payload.get("features")
        if (not isinstance(features, list) or not features or len(features) > 256
                or any(not isinstance(x, str) for x in features) or len(features) != len(set(features))):
            raise ValueError("Invalid model feature registry")
        self.features = features
        try:
            self.medians = np.array(payload.get("medians"), dtype=float)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Invalid numeric training medians") from exc
        if self.medians.shape != (len(features),) or not np.isfinite(self.medians).all():
            raise ValueError("Invalid training medians")
        max_samples = payload.get("max_samples")
        if not isinstance(max_samples, int) or not 2 <= max_samples <= 1_000_000:
            raise ValueError("Invalid training sample size")
        trees = payload.get("trees")
        if not isinstance(trees, list) or not 1 <= len(trees) <= 512:
            raise ValueError("Invalid forest size")
        self.denominator = len(trees) * float(_path_adjustment(np.array([max_samples]))[0])
        self.trees = []
        for data in trees:
            names = ("left", "right", "feature", "threshold", "samples")
            if not isinstance(data, dict) or any(not isinstance(data.get(k), list) for k in names):
                raise ValueError("Invalid tree structure")
            n = len(data["left"])
            if not 1 <= n <= 100_000 or any(len(data[k]) != n for k in names):
                raise ValueError("Invalid tree lengths")
            for key in ("left", "right", "feature", "samples"):
                if any(type(value) is not int or not -2 <= value <= 1_000_000 for value in data[key]):
                    raise ValueError("Tree indices and sample counts must be bounded exact integers")
            left, right, feature, samples = [np.asarray(data[k], dtype=np.int64) for k in
                                              ("left", "right", "feature", "samples")]
            try:
                threshold = np.asarray(data["threshold"], dtype=float)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Invalid numeric tree thresholds") from exc
            if not np.isfinite(threshold).all() or np.any(samples < 1):
                raise ValueError("Invalid tree values")
            leaf = (left == -1) & (right == -1)
            split = ~leaf
            idx = np.arange(n)
            if (np.any(left[split] <= idx[split]) or np.any(right[split] <= idx[split])
                    or np.any(left[split] >= n) or np.any(right[split] >= n)
                    or np.any(feature[split] < 0) or np.any(feature[split] >= len(features))):
                raise ValueError("Tree has an invalid child/feature or cycle")
            depth = np.full(n, -1, dtype=np.int64)
            depth[0] = 0
            for i in range(n):
                if depth[i] < 0:
                    raise ValueError("Tree has unreachable nodes")
                if not leaf[i]:
                    for child in (left[i], right[i]):
                        if depth[child] >= 0:
                            raise ValueError("Tree contains a shared child")
                        depth[child] = depth[i] + 1
            nodes = np.zeros(n, dtype=_tree.NODE_DTYPE)
            nodes["left_child"], nodes["right_child"] = left, right
            nodes["feature"], nodes["threshold"] = feature, threshold
            nodes["n_node_samples"], nodes["weighted_n_node_samples"] = samples, samples
            compiled = _tree.Tree(len(features), np.array([1], dtype=np.intp), 1)
            compiled.__setstate__({"max_depth": int(depth.max()), "node_count": n,
                                   "nodes": nodes, "values": np.zeros((n, 1, 1), dtype=float)})
            self.trees.append((compiled, depth + _path_adjustment(samples)))

    def scores(self, frame: pd.DataFrame) -> np.ndarray:
        X = _transform(_numeric(frame, self.features), self.medians)
        if not len(X):
            return np.zeros(0, dtype=float)
        depths = np.zeros(len(X), dtype=float)
        for tree, adjustment in self.trees:
            depths += adjustment[tree.apply(X)]
        return np.power(2.0, -depths / self.denominator)


@lru_cache(maxsize=4)
def load_model(model_text: str) -> Predictor:
    if len(model_text) > 20_000_000:
        raise ValueError("Model artefact exceeds the supported size")
    return Predictor(json.loads(model_text))


def predict_model(model_text: str, frame: pd.DataFrame) -> np.ndarray:
    return load_model(model_text).scores(frame)


def explain_model(model_text: str, frame: pd.DataFrame, top_n: int = 8) -> list[list[dict]]:
    predictor = load_model(model_text)
    frame = frame.loc[:, predictor.features].copy()
    base = predictor.scores(frame)
    contributions = np.zeros((len(frame), len(predictor.features)))
    for i, name in enumerate(predictor.features):
        original = frame[name].copy()
        frame[name] = predictor.medians[i]
        contributions[:, i] = base - predictor.scores(frame)
        frame[name] = original
    values = frame.to_numpy(dtype=float)
    out = []
    for r in range(len(frame)):
        order = np.argsort(-np.abs(contributions[r]))[:top_n]
        out.append([{"feature": predictor.features[i],
                     "value": float(values[r, i]) if math.isfinite(values[r, i]) else None,
                     "contribution": float(contributions[r, i])} for i in order])
    return out
