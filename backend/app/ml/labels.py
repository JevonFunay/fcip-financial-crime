"""Ground truth for training and evaluation: the only place labels meet features.

Reads `labels.csv` and `label_transactions.csv` (the feature library never
does) and gives every row of a feature matrix a group, following the rules
decided before training (PROJECT_CONTEXT §8, "Labels for stage 2"):

- **positive**: a transaction (Fraud) or an entity-week (AML) that contains the
  labelled entity's own scenario rows, and only rows inside the label's window.
  Since generator 1.4.0 a dormancy scenario on a quiet customer also carries
  the two ordinary payments it was given *before* its dormancy; they are
  outside the window and are not suspicious, so they do not make a positive
- **other_positive**: the same, for a pattern the other model owns. Not a
  negative (it is genuinely suspicious) and not this model's positive
- **lookalike**: rows of a look-alike or boundary case on its labelled entity:
  legitimate, and the hardest negatives
- **control**, **background**: the control-clean cohort, everything else
- excluded from training and from false-positive counts, reported on their
  own: **participant** (an entity that took part in a scenario without
  carrying its label, like P11's senders), **screening** (P12 entities: name
  matching, outside both models), **post_scenario** (a positive entity's rows
  from its scenario's start until 30 days after it, whose windows still hold
  the scenario)

Which model owns which pattern is decided by how fast the behaviour happens,
not by its domain (PROJECT_CONTEXT §8); domain is for reporting.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.feature_set import JAKARTA
from app.ml.features import week_end

FRAUD_OWNS = ("ATO", "P04", "P05")
AML_OWNS = ("P01", "P02", "P03", "P06", "P07", "P08", "P09", "P10", "P11")
OWNS = {"aml": AML_OWNS, "fraud": FRAUD_OWNS}
SCREENING = ("P12",)
DOMAIN = {
    **{p: "AML" for p in ("P01", "P02", "P06", "P07", "P08", "P12")},
    **{p: "Fraud" for p in ("P04", "P10", "ATO")},
    **{p: "Overlap" for p in ("P03", "P05", "P09", "P11")},
}
# A positive entity's rows stay ambiguous while their 30-day windows still
# hold the scenario (the longest window a feature uses besides baselines).
POST_SCENARIO_DAYS = 30
DAY = 86_400.0

TRAINABLE = ("positive", "lookalike", "control", "background")
EXCLUDED = ("other_positive", "participant", "screening", "post_scenario")


@dataclass(frozen=True)
class Label:
    scenario_id: str
    label_type: str
    pattern: str
    entity: str
    window_start: date
    window_end: date

    @property
    def kind(self) -> str:
        if self.label_type == "INJECTED_POSITIVE":
            return "positive"
        return "boundary" if "-BOUND-" in self.scenario_id else "look-alike"


@dataclass
class Truth:
    labels: dict[tuple[str, str], Label]                  # (scenario_id, entity) -> label
    scenario_refs: dict[str, list[str]]                    # scenario_id -> transaction references
    control: set[str] = field(default_factory=set)

    @property
    def screening_entities(self) -> set[str]:
        return {l.entity for l in self.labels.values() if l.pattern in SCREENING}


def load_truth(dataset_dir: Path) -> Truth:
    labels: dict[tuple[str, str], Label] = {}
    control: set[str] = set()
    with (dataset_dir / "labels.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["label_type"] == "CONTROL_CLEAN":
                control.add(row["entity_source_id"])
            elif row["pattern_code"] != "ER":  # entity resolution twins, not monitoring
                labels[(row["scenario_id"], row["entity_source_id"])] = Label(
                    row["scenario_id"], row["label_type"], row["pattern_code"], row["entity_source_id"],
                    date.fromisoformat(row["window_start"]), date.fromisoformat(row["window_end"]))
    refs: dict[str, list[str]] = defaultdict(list)
    with (dataset_dir / "label_transactions.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            refs[row["scenario_id"]].append(row["source_transaction_reference"])
    return Truth(labels, dict(refs), control)


def _local_date(ts: float) -> date:
    return datetime.fromtimestamp(ts, JAKARTA).date()


@dataclass
class ScenarioRows:
    """Where each scenario's rows landed, from the Fraud matrix (every accepted
    transaction, with its entity and time)."""

    positive: dict[tuple[str, str], list[tuple[str, float]]]   # label key -> in-window (ref, ts)
    edge: dict[tuple[str, str], list[tuple[str, float]]]       # label key -> (ref, ts)
    participants: set[str]


def locate(truth: Truth, fraud: pd.DataFrame) -> ScenarioRows:
    where = dict(zip(fraud.transaction_ref, zip(fraud.entity_id, fraud.ts)))
    positive: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    edge: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    labelled_by_scenario: dict[str, set[str]] = defaultdict(set)
    for scenario_id, entity in truth.labels:
        labelled_by_scenario[scenario_id].add(entity)
    participants: set[str] = set()
    for scenario_id, refs in truth.scenario_refs.items():
        for ref in refs:
            if ref not in where:  # quarantined by ingestion's validation: never scored
                continue
            entity, ts = where[ref]
            label = truth.labels.get((scenario_id, entity))
            if label is None:
                if entity not in labelled_by_scenario[scenario_id]:
                    participants.add(entity)
                continue
            if label.kind == "positive":
                if label.window_start <= _local_date(ts) <= label.window_end:
                    positive[(scenario_id, entity)].append((ref, ts))
            else:
                edge[(scenario_id, entity)].append((ref, ts))
    # An entity with its own label is not a participant of someone else's scenario.
    participants -= {entity for _, entity in truth.labels}
    return ScenarioRows(dict(positive), dict(edge), participants)


def _assign(model: str, units: pd.DataFrame, unit_positive: dict, unit_edge: dict, truth: Truth,
            located: ScenarioRows, time_col: str) -> pd.DataFrame:
    """Group, target and scenario for every unit (row of a feature matrix)."""
    owns = OWNS[model]
    screening = truth.screening_entities
    spans: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for (scenario_id, entity), rows in located.positive.items():
        times = [ts for _, ts in rows]
        spans[entity].append((min(times), max(times) + POST_SCENARIO_DAYS * DAY))

    group, pattern, scenario, kind = [], [], [], []
    for key, entity, moment in zip(units["_key"], units.entity_id, units[time_col]):
        g = p = s = k = None
        if entity in located.participants:
            g = "participant"
        elif entity in screening:
            g = "screening"
        elif key in unit_positive:
            s, p = unit_positive[key]
            g = "positive" if p in owns else "other_positive"
            k = "positive"
        elif key in unit_edge:
            s, p, k = unit_edge[key]
            g = "lookalike"
        elif any(start <= moment <= end for start, end in spans.get(entity, ())):
            g = "post_scenario"
        elif entity in truth.control:
            g = "control"
        else:
            g = "background"
        group.append(g)
        pattern.append(p)
        scenario.append(s)
        kind.append(k)
    out = pd.DataFrame({"group": group, "pattern": pattern, "scenario_id": scenario, "kind": kind},
                       index=units.index)
    out["y"] = np.where(out.group == "positive", 1.0, np.where(out.group.isin(TRAINABLE), 0.0, np.nan))
    return out


def fraud_labels(truth: Truth, fraud: pd.DataFrame, located: ScenarioRows | None = None) -> pd.DataFrame:
    located = located or locate(truth, fraud)
    unit_positive = {ref: (sid, truth.labels[(sid, e)].pattern)
                     for (sid, e), rows in located.positive.items() for ref, _ in rows}
    unit_edge = {ref: (sid, truth.labels[(sid, e)].pattern, truth.labels[(sid, e)].kind)
                 for (sid, e), rows in located.edge.items() for ref, _ in rows}
    units = fraud[["entity_id", "ts"]].assign(_key=fraud.transaction_ref)
    return _assign("fraud", units, unit_positive, unit_edge, truth, located, "ts")


def aml_labels(truth: Truth, aml: pd.DataFrame, fraud: pd.DataFrame,
               located: ScenarioRows | None = None) -> pd.DataFrame:
    located = located or locate(truth, fraud)
    unit_positive, unit_edge = {}, {}
    for (sid, entity), rows in located.positive.items():
        for _, ts in rows:
            unit_positive[(entity, week_end(ts))] = (sid, truth.labels[(sid, entity)].pattern)
    for (sid, entity), rows in located.edge.items():
        label = truth.labels[(sid, entity)]
        for _, ts in rows:
            unit_edge.setdefault((entity, week_end(ts)), (sid, label.pattern, label.kind))
    units = aml[["entity_id", "as_of"]].assign(_key=list(zip(aml.entity_id, aml.as_of)))
    return _assign("aml", units, unit_positive, unit_edge, truth, located, "as_of")


def positive_labels(truth: Truth) -> list[Label]:
    return sorted((l for l in truth.labels.values() if l.kind == "positive"),
                  key=lambda l: (l.pattern, l.scenario_id, l.entity))
