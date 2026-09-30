"""Feature library fs_v1 (stage 1b): what the models will see, and nothing more.

The leakage tests prove the answers cannot reach a feature: ground-truth files
are never read, extra columns are ignored, identifiers carry no value, and no
feature looks at the future. The parity tests prove the features describe the
rows the app would store, and that one entity scored alone (live) gets exactly
what the batch build gave it. Labels appear here only to check that each
pattern's defining feature really separates it; they never enter the library.
"""

import csv
import math
import random
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from sqlalchemy import select

from app.ml import feature_set
from app.ml.feature_set import AML_FEATURES, FEATURES, FRAUD_FEATURES, JAKARTA, P, geo_list, render_markdown
from app.ml.features import (
    EntityHistory,
    InMemoryContext,
    accounts_by_entity,
    aml_features,
    build_matrices,
    fraud_features,
    histories,
    week_end,
)
from app.ml.loader import ALLOWED, Account, Txn, load_dataset
from app.models.transaction import Transaction
from app.scripts.generate_raw_dataset import HIGH_RISK_COUNTRIES
from app.scripts.load_raw_dataset import SHARED_ROOT, load_master_data, project_transactions
from app.services.ingestion import ingest_transactions_csv

SMALL = SHARED_ROOT / "Small"
GROUND_TRUTH = ("labels.csv", "label_transactions.csv", "generation_report.json", "scenario_catalogue.md",
                "seeds.json", "data_dictionary.md")
DOCS = Path(__file__).resolve().parents[2] / "docs" / "ml" / "feature_set_fs_v1.md"


@pytest.fixture(scope="module")
def small():
    dataset = load_dataset(SMALL)
    context = InMemoryContext(dataset)
    aml, fraud = build_matrices(dataset)
    return {"dataset": dataset, "context": context, "aml": aml, "fraud": fraud,
            "histories": histories(dataset, context)}


def _rows(folder: Path, name: str) -> list[dict[str, str]]:
    with (folder / name).open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write(folder: Path, name: str, rows: list[dict[str, str]]) -> None:
    with (folder / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


# --- leakage -----------------------------------------------------------------------------------


def test_ground_truth_files_are_never_read(small, tmp_path):
    """Delete every file that knows the answer: the features come out identical."""
    copy = tmp_path / "blind"
    shutil.copytree(SMALL, copy)
    for name in GROUND_TRUTH:
        (copy / name).unlink()

    aml, fraud = build_matrices(load_dataset(copy))

    assert_frame_equal(aml, small["aml"])
    assert_frame_equal(fraud, small["fraud"])


def test_the_loader_allow_list_contains_no_answer():
    assert not set(ALLOWED) & set(GROUND_TRUTH)
    assert "customers.csv" not in ALLOWED and "business_customers.csv" not in ALLOWED


def test_a_column_smuggled_into_the_transactions_is_ignored(small, tmp_path):
    """Even if a column named after the answer appeared, it would not be read."""
    copy = tmp_path / "smuggled"
    shutil.copytree(SMALL, copy)
    scenario_of = {r["source_transaction_reference"]: r["scenario_id"] for r in _rows(SMALL, "label_transactions.csv")}
    rows = _rows(copy, "transactions.csv")
    for row in rows:
        row["scenario_id"] = scenario_of.get(row["source_transaction_reference"], "")
    _write(copy, "transactions.csv", rows)

    aml, fraud = build_matrices(load_dataset(copy))

    assert_frame_equal(aml, small["aml"])
    assert_frame_equal(fraud, small["fraud"])


def test_no_feature_is_named_after_the_answer_or_an_identifier():
    forbidden = ("COHORT", "LABEL", "SCENARIO", "EXPECTED", "NOTE", "PATTERN", "_ID", "REF", "NAME", "EMAIL",
                 "PHONE", "NATIONAL", "IP_", "SEED")
    for feature in FEATURES:
        assert not any(token in feature.code for token in forbidden), feature.code
    assert len(set(AML_FEATURES)) == len(AML_FEATURES) and len(set(FRAUD_FEATURES)) == len(FRAUD_FEATURES)


def test_identifiers_carry_no_value(small, tmp_path):
    """Rename every entity, account, device, merchant and counterparty: every
    feature value stays the same, so no feature reads an identifier."""
    copy = tmp_path / "renamed"
    shutil.copytree(SMALL, copy)
    rng = random.Random(3)
    names: dict[str, str] = {}

    def rename(value: str) -> str:
        if value and value not in names:
            names[value] = f"X{rng.randrange(10**12):012d}"
        return names.get(value, value)

    accounts = _rows(copy, "accounts.csv")
    for row in accounts:
        row["source_account_id"], row["owner_source_id"] = rename(row["source_account_id"]), rename(row["owner_source_id"])
    _write(copy, "accounts.csv", accounts)
    merchants = _rows(copy, "merchants.csv")
    for row in merchants:
        row["source_merchant_id"], row["source_business_id"] = rename(row["source_merchant_id"]), rename(row["source_business_id"])
    _write(copy, "merchants.csv", merchants)
    devices = _rows(copy, "devices.csv")
    for row in devices:
        row["source_device_id"] = rename(row["source_device_id"])
    _write(copy, "devices.csv", devices)
    transactions = _rows(copy, "transactions.csv")
    for row in transactions:
        for column in ("source_account_id", "source_merchant_id", "source_device_id", "counterparty_reference"):
            row[column] = rename(row[column])
    _write(copy, "transactions.csv", transactions)

    aml, fraud = build_matrices(load_dataset(copy))
    back = {new: old for old, new in names.items()}
    for frame, original, key in ((aml, small["aml"], ["entity_id", "as_of"]),
                                 (fraud, small["fraud"], ["transaction_ref"])):
        frame = frame.assign(entity_id=frame.entity_id.map(back)).sort_values(key).reset_index(drop=True)
        assert_frame_equal(frame, original.sort_values(key).reset_index(drop=True))


# --- point in time -------------------------------------------------------------------------------


def _truncated(small, entity: str, moment: float) -> EntityHistory:
    rows = [t for t in small["dataset"].entities[entity] if t.ts <= moment]
    return EntityHistory.build(entity, rows, accounts_by_entity(small["dataset"])[entity],
                               small["dataset"].merchants, small["context"])


def _same(a: dict, b: dict) -> bool:
    return all((math.isnan(a[k]) and math.isnan(b[k])) or a[k] == pytest.approx(b[k]) for k in a)


def test_an_aml_row_never_reads_the_future(small):
    """Features at a week's end are the same whether or not later history exists."""
    rng = random.Random(1)
    for entity in rng.sample(sorted(small["histories"]), 60):
        full = small["histories"][entity]
        as_of = week_end(rng.choice(full.t.tolist()))
        assert _same(aml_features(full, as_of, small["context"]),
                     aml_features(_truncated(small, entity, as_of), as_of, small["context"])), entity


def test_a_fraud_row_never_reads_the_future(small):
    rng = random.Random(2)
    for entity in rng.sample(sorted(small["histories"]), 60):
        full = small["histories"][entity]
        i = rng.randrange(len(full.t))
        cut = EntityHistory.build(entity, [t for t in small["dataset"].entities[entity]
                                           if (t.ts, t.ref) <= (full.t[i], full.refs[i])],
                                  accounts_by_entity(small["dataset"])[entity], small["dataset"].merchants,
                                  small["context"])
        assert _same(fraud_features(full, i, small["context"]), fraud_features(cut, len(cut.t) - 1, small["context"]))


def test_device_usage_is_counted_up_to_the_moment_only(small):
    context = small["context"]
    device, (times, *_rest) = next((d, tl) for d, tl in context._timelines.items() if len(tl[0]) > 3)
    assert context.device_entities(device, times[0] - 1, times[0]) >= 1
    assert context.device_entities(device, times[0] - 10, times[0] - 1) == 0


# --- parity: batch vs one entity, and features vs ingestion ---------------------------------------


def test_one_entity_scored_alone_gets_exactly_its_batch_features(small):
    """What live monitoring will do: build one entity's history and score it."""
    rng = random.Random(4)
    owned = accounts_by_entity(small["dataset"])
    for entity in rng.sample(sorted(small["histories"]), 40):
        alone = EntityHistory.build(entity, small["dataset"].entities[entity], owned[entity],
                                    small["dataset"].merchants, small["context"])
        batch_aml = small["aml"][small["aml"].entity_id == entity]
        for row in batch_aml.itertuples(index=False):
            assert _same(aml_features(alone, row.as_of, small["context"]),
                         {c: getattr(row, c) for c in AML_FEATURES})
        batch_fraud = small["fraud"][small["fraud"].entity_id == entity].reset_index(drop=True)
        for i, row in enumerate(batch_fraud.itertuples(index=False)):
            assert row.transaction_ref == alone.refs[i]
            assert _same(fraud_features(alone, i, small["context"]), {c: getattr(row, c) for c in FRAUD_FEATURES})


def test_features_describe_exactly_the_rows_ingestion_accepts(small, db, tmp_path):
    """Training/serving parity: the feature library's rows are the rows the
    upload endpoint stores, no more, no fewer."""
    load_master_data(db, SMALL)
    projected = tmp_path / "app_format.csv"
    project_transactions(SMALL, projected)
    ingest_transactions_csv(db, file_name=projected.name, content=projected.read_text())
    stored = set(db.execute(select(Transaction.transaction_ref)).scalars())

    assert stored == {t.ref for t in small["dataset"].transactions}
    assert set(small["fraud"].transaction_ref) == stored


def test_aml_rows_are_the_weeks_an_entity_transacted(small):
    assert (small["aml"].TXN_COUNT_R7D >= 1).all()
    assert len(small["fraud"]) == len(small["dataset"].transactions)


# --- short history is explicit ------------------------------------------------------------------


def _synthetic(days: int, count: int) -> tuple[EntityHistory, InMemoryContext]:
    start = datetime(2026, 6, 1, 10, tzinfo=JAKARTA).timestamp()
    rows = [Txn(ref=f"T{i:03d}", account="A1", entity="E1", ts=start + i * days * 86_400 / count,
                amount=100_000.0 + i, out=i % 2 == 0, channel="TRANSFER", txn_type="P2P_TRANSFER",
                counterparty=f"C{i % 3}", counterparty_country="ID", merchant="", device="D1", reversed=False)
            for i in range(count)]
    context = InMemoryContext.__new__(InMemoryContext)
    context._timelines, context._flags, context._listed = {}, {}, geo_list()
    return EntityHistory.build("E1", rows, [Account("E1", False, start - 400 * 86_400)], {}, context), context


def test_a_short_history_leaves_baseline_and_novelty_features_empty():
    """Three weeks of data, e.g. an upload of a few weeks: the features that
    compare with the entity's own past are NaN, not zero, and say why."""
    history, context = _synthetic(days=20, count=12)
    as_of = week_end(history.t[-1])
    aml = aml_features(history, as_of, context)
    gated = [f.code for f in FEATURES if f.unit == "AML" and f.gate not in (feature_set.GATE_NONE,
                                                                              feature_set.GATE_BUSINESS)]

    assert all(math.isnan(aml[code]) for code in gated), [c for c in gated if not math.isnan(aml[c])]
    assert aml["HISTORY_DAYS"] < P["min_history_days"] and aml["BASELINE_TXN_COUNT"] < 12

    fraud = fraud_features(history, len(history.t) - 1, context)
    for code in ("DEVICE_FIRST_USE", "COUNTERPARTY_FIRST_USE", "AMOUNT_OVER_OWN_P95", "AMOUNT_OVER_P95_LAST_N"):
        assert math.isnan(fraud[code]), code
    assert fraud["HISTORY_DAYS"] < P["min_history_days"]


def test_enough_history_opens_the_gates():
    history, context = _synthetic(days=120, count=60)
    fraud = fraud_features(history, len(history.t) - 1, context)

    assert fraud["COUNTERPARTY_FIRST_USE"] == 0.0 and fraud["DEVICE_FIRST_USE"] == 0.0
    assert not math.isnan(fraud["AMOUNT_OVER_P95_LAST_N"])


# --- each pattern's defining feature separates it (labels used here only) -----------------------


def _positive_weeks(small) -> dict[tuple[str, float], str]:
    labels = {l["scenario_id"]: l for l in _rows(SMALL, "labels.csv")}
    positive = {(l["scenario_id"], l["entity_source_id"]) for l in labels.values() if l["label_type"] == "INJECTED_POSITIVE"}
    ts = dict(zip(small["fraud"].transaction_ref, small["fraud"].ts))
    entity = dict(zip(small["fraud"].transaction_ref, small["fraud"].entity_id))
    weeks = {}
    for row in _rows(SMALL, "label_transactions.csv"):
        ref, sid = row["source_transaction_reference"], row["scenario_id"]
        if ref in ts and (sid, entity[ref]) in positive:
            weeks[(entity[ref], week_end(ts[ref]))] = labels[sid]["pattern_code"]
    return weeks


@pytest.mark.parametrize(("pattern", "feature", "threshold"), [
    ("P01", "TXN_VALUE_MAX_R7D", 1_000_000_000),
    ("P02", "P02_BAND_COUNT_ROLLING_7D", 3),
    ("P03", "PASSTHROUGH_RATIO_MAX_R7D", 0.9),
    ("P04", "MAX_COUNT_ROLLING_24H", 10),
    ("P05", "LONGEST_ACCOUNT_GAP_DAYS_R7D", 90),
    ("P06", "ROUND_SHARE_R30D", 0.6),
    ("P08", "GEO_EXPOSURE_SHARE_R30D", 0.5),
    ("P09", "DEVICE_ENTITY_COUNT_R30D", 4),
    ("P10", "TICKET_OVER_BAND", 20),
    ("P11", "FUNNEL_IN_DEGREE_ROLLING_7D", 5),
])
def test_each_positive_shows_its_defining_feature(small, pattern, feature, threshold):
    weeks = _positive_weeks(small)
    aml = small["aml"].assign(pattern=[weeks.get(k) for k in zip(small["aml"].entity_id, small["aml"].as_of)])
    by_entity = aml[aml.pattern == pattern].groupby("entity_id")[feature].max()

    assert len(by_entity), f"no {pattern} positive in Small"
    assert (by_entity >= threshold).all(), by_entity[by_entity < threshold]


def test_ato_rows_show_a_new_device_and_new_recipients(small):
    scenario = {r["source_transaction_reference"]: r["scenario_id"] for r in _rows(SMALL, "label_transactions.csv")}
    fraud = small["fraud"].assign(scenario=small["fraud"].transaction_ref.map(scenario))
    ato = fraud[fraud.scenario.fillna("").str.startswith("ATO-POS-")]

    assert len(ato)
    assert (ato.DEVICE_TENURE_DAYS.dropna() < 1).all()
    assert (ato.COUNTERPARTY_FIRST_USE.dropna() == 1).all() or (ato.MERCHANT_FIRST_USE.dropna() == 1).all()
    assert ato.groupby("scenario").DAYS_SINCE_PREV_TXN.max().min() >= 90


# --- the feature set as an artefact -------------------------------------------------------------


def test_the_feature_documentation_is_rendered_from_the_registry():
    assert DOCS.read_text(encoding="utf-8") == render_markdown(), (
        "docs/ml/feature_set_fs_v1.md is stale: python -c 'from app.ml.feature_set import render_markdown; "
        "print(render_markdown(), end=\"\")' > ../docs/ml/feature_set_fs_v1.md"
    )


def test_every_parameter_states_its_source():
    assert all(parameter.source.strip() for parameter in feature_set.PARAMETERS)
    assert P["min_history_days"] == 30


def test_the_geography_list_is_the_organisations_configured_list():
    """geo_list_v1 is configuration, like P02's threshold. The generator builds
    its exposure scenarios against the same organisational list; this checks
    the two did not drift apart."""
    assert geo_list() == frozenset(HIGH_RISK_COUNTRIES)


@pytest.mark.slow
def test_the_full_training_profile_builds(tmp_path):
    """Run with `pytest -m slow`: generate the full profile and build both matrices."""
    from app.ml.build_features import build
    from app.scripts.generate_raw_dataset import RawDatasetGenerator
    from datetime import date

    generator = RawDatasetGenerator(profile="full", seed=20260923, reference_date=date(2026, 9, 30))
    generator.generate()
    generator.write(tmp_path / "full")
    report = build(tmp_path / "full")

    assert report["rows"]["fraud"] == report["rows"]["transactions_accepted"] > 390_000
    # One row per entity and week the entity transacted: ~149k since 1.4.0's
    # heavy-tailed activity (quiet entities skip most weeks; 1.3.0 had ~198k).
    assert report["rows"]["aml"] > 140_000
    print(f"\nfs_v1 on full: {report['rows']} in {report['seconds']}")
