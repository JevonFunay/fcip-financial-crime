"""Stage 2: labels, split, training and the model card.

What must hold for the trained models to mean anything:
- a positive is the labelled entity's own scenario activity inside its label
  window, nothing else; entities that took part without the label, P12, the
  other model's patterns and the aftermath of a scenario are left out
- the validation split keeps a scenario on one side and never shares an entity
- training is reproducible; contributions add up to the score
- a model trained on shuffled labels finds nothing
- the model card carries the TRD §10.5 envelope and its provenance
"""

import json
import shutil
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml import comparators
from app.ml.build_features import build
from app.ml.feature_set import FEATURE_SET_VERSION, JAKARTA
from app.ml.labels import FRAUD_OWNS, Label, Truth, aml_labels, fraud_labels, load_truth, locate
from app.ml.models import (
    canary,
    choose_threshold,
    gbm_contributions,
    gbm_scores,
    pattern_balanced_weights,
    split_entities,
    train_gbm,
    train_linear,
    LinearModel,
)
from app.ml.train import load_model, prepare, train_model

SMALL = Path(__file__).resolve().parents[1] / "sample_data" / "Small"
ENVELOPE = ("artefact_type", "version", "definition_hash", "state", "created_by", "submitted_by", "approved_by",
            "approved_at", "simulation_reference", "effective_from", "superseded_by")


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    """A copy of Small with its features built (never written into the shared folder)."""
    folder = tmp_path_factory.mktemp("training") / "Small"
    shutil.copytree(SMALL, folder)
    build(folder)
    frames, labels, truth, located, build_report = prepare(folder)
    return {"folder": folder, "frames": frames, "labels": labels, "truth": truth, "located": located,
            "build": build_report}


def _ts(day: str, hour: int = 12) -> float:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(tzinfo=JAKARTA).timestamp()


# --- labels -----------------------------------------------------------------------------------


def test_only_in_window_rows_of_the_labelled_entity_are_positive():
    """A dormancy scenario on a quiet customer carries two ordinary payments
    before its dormancy (generator 1.4.0); outside the window, not positive."""
    truth = Truth(
        labels={("ATO-POS-0001", "CUST-1"): Label("ATO-POS-0001", "INJECTED_POSITIVE", "ATO", "CUST-1",
                                                   date(2026, 8, 20), date(2026, 8, 20))},
        scenario_refs={"ATO-POS-0001": ["history-1", "history-2", "burst-1", "burst-2"]},
    )
    fraud = pd.DataFrame({
        "entity_id": ["CUST-1"] * 5,
        "transaction_ref": ["history-1", "history-2", "ordinary", "burst-1", "burst-2"],
        "ts": [_ts("2026-04-10"), _ts("2026-04-20"), _ts("2026-04-25"), _ts("2026-08-20", 10), _ts("2026-08-20", 11)],
    })
    labels = fraud_labels(truth, fraud)

    assert labels.group.tolist() == ["background", "background", "background", "positive", "positive"]
    assert labels.y.tolist() == [0.0, 0.0, 0.0, 1.0, 1.0]


def test_a_scenarios_aftermath_is_left_out_and_so_are_participants():
    truth = Truth(
        labels={("P11-POS-0001", "COLLECTOR"): Label("P11-POS-0001", "INJECTED_POSITIVE", "P11", "COLLECTOR",
                                                      date(2026, 7, 1), date(2026, 7, 5))},
        scenario_refs={"P11-POS-0001": ["in-1", "out-1"]},
    )
    fraud = pd.DataFrame({
        "entity_id": ["COLLECTOR", "SENDER", "COLLECTOR", "COLLECTOR", "SENDER"],
        "transaction_ref": ["in-1", "out-1", "after-10d", "after-40d", "sender-later"],
        "ts": [_ts("2026-07-02"), _ts("2026-07-02"), _ts("2026-07-12"), _ts("2026-08-11"), _ts("2026-09-01")],
    })
    labels = fraud_labels(truth, fraud)

    # P11 belongs to the AML model: the Fraud model sees it as the other model's positive.
    assert labels.group.tolist() == ["other_positive", "participant", "post_scenario", "background", "participant"]
    assert np.isnan(labels.y.to_numpy()[[0, 1, 2, 4]]).all()


def test_on_small_every_positive_is_the_owned_patterns_own_activity(small):
    truth, frames, labels = small["truth"], small["frames"], small["labels"]
    fraud, fl = frames["fraud"], labels["fraud"]
    positive = fl.group == "positive"
    assert positive.any()
    for ref, entity, ts, scenario, pattern in zip(fraud.transaction_ref[positive], fraud.entity_id[positive],
                                                  fraud.ts[positive], fl.scenario_id[positive], fl.pattern[positive]):
        label = truth.labels[(scenario, entity)]
        assert ref in truth.scenario_refs[scenario] and pattern in FRAUD_OWNS
        assert label.window_start <= datetime.fromtimestamp(ts, JAKARTA).date() <= label.window_end
    # Control entities are never positive, P12 entities never trained on.
    assert not (positive & fraud.entity_id.isin(truth.control)).any()
    assert fl.y[fl.group == "screening"].isna().all()


def test_on_small_an_aml_positive_week_holds_that_scenarios_rows(small):
    frames, labels = small["frames"], small["labels"]
    al, fl = labels["aml"], labels["fraud"]
    weeks_with_rows = set()
    fraud = frames["fraud"]
    for entity, ts, scenario in zip(fraud.entity_id, fraud.ts, fl.scenario_id):
        if scenario is not None:
            from app.ml.features import week_end
            weeks_with_rows.add((entity, week_end(ts), scenario))
    aml = frames["aml"]
    positive = al.group == "positive"
    assert positive.any()
    for entity, as_of, scenario in zip(aml.entity_id[positive], aml.as_of[positive], al.scenario_id[positive]):
        assert (entity, as_of, scenario) in weeks_with_rows


def test_the_participants_are_p11_senders_and_excluded(small):
    truth, located, labels = small["truth"], small["located"], small["labels"]
    assert located.participants
    for model in ("aml", "fraud"):
        frame = small["frames"][model]
        rows = frame.entity_id.isin(located.participants)
        assert (labels[model].group[rows] == "participant").all() and labels[model].y[rows].isna().all()
    senders = set()
    for (scenario, _), label in truth.labels.items():
        if label.pattern == "P11" and label.kind == "positive":
            refs = set(truth.scenario_refs[scenario])
            fraud = small["frames"]["fraud"]
            senders |= set(fraud.entity_id[fraud.transaction_ref.isin(refs)])
    assert located.participants <= senders


# --- split, weights, threshold ---------------------------------------------------------------


def test_the_split_keeps_a_scenario_together_and_is_deterministic(small):
    truth, located = small["truth"], small["located"]
    entities = sorted(set(small["frames"]["fraud"].entity_id))
    first = split_entities(truth, entities, located.participants)
    assert first == split_entities(truth, entities, located.participants)
    assert set(first) == set(entities) and set(first.values()) == {"train", "validation"}
    by_scenario = {}
    for (scenario, entity) in truth.labels:
        if entity in first:
            by_scenario.setdefault(scenario, set()).add(first[entity])
    assert all(len(sides) == 1 for sides in by_scenario.values())
    share = sum(side == "validation" for side in first.values()) / len(first)
    assert 0.15 <= share <= 0.25


def test_each_owned_pattern_carries_the_same_positive_weight():
    y = np.array([1, 1, 1, 1, 1, 1, 0, 0])
    pattern = pd.Series(["P04", "P04", "P04", "P04", "ATO", "P05", None, None])
    w = pattern_balanced_weights(y, pattern)

    totals = {p: w[(pattern == p).to_numpy()].sum() for p in ("P04", "ATO", "P05")}
    assert np.allclose(list(totals.values()), 2.0) and w[y == 0].tolist() == [1.0, 1.0]


def test_the_threshold_never_flags_a_control_entity():
    scores = np.array([0.95, 0.9, 0.8, 0.7, 0.6, 0.2])
    y = np.array([1, 1, 0, 1, 0, 0], dtype=float)
    control = np.array([False, False, True, False, False, False])

    chosen = choose_threshold(scores, y, control)
    assert chosen["threshold"] > 0.8 and chosen["recall"] == pytest.approx(2 / 3)


# --- training ---------------------------------------------------------------------------------


def _toy(seed=0, n=3000):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n), "c": rng.exponential(size=n)})
    y = ((X.a + 0.5 * X.b + rng.normal(scale=0.5, size=n)) > 1.5).astype(float).to_numpy()
    X.loc[rng.random(n) < 0.1, "c"] = np.nan
    return X, y


def test_training_is_reproducible_and_contributions_add_up():
    X, y = _toy()
    first = train_gbm(X[:2000], y[:2000], np.ones(2000), X[2000:], y[2000:])
    second = train_gbm(X[:2000], y[:2000], np.ones(2000), X[2000:], y[2000:])
    assert first.model_to_string() == second.model_to_string()

    contributions = gbm_contributions(first, X[2000:])
    raw = first.predict(X[2000:], num_iteration=first.best_iteration, raw_score=True)
    assert np.allclose(contributions.sum(axis=1), raw)
    assert np.allclose(1 / (1 + np.exp(-raw)), gbm_scores(first, X[2000:]))


def test_the_linear_comparator_round_trips_through_json():
    X, y = _toy(1)
    model = train_linear(X, y, np.ones(len(y)))
    again = LinearModel.from_json(json.loads(json.dumps(model.to_json())))
    assert np.allclose(model.scores(X), again.scores(X))
    assert np.allclose(model.contributions(X).sum(axis=1) + model.intercept,
                       np.log(model.scores(X) / (1 - model.scores(X))))


def test_the_anomaly_comparator_has_no_score_without_a_baseline():
    frame = pd.DataFrame({c: [np.nan, 2.0, 10.0] for c in comparators.Z_COLUMNS})
    for c in comparators.JSD_COLUMNS:
        frame[c] = [np.nan, 0.1, 0.9]
    score = comparators.anomaly_score(frame)
    assert np.isnan(score[0]) and 0 < score[1] < score[2] <= 1


def test_training_on_small_writes_a_complete_model_card(small, tmp_path):
    out = tmp_path / "models"
    card = train_model("fraud", small["folder"], small["frames"], small["labels"], small["truth"],
                       small["located"], small["build"], out)

    assert all(field in card for field in ENVELOPE)
    assert card["artefact_type"] == "ANOMALY_MODEL" and card["state"] == "DRAFT" and card["approved_by"] is None
    assert card["feature_set"]["version"] == FEATURE_SET_VERSION
    assert card["training_data"]["seed"] == 20260923 and card["training_data"]["profile"] == "small"
    assert "caveat" in card and "Synthetic" in card["caveat"]
    booster, linear, loaded = load_model(out, "fraud")
    assert loaded["definition_hash"] == card["definition_hash"]
    # The stored model scores exactly as trained.
    frame = small["frames"]["fraud"]
    assert np.allclose(gbm_scores(booster, frame[card["feature_set"]["features"]].head(200)),
                       booster.predict(frame[card["feature_set"]["features"]].head(200)))


def _clustered(seed=0, n=20000):
    """Positives in a few sparse regions, as in the generated data."""
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(n, 4)), columns=list("abcd"))
    y = np.zeros(n)
    for column in "abc":
        tail = rng.choice(n, 70, replace=False)
        X.loc[tail, column] += 6
        y[tail] = 1
    return X, y


def test_the_canary_passes_without_a_label_path():
    X, y = _clustered()
    result = canary(X[:15000], y[:15000], X[15000:], y[15000:])
    assert result["passed"], result


def test_the_canary_catches_the_true_labels_leaking_through_the_weights():
    X, y = _clustered()
    leaky = np.where(y[:15000] == 1, 50.0, 1.0)  # weights computed from the true labels
    result = canary(X[:15000], y[:15000], X[15000:], y[15000:], weights=leaky)
    assert not result["passed"], result


def test_the_card_records_the_canary(small, tmp_path):
    """On Small the canary is noise (a handful of validation positives); it is
    recorded, and judged at the full profile."""
    card = train_model("aml", small["folder"], small["frames"], small["labels"], small["truth"],
                       small["located"], small["build"], tmp_path)
    assert len(card["canary"]["roc_auc"]) == 10 and card["canary"]["validation_positives"] > 0


def test_features_built_with_another_reference_are_refused(small, tmp_path):
    folder = tmp_path / "Small"
    shutil.copytree(small["folder"], folder)
    report = folder / f"features_{FEATURE_SET_VERSION}" / "build.json"
    data = json.loads(report.read_text())
    data["mcc_ticket_reference_sha256"] = "0" * 64
    report.write_text(json.dumps(data))
    with pytest.raises(SystemExit, match="another MCC reference"):
        prepare(folder)
