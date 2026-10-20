"""Check matched research comparisons without inventing production approval.

Counts may describe publisher reference labels, simulator scenarios or reviewed
observations. They are not interchangeable targets. A precision gain is only
reported within the same labelled evaluation population, with recall/workload
guards checked separately. Human review remains a distinct source of evidence.
"""

from __future__ import annotations

import math
from numbers import Integral


def _counts(value: dict) -> dict:
    result = {}
    for key in ("tp", "fp", "fn", "tn"):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, Integral) or number < 0:
            raise ValueError(f"{key} must be a nonnegative integer count")
        result[key] = int(number)
    tp, fp, fn, tn = (result[key] for key in ("tp", "fp", "fn", "tn"))
    result.update(precision=tp / (tp + fp) if tp + fp else None,
                  recall=tp / (tp + fn) if tp + fn else None,
                  units=tp + fp + fn + tn, positive_units=tp + fn,
                  negative_units=fp + tn)
    for key in ("precision", "recall"):
        if key in value:
            actual, expected = value[key], result[key]
            if ((actual is None) != (expected is None)
                    or actual is not None and not math.isclose(actual, expected, abs_tol=1e-12, rel_tol=1e-12)):
                raise ValueError(f"{key} disagrees with confusion counts")
    return result


def matched_comparison(selected: dict, reference: dict, *, slices: dict | None = None,
                       evaluation_id: str, reference_evaluation_id: str,
                       target_scope: str, reference_target_scope: str) -> dict:
    """Assess a fixed comparison, never select/tune a model on final outcomes.

    Caller supplies exact matching evaluation and target definitions, not only
    equal row counts. Small/recipe guards are declared by the experiment before
    final evaluation and supplied as matched ``selected``/``reference`` pairs.
    Empty positive slices are disclosed as unassessable, never evidence of recall.
    """
    if (not evaluation_id or evaluation_id != reference_evaluation_id
            or not target_scope or target_scope != reference_target_scope):
        raise ValueError("Comparisons require the same evaluation population and target definition")
    if slices is not None and "all" in slices:
        raise ValueError("The overall recall guard cannot be replaced by a supplied slice")
    left, right = _counts(selected), _counts(reference)
    if (left["positive_units"], left["negative_units"]) != (right["positive_units"], right["negative_units"]):
        raise ValueError("Reference and candidate do not describe the same labelled population")
    guards, unassessable = {}, []
    for name, pair in {"all": {"selected": left, "reference": right}, **(slices or {})}.items():
        a, b = _counts(pair["selected"]), _counts(pair["reference"])
        if (a["positive_units"], a["negative_units"]) != (b["positive_units"], b["negative_units"]):
            raise ValueError(f"Mismatched evaluation population in {name}")
        if not a["positive_units"]:
            guards[name] = None
            unassessable.append(name)
        else:
            guards[name] = a["tp"] >= b["tp"]
    delta = left["precision"] - right["precision"] if left["precision"] is not None and right["precision"] is not None else None
    fp_retained = left["fp"] <= right["fp"]
    recall_retained = bool(guards["all"]) and all(value is not False for value in guards.values())
    return {
        "state": "DRAFT", "evaluation_id": evaluation_id, "target_scope": target_scope,
        "selected": left, "reference": right,
        "precision_delta_percentage_points": delta * 100 if delta is not None else None,
        "recall_count_guards": guards, "unassessable_positive_slices": unassessable,
        "false_positives_do_not_increase": fp_retained,
        "recall_counts_retained": recall_retained,
        "matched_research_gain": bool(delta is not None and delta > 0 and recall_retained and fp_retained),
        "production_approval": False,
        "caveat": "Matched benchmark comparison only. Missing positive slices remain unassessed; no claim about real-world crime accuracy or approval follows.",
    }


def random_pattern_expectation(population_size: int, flagged_count: int,
                               complete_pattern_support: dict[str, int]) -> dict:
    """Descriptive same-workload random reference for any-endpoint pattern hits.

    Uniformly choosing exactly F of N account-windows hits a complete pattern
    with m distinct participating account-windows with probability
    1 - choose(N-m,F)/choose(N,F). Linearity gives expected pattern hits without
    assuming independent or disjoint patterns. This is an after-freeze diagnostic,
    not a model candidate, significance test, label source or selection rule.
    """
    for value in (population_size, flagged_count):
        if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
            raise ValueError("Population and flag counts must be nonnegative integers")
    if flagged_count > population_size:
        raise ValueError("Flag count exceeds the evaluation population")
    probabilities = {}
    for pattern, support in complete_pattern_support.items():
        if (isinstance(support, bool) or not isinstance(support, Integral)
                or not 1 <= support <= population_size):
            raise ValueError("Complete patterns require distinct observed endpoint support inside the population")
        if support > population_size - flagged_count:
            probability = 1.0
        else:
            probability = -math.expm1(sum(math.log1p(-flagged_count / (population_size - i))
                                          for i in range(support)))
        probabilities[str(pattern)] = probability
    return {"kind": "uniform random flags at identical account-window workload",
            "population_size": int(population_size), "flagged_count": int(flagged_count),
            "complete_patterns": len(probabilities), "expected_complete_patterns_hit": sum(probabilities.values()),
            "pattern_hit_probabilities": probabilities,
            "after_freeze_diagnostic_only": True,
            "caveat": "Expected hits under uniform random flags, not a fitted detector or evidence of early interception. Not used for selection or a hypothesis test."}
