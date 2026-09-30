"""Generator 1.2.0: scenarios that differ from ordinary traffic only in their
behaviour (PROJECT_CONTEXT §8).

A model learns whatever separates a scenario from background. In 1.1.0 that
included how the generator wrote the rows (no device, a fixed hour), so a
model would have learnt the recipe. These tests pin the fix: the artefact
audit, the ATO scenario and its look-alikes, the device model, and the
per-transaction ground truth.
"""

import csv
import json
import random
import shutil
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app.scripts.audit_scenario_artefacts import audit
from app.scripts.generate_raw_dataset import (
    ATO_EDGE_KINDS,
    DEVICE_CHANGE_SHARE,
    MIN_TARGET_TRANSACTIONS,
    SHARED_HOUSEHOLD_DEVICES,
    RawDatasetGenerator,
)

REFERENCE_DATE = date(2026, 9, 30)
SEED = 20260923


def _rows(folder: Path, name: str) -> list[dict[str, str]]:
    with (folder / name).open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write(folder: Path, name: str, rows: list[dict[str, str]]) -> None:
    with (folder / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory) -> Path:
    """The smallest custom scale: every pattern floored at five scenarios, so
    all three ATO look-alike kinds are present."""
    generator = RawDatasetGenerator(seed=SEED, reference_date=REFERENCE_DATE,
                                    target_transactions=MIN_TARGET_TRANSACTIONS)
    generator.generate()
    out_dir = tmp_path_factory.mktemp("realism") / "data"
    generator.write(out_dir)
    return out_dir


@pytest.fixture(scope="module")
def world(dataset):
    """Rows, owners, devices and ground truth, read once."""
    transactions = _rows(dataset, "transactions.csv")
    owner = {a["source_account_id"]: a["owner_source_id"] for a in _rows(dataset, "accounts.csv")}
    by_entity = defaultdict(list)
    for row in transactions:
        if row["source_account_id"] in owner:
            by_entity[owner[row["source_account_id"]]].append(row)
    rows_of = defaultdict(list)
    by_ref = {r["source_transaction_reference"]: r for r in transactions}
    for entry in _rows(dataset, "label_transactions.csv"):
        rows_of[entry["scenario_id"]].append(by_ref[entry["source_transaction_reference"]])
    return {
        "transactions": transactions,
        "owner": owner,
        "by_entity": by_entity,
        "labels": _rows(dataset, "labels.csv"),
        "rows_of": rows_of,
        "devices": {d["source_device_id"]: d for d in _rows(dataset, "devices.csv")},
    }


def _when(row: dict[str, str]) -> datetime:
    return datetime.fromisoformat(row["value_datetime"])


def _parseable(rows):
    out = []
    for row in rows:
        try:
            out.append((_when(row), row))
        except ValueError:
            continue
    return out


# --- the artefact audit ---------------------------------------------------------------


def test_a_generated_dataset_has_no_scenario_artefacts(dataset):
    """Every scenario group looks like background except in its own behaviour."""
    result = audit(dataset)

    assert result.attribution.startswith("exact")
    assert result.flagged == [], [(group, view.view, view.tvd) for group, view in result.flagged]


def test_the_audit_catches_scenario_rows_without_a_device(dataset, tmp_path):
    """The 1.1.0 artefact, reintroduced on purpose: P04's rows lose their device."""
    copy = tmp_path / "broken"
    shutil.copytree(dataset, copy)
    p04 = {r["source_transaction_reference"] for r in _rows(copy, "label_transactions.csv")
           if r["scenario_id"].startswith("P04-POS-")}
    rows = _rows(copy, "transactions.csv")
    for row in rows:
        if row["source_transaction_reference"] in p04:
            row["source_device_id"] = ""
    _write(copy, "transactions.csv", rows)

    flagged = {(group, view.view) for group, view in audit(copy).flagged}

    assert ("P04 positive", "device_missing") in flagged


def test_the_audit_does_not_flag_a_random_sample_of_background(dataset, tmp_path):
    """The null case: ordinary rows dressed up as a scenario must pass, or the
    audit's clean bill for 1.2.0 would mean nothing."""
    copy = tmp_path / "null"
    shutil.copytree(dataset, copy)
    taken = {r["source_transaction_reference"] for r in _rows(copy, "label_transactions.csv")}
    background = [r for r in _rows(copy, "transactions.csv") if r["source_transaction_reference"] not in taken]
    sample = random.Random(7).sample(background, 400)
    ground_truth = _rows(copy, "label_transactions.csv") + [
        {"scenario_id": "P01-POS-9999", "source_transaction_reference": r["source_transaction_reference"]}
        for r in sample
    ]
    _write(copy, "label_transactions.csv", ground_truth)
    labels = _rows(copy, "labels.csv")
    labels.append({**labels[-1], "scenario_id": "P01-POS-9999", "label_type": "INJECTED_POSITIVE",
                   "pattern_code": "P01"})
    _write(copy, "labels.csv", labels)
    # Only the fake group is of interest; its real P01 rows are too few to be judged.
    fake = [g for g in audit(copy).groups if g.group == "P01 positive"][0]

    assert fake.rows >= 400
    assert fake.flagged == [], [(v.view, v.tvd, v.noise) for v in fake.flagged]


# --- per-transaction ground truth -----------------------------------------------------


def test_every_scenario_row_is_listed_and_every_listed_row_exists(dataset, world):
    listed = _rows(dataset, "label_transactions.csv")
    refs = {r["source_transaction_reference"] for r in world["transactions"]}
    scenario_ids = {l["scenario_id"] for l in world["labels"]}

    assert listed
    assert all(r["source_transaction_reference"] in refs for r in listed)
    assert {r["scenario_id"] for r in listed} <= scenario_ids
    assert len({r["source_transaction_reference"] for r in listed}) == len(listed)


def test_every_behavioural_scenario_has_rows_and_screening_has_none(world):
    """P12 is a renamed customer: no rows of its own. Every other positive or
    look-alike emitted at least one row."""
    for label in world["labels"]:
        if label["label_type"] == "CONTROL_CLEAN" or label["pattern_code"] == "ER":
            continue
        rows = world["rows_of"].get(label["scenario_id"], [])
        if label["pattern_code"] == "P12" and label["label_type"] == "INJECTED_POSITIVE":
            assert rows == [], label["scenario_id"]
        else:
            assert rows, label["scenario_id"]


def test_ground_truth_stays_out_of_the_manifest(dataset):
    manifest = json.loads((dataset / "manifest.json").read_text())

    assert "label_transactions.csv" not in {f["name"] for f in manifest["files"]}


# --- ATO --------------------------------------------------------------------------------


def _ato_labels(world, label_type):
    return [l for l in world["labels"] if l["pattern_code"] == "ATO" and l["label_type"] == label_type]


def _history_before(world, entity, moment):
    return [(when, row) for when, row in _parseable(world["by_entity"][entity]) if when < moment]


def test_ato_is_dormancy_then_a_never_used_device_then_an_outgoing_burst(world):
    positives = _ato_labels(world, "INJECTED_POSITIVE")
    assert len(positives) >= 5

    for label in positives:
        rows = sorted(_parseable(world["rows_of"][label["scenario_id"]]), key=lambda t: t[0])
        first, last = rows[0][0], rows[-1][0]
        before = _history_before(world, label["entity_source_id"], first)
        devices_before = {row["source_device_id"] for _, row in before}
        burst_devices = {row["source_device_id"] for _, row in rows if row["source_device_id"]}

        assert 5 <= len(rows) <= 15, label["scenario_id"]
        assert all(row["direction"] == "OUT" for _, row in rows)
        assert last - first <= timedelta(hours=7) and first.date() == last.date()
        # FRD §8.5 DORMANCY_DAYS: nothing in the 90 days before, but a history before that.
        assert all(first - when > timedelta(days=90) for when, _ in before), label["scenario_id"]
        assert before, f"{label['scenario_id']} has no history to take over"
        # One device, never used by this customer, first seen that day.
        assert len(burst_devices) == 1 and not (burst_devices & devices_before)
        device = world["devices"][burst_devices.pop()]
        assert datetime.fromisoformat(device["first_seen"]).date() == first.date()


def test_ato_look_alikes_carry_at_most_two_of_the_three_signals(world):
    """Each look-alike has some of dormancy / a never-used device / a burst,
    never all three, and all three kinds are present."""
    edges = _ato_labels(world, "EDGE_CASE")
    kinds = Counter(label["note"].split(":", 1)[0] for label in edges)

    assert set(kinds) == {kind for kind, _, _ in ATO_EDGE_KINDS}
    for label in edges:
        rows = sorted(_parseable(world["rows_of"][label["scenario_id"]]), key=lambda t: t[0])
        first = rows[0][0]
        before = _history_before(world, label["entity_source_id"], first)
        dormant = all(first - when > timedelta(days=90) for when, _ in before)
        devices_before = {row["source_device_id"] for _, row in before}
        new_device = any(row["source_device_id"] and row["source_device_id"] not in devices_before
                         for _, row in rows)
        burst = len(rows) >= 5 and rows[-1][0] - first <= timedelta(hours=7)

        assert not (dormant and new_device and burst), label["scenario_id"]


def test_ordinary_customers_change_phones_too(world):
    """So "a device never seen for this customer" is not unique to ATO."""
    labelled = {l["entity_source_id"] for l in world["labels"] if l["label_type"] != "CONTROL_CLEAN"}
    period_start = REFERENCE_DATE - timedelta(days=182)
    changers = total = 0
    for entity, rows in world["by_entity"].items():
        if entity in labelled or entity.startswith("BUS-"):
            continue
        total += 1
        first_use = {}
        for when, row in sorted(_parseable(rows), key=lambda t: t[0]):
            first_use.setdefault(row["source_device_id"], when)
        if any(when.date() > period_start + timedelta(days=30)
               and datetime.fromisoformat(world["devices"][device]["first_seen"]).date() > period_start
               for device, when in first_use.items() if device):
            changers += 1

    assert DEVICE_CHANGE_SHARE / 2 <= changers / total <= DEVICE_CHANGE_SHARE * 1.5


def test_only_a_few_devices_are_shared_outside_the_sharing_scenarios(world):
    """TRD §11.1: ~120 devices deliberately shared (scaled), not thousands."""
    scenario_refs = {r["source_transaction_reference"] for rows in world["rows_of"].values() for r in rows}
    users = defaultdict(set)
    for row in world["transactions"]:
        owner = world["owner"].get(row["source_account_id"])  # None: a deliberately unresolved account
        if owner and row["source_device_id"] and row["source_transaction_reference"] not in scenario_refs:
            users[row["source_device_id"]].add(owner)
    shared = sum(1 for owners in users.values() if len(owners) >= 2)
    factor = MIN_TARGET_TRANSACTIONS / 420_000

    assert shared <= 3 * max(1, round(SHARED_HOUSEHOLD_DEVICES * factor))


# --- counterparties (TRD §11.2, generator 1.3.0) --------------------------------------------


def _unlabelled(world):
    labelled = {l["entity_source_id"] for l in world["labels"] if l["label_type"] != "CONTROL_CLEAN"}
    return {e: rows for e, rows in world["by_entity"].items() if e not in labelled}


def test_retail_customers_pay_a_small_stable_set_of_counterparties(world):
    """TRD §11.2: "transfer ke sekumpulan counterparty kecil yang stabil"."""
    shares = []
    for entity, rows in _unlabelled(world).items():
        refs = [r["counterparty_reference"] for r in rows
                if not entity.startswith("BUS-") and r["transaction_type"] in ("P2P_TRANSFER", "BILL_PAYMENT", "WALLET_TOPUP")
                and r["counterparty_reference"]]
        if len(refs) >= 15:
            top = sum(count for _, count in Counter(refs).most_common(8))
            shares.append(top / len(refs))
    shares.sort()

    assert shares, "no retail customer with enough payments to judge"
    assert shares[len(shares) // 2] >= 0.6  # most of a customer's payments go to its regulars


def test_businesses_have_a_broad_payer_base(world):
    """TRD §11.2: "basis pembayar yang luas"."""
    ratios = []
    for entity, rows in _unlabelled(world).items():
        refs = [r["counterparty_reference"] for r in rows if entity.startswith("BUS-") and r["counterparty_reference"]]
        if len(refs) >= 10:
            ratios.append(len(set(refs)) / len(refs))
    ratios.sort()

    assert ratios and ratios[len(ratios) // 2] >= 0.25


def test_a_first_time_counterparty_is_ordinary_in_background(world):
    """So "a recipient never paid before" is not a signature of ATO alone,
    the same logic as ordinary phone changes."""
    burn_in = datetime.combine(REFERENCE_DATE - timedelta(days=182 - 60), datetime.min.time()).astimezone()
    scenario_refs = {r["source_transaction_reference"] for rows in world["rows_of"].values() for r in rows}
    new = total = 0
    for entity, rows in world["by_entity"].items():
        if entity.startswith("BUS-"):
            continue
        seen = set()
        for when, row in sorted(_parseable(rows), key=lambda t: t[0]):
            ref = row["counterparty_reference"]
            if not ref:
                continue
            if when >= burn_in and row["source_transaction_reference"] not in scenario_refs:
                total += 1
                new += ref not in seen
            seen.add(ref)

    assert 0.10 <= new / total <= 0.40


def test_ato_pays_only_recipients_its_victim_never_paid(world):
    for label in _ato_labels(world, "INJECTED_POSITIVE"):
        rows = sorted(_parseable(world["rows_of"][label["scenario_id"]]), key=lambda t: t[0])
        before = {row["counterparty_reference"] for _, row in _history_before(world, label["entity_source_id"], rows[0][0])}
        paid = {row["counterparty_reference"] for _, row in rows if row["counterparty_reference"]}

        assert paid and not (paid & before), label["scenario_id"]


def test_p05_leaves_history_before_its_dormancy(world):
    """FRD §8.5 DORMANCY_DAYS >= 90, with history in view before the gap."""
    for label in world["labels"]:
        if label["pattern_code"] != "P05" or label["label_type"] != "INJECTED_POSITIVE":
            continue
        start, end = date.fromisoformat(label["window_start"]), date.fromisoformat(label["window_end"])
        [credit] = world["rows_of"][label["scenario_id"]]
        quiet = [r for r in world["by_entity"][label["entity_source_id"]]
                 if r["source_account_id"] == credit["source_account_id"]
                 and start.isoformat() <= r["business_date"] < end.isoformat()]
        history = [r for r in world["by_entity"][label["entity_source_id"]] if r["business_date"] < start.isoformat()]

        assert (end - start).days >= 90 and quiet == [] and history, label["scenario_id"]


def test_a_ring_shares_its_own_device_not_a_bystanders(world):
    """P09 and P11 use a device of their own; 1.2.0 borrowed an uninvolved
    customer's, which made that customer look shared."""
    scenario_refs = {r["source_transaction_reference"] for rows in world["rows_of"].values() for r in rows}
    for scenario_id, rows in world["rows_of"].items():
        if not scenario_id.startswith(("P09-POS-", "P11-POS-")):
            continue
        devices = Counter(r["source_device_id"] for r in rows if r["source_device_id"])
        shared = devices.most_common(1)[0][0]
        outside = [r for r in world["transactions"]
                   if r["source_device_id"] == shared and r["source_transaction_reference"] not in scenario_refs]
        assert outside == [], scenario_id


# --- look-alikes now match their notes ----------------------------------------------------


def test_p06_look_alikes_are_round_as_their_note_says(world):
    for label in world["labels"]:
        if label["pattern_code"] == "P06" and label["scenario_id"].startswith("P06-EDGE-"):
            amounts = [float(r["amount_original"]) for r in world["rows_of"][label["scenario_id"]]]
            assert amounts and all(a % 1_000_000 == 0 for a in amounts), label["scenario_id"]


def test_p08_look_alikes_are_cross_border_as_their_note_says(world):
    for label in world["labels"]:
        if label["pattern_code"] == "P08" and label["scenario_id"].startswith("P08-EDGE-"):
            rows = world["rows_of"][label["scenario_id"]]
            assert rows and all(r["counterparty_country"] != "ID" and r["direction"] == "IN" for r in rows)


def test_p04_look_alikes_pay_payroll_on_payday_as_their_note_says(world):
    for label in world["labels"]:
        if label["pattern_code"] == "P04" and label["scenario_id"].startswith("P04-EDGE-"):
            first = min(date.fromisoformat(r["business_date"]) for r in world["rows_of"][label["scenario_id"]])
            assert first.day == 25, label["scenario_id"]


def test_p03_credits_arrive_at_any_hour(world):
    """1.1.0 put every P03 credit at 09:xx."""
    hours = {_when(r).hour for sid, rows in world["rows_of"].items() if sid.startswith("P03-POS-")
             for r in rows if r["direction"] == "IN"}

    assert len(hours) > 1


# --- the full profile --------------------------------------------------------------------


@pytest.mark.slow
def test_the_full_profile_is_free_of_scenario_artefacts_on_two_seeds(tmp_path):
    """The ML datasets: training seed 20260923 and test seed 20261001, both at
    the full profile. Run with `pytest -m slow`."""
    for seed in (20260923, 20261001):
        generator = RawDatasetGenerator(profile="full", seed=seed, reference_date=REFERENCE_DATE)
        generator.generate()
        out_dir = tmp_path / str(seed)
        report = generator.write(out_dir)
        result = audit(out_dir)

        assert result.flagged == [], (seed, [(g, v.view, v.tvd) for g, v in result.flagged])
        # TRD §11.1 device band, now that the total follows the population.
        assert 8_000 <= report["row_counts"]["devices.csv"] <= 12_000
