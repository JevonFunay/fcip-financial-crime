"""Stage 3: the evaluation harness.

The numbers themselves come from the full test seed (docs/ml/evaluation_v1.md);
these tests pin how they are computed: the interval, that thresholds come from
the model card and are never re-tuned on the data being evaluated, that
false positives are counted apart from positives, and that the report renders.
"""

import shutil
from pathlib import Path

import numpy as np
import pytest

from app.ml.build_features import build
from app.ml.evaluate import _pattern_features, evaluate_model, render, wilson
from app.ml.models import FEATURES
from app.ml.train import prepare, train_model

SMALL = Path(__file__).resolve().parents[1] / "sample_data" / "Small"


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    root = tmp_path_factory.mktemp("evaluation")
    folder = root / "Small"
    shutil.copytree(SMALL, folder)
    build(folder)
    frames, labels, truth, located, report = prepare(folder)
    for model in ("aml", "fraud"):
        train_model(model, folder, frames, labels, truth, located, report, root / "models")
    return {"folder": folder, "models": root / "models", "frames": frames, "labels": labels, "truth": truth,
            "build": report}


def test_the_wilson_interval():
    low, high = wilson(9, 10)
    assert low == pytest.approx(0.596, abs=0.001) and high == pytest.approx(0.982, abs=0.001)
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == 1.0


def test_thresholds_come_from_the_card_not_the_evaluated_data(trained):
    import json

    card = json.loads((trained["models"] / "fraud_gbm_v1" / "model_card.json").read_text())
    result = evaluate_model("fraud", trained["folder"], trained["models"], trained["frames"], trained["labels"],
                            trained["truth"])
    for name, scorer in result["scorers"].items():
        assert scorer["threshold"] == card["validation"][name]["threshold"]


def test_false_positives_are_counted_apart_from_positives(trained):
    result = evaluate_model("aml", trained["folder"], trained["models"], trained["frames"], trained["labels"],
                            trained["truth"])
    gbm = result["scorers"]["gbm"]
    look_units = int((trained["labels"]["aml"].group == "lookalike").sum())
    assert gbm["fp"] <= look_units + gbm["background"]["units"] + result["units_by_group"].get("control", 0)
    assert set(gbm["recall_by_pattern"]) <= {"P01", "P02", "P03", "P06", "P07", "P08", "P09", "P10", "P11"}
    assert "control" in gbm and "entities_flagged" in gbm["control"]


def test_the_report_renders_with_the_synthetic_data_caveat(trained):
    report = {"test_data": {k: trained["build"]["dataset"][k] for k in ("generator_version", "profile", "seed")},
              "train_seed": 20260923}
    for model in ("aml", "fraud"):
        report[model] = evaluate_model(model, trained["folder"], trained["models"], trained["frames"],
                                       trained["labels"], trained["truth"])
    text = render(report)
    assert "Synthetic data only" in text and "## AML model" in text and "## FRAUD model" in text


def test_ablation_removes_exactly_the_features_tagged_with_the_pattern():
    removed = _pattern_features("P02", list(FEATURES["aml"]))
    assert removed and all(c.startswith(("P02_", "TXN_VALUE_SUM_R7D", "DISTINCT_ACCOUNTS")) for c in removed)
    assert "P02_BAND_COUNT_ROLLING_7D" in removed and "TXN_COUNT_R7D" not in removed
    assert np.all([c in FEATURES["fraud"] for c in _pattern_features("ATO", list(FEATURES["fraud"]))])
