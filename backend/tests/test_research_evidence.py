"""Precision claims must keep their population, target and recall evidence."""

import pytest

from app.ml.behavioral.research_evidence import matched_comparison, random_pattern_expectation


def compare(selected, reference, **kwargs):
    return matched_comparison(selected, reference, evaluation_id="fixed-final-sha",
                              reference_evaluation_id="fixed-final-sha", target_scope="simulator SAR",
                              reference_target_scope="simulator SAR", **kwargs)


def counts(tp=8, fp=20, fn=2, tn=70):
    return dict(tp=tp, fp=fp, fn=fn, tn=tn)


def test_research_gain_retains_recall_and_never_approves_production():
    result = compare(counts(fp=5, tn=85), counts())
    assert result["matched_research_gain"]
    assert result["recall_counts_retained"]
    assert result["state"] == "DRAFT" and not result["production_approval"]


def test_fewer_flags_cannot_hide_a_recall_loss():
    result = compare(counts(tp=4, fp=1, fn=6, tn=89), counts())
    assert result["precision_delta_percentage_points"] > 0
    assert not result["matched_research_gain"]


def test_small_amount_recall_loss_rejects_overall_precision_gain():
    result = compare(counts(fp=5, tn=85), counts(), slices={
        "small": {"selected": counts(tp=1, fp=1, fn=3, tn=15),
                  "reference": counts(tp=3, fp=4, fn=1, tn=12)}})
    assert result["recall_count_guards"]["all"]
    assert not result["recall_count_guards"]["small"]
    assert not result["matched_research_gain"]


def test_same_model_is_not_an_improvement():
    result = compare(counts(), counts())
    assert result["precision_delta_percentage_points"] == 0
    assert not result["matched_research_gain"]


def test_zero_complete_patterns_is_unassessable_not_perfect_recall():
    result = compare(counts(fp=5, tn=85), counts(), slices={
        "complete_cycle": {"selected": counts(tp=0, fp=0, fn=0, tn=0),
                           "reference": counts(tp=0, fp=0, fn=0, tn=0)}})
    assert result["recall_count_guards"]["complete_cycle"] is None
    assert result["unassessable_positive_slices"] == ["complete_cycle"]


@pytest.mark.parametrize("bad", [counts(tp=-1), counts(tp=True), counts(fp=2.5), {**counts(), "precision": .99}])
def test_invalid_counts_or_fabricated_precision_fail(bad):
    with pytest.raises(ValueError):
        compare(bad, counts())


def test_unequal_label_prevalence_cannot_be_a_matched_improvement():
    with pytest.raises(ValueError, match="population"):
        compare(counts(tp=9, fn=2), counts())


@pytest.mark.parametrize("changed", ["population", "target"])
def test_other_datasets_and_other_review_targets_are_not_comparable(changed):
    with pytest.raises(ValueError, match="same evaluation"):
        matched_comparison(counts(), counts(), evaluation_id="new", reference_evaluation_id="old" if changed == "population" else "new",
                           target_scope="fraud", reference_target_scope="anomaly review" if changed == "target" else "fraud")


def test_empty_predictions_or_no_positives_cannot_claim_a_gain():
    result = compare(counts(tp=0, fp=0, fn=0, tn=100), counts(tp=0, fp=0, fn=0, tn=100))
    assert result["selected"]["precision"] is None
    assert result["selected"]["recall"] is None
    assert not result["matched_research_gain"]


def test_declared_slices_cannot_replace_the_overall_guard():
    with pytest.raises(ValueError, match="cannot be replaced"):
        compare(counts(tp=1, fp=0, fn=9, tn=90), counts(),
                slices={"all": {"selected": counts(), "reference": counts()}})


def test_random_pattern_reference_matches_exact_fixed_workload_probability():
    result = random_pattern_expectation(10, 2, {"small_pattern": 2, "large_pattern": 9})
    assert result["pattern_hit_probabilities"]["small_pattern"] == pytest.approx(1 - 28 / 45)
    assert result["pattern_hit_probabilities"]["large_pattern"] == 1
    assert result["expected_complete_patterns_hit"] == pytest.approx(1 + 1 - 28 / 45)
    assert result["after_freeze_diagnostic_only"]


def test_no_flags_provide_no_random_pattern_detection():
    assert random_pattern_expectation(10, 0, {"pattern": 4})["expected_complete_patterns_hit"] == 0
    assert random_pattern_expectation(0, 0, {})["complete_patterns"] == 0


@pytest.mark.parametrize("population,flagged,patterns", [(2, 3, {}), (2, 1, {"outside": 3}), (2, 1, {"empty": 0})])
def test_random_pattern_reference_cannot_invent_missing_pattern_support(population, flagged, patterns):
    with pytest.raises(ValueError):
        random_pattern_expectation(population, flagged, patterns)
