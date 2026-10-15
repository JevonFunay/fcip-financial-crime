"""Serving artefact parity and label-free training, not a fraud-accuracy claim."""

import json

import numpy as np
import pandas as pd
import pytest

from app.ml.behavioral.model import explain_model, fit_model, load_model, predict_model


def _frame():
    f = pd.DataFrame(np.random.default_rng(31).normal(size=(500, 4)), columns=list("abcd"))
    f.loc[:20, "a"] = np.nan
    return f


def test_json_roundtrip_and_label_identifiers_do_not_change_scores():
    f = _frame()
    text, fitted = fit_model(f, list("abcd"), seed=31, estimators=12)
    assert np.allclose(predict_model(text, f), fitted, atol=1e-12, rtol=1e-12)
    contaminated = f.assign(fraud_label=np.arange(len(f)) % 2, AccountID="a-meaningless-renamed-id")
    other, scores = fit_model(contaminated, list("abcd"), seed=31, estimators=12)
    assert other == text
    assert np.array_equal(scores, fitted)


def test_explanation_is_a_reproducible_score_difference_not_a_probability_or_shap():
    f = _frame()
    text, _ = fit_model(f, list("abcd"), seed=32, estimators=10)
    observations = f.iloc[:3]
    details = explain_model(text, observations, top_n=4)
    predictor = load_model(text)
    original = predict_model(text, observations)
    for i, row in enumerate(details):
        for item in row:
            altered = observations.copy()
            index = predictor.features.index(item["feature"])
            altered[item["feature"]] = predictor.medians[index]
            expected = original[i] - predict_model(text, altered)[i]
            assert item["contribution"] == pytest.approx(expected)
    assert details[0][0]["value"] is None or np.isfinite(details[0][0]["value"])


@pytest.mark.parametrize("mutation", ["cycle", "out_of_bounds", "nonfinite", "shared", "bad_medians", "huge_index", "fractional_index"])
def test_unsafe_json_tree_state_is_rejected_before_native_tree_construction(mutation):
    text, _ = fit_model(_frame(), list("abcd"), seed=33, estimators=2)
    payload = json.loads(text)
    tree = payload["trees"][0]
    if mutation == "cycle":
        tree["left"][0] = 0
    elif mutation == "out_of_bounds":
        tree["feature"][0] = 900
    elif mutation == "nonfinite":
        tree["threshold"][0] = float("inf")
    elif mutation == "shared":
        tree["right"][0] = tree["left"][0]
    elif mutation == "huge_index":
        tree["left"][0] = 10**100
    elif mutation == "fractional_index":
        tree["left"][0] = 1.5
    else:
        payload["medians"] = [float("nan")] * 4
    with pytest.raises(ValueError):
        predict_model(json.dumps(payload), _frame())


def test_non_object_json_is_rejected():
    with pytest.raises(ValueError):
        predict_model("[]", _frame())
