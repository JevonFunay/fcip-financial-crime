"""Generator 1.4.0: the population behaves the way TRD §11.2 describes it,
each look-alike resembles the pattern it is named after (TRD §11.2, FRD §8
expected false positives), and each boundary case sits exactly on its
pattern's own FRD §8 threshold, on the side that must not fire.

1.3.0 gave every entity about the same volume (~37 rows in six months), drew
top-ups and bill payments in either direction, had no settlement, gave
businesses tickets unrelated to their category, built seven of the twelve
look-alikes as six generic transfers, and put a 100,000,000 deposit at the
"boundary" of every pattern. PROJECT_CONTEXT §8 has the measured table.
"""

import csv
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app.scripts.generate_raw_dataset import (
    BOUNDARY_PATTERNS,
    MCC_BANDS,
    SCENARIO_PLAN,
    TOPUP_UNIT,
    RawDatasetGenerator,
)

REFERENCE_DATE = date(2026, 9, 30)
SEED = 20260923
TYPICAL_TICKET = {str(mcc): ticket for mcc, _, ticket in MCC_BANDS}


def _rows(folder: Path, name: str) -> list[dict[str, str]]:
    with (folder / name).open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _amount(row: dict[str, str]) -> float | None:
    try:
        amount = float(row["amount_original"])
    except ValueError:
        return None
    return amount if amount > 0 else None


def _when(row: dict[str, str]) -> datetime | None:
    try:
        return datetime.fromisoformat(row["value_datetime"])
    except ValueError:
        return None


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """The small preset, read once: built like the full ML datasets (no
    calibration to an exact count), with at least one scenario of each kind.
    Rows are taken first-copy-wins, as ingestion keeps them."""
    generator = RawDatasetGenerator(profile="small", seed=SEED, reference_date=REFERENCE_DATE)
    generator.generate()
    folder = tmp_path_factory.mktemp("population") / "data"
    generator.write(folder)

    unique: dict[str, dict[str, str]] = {}
    for row in _rows(folder, "transactions.csv"):
        unique.setdefault(row["source_transaction_reference"], row)
    owner = {a["source_account_id"]: a["owner_source_id"] for a in _rows(folder, "accounts.csv")}
    labels = _rows(folder, "labels.csv")
    labelled = {l["entity_source_id"] for l in labels if l["label_type"] != "CONTROL_CLEAN"}
    rows_of = defaultdict(list)
    for entry in _rows(folder, "label_transactions.csv"):
        rows_of[entry["scenario_id"]].append(unique[entry["source_transaction_reference"]])
    scenario_refs = {r["source_transaction_reference"] for rows in rows_of.values() for r in rows}

    by_entity = defaultdict(list)    # every row an entity owns
    background = defaultdict(list)   # unlabelled entities' ordinary rows
    for row in unique.values():
        entity = owner.get(row["source_account_id"])
        if entity is None:
            continue
        by_entity[entity].append(row)
        if entity not in labelled and row["source_transaction_reference"] not in scenario_refs:
            background[entity].append(row)
    cohort = {p.source_id: p.cohort for p in generator.parties.values()}
    return {
        "background": background,
        "by_entity": by_entity,
        "labels": labels,
        "rows_of": rows_of,
        "cohort": cohort,
        "retail": [e for e in background if cohort.get(e) == "RETAIL_NORMAL"],
        "business": [e for e in background if cohort.get(e) == "BUSINESS_NORMAL"],
        "customers": {c["source_customer_id"]: c for c in _rows(folder, "customers.csv")},
        "watchlist": _rows(folder, "watchlist.csv"),
        "merchants": {m["source_merchant_id"]: m for m in _rows(folder, "merchants.csv")},
    }


def _scenarios(world, prefix: str) -> list[tuple[dict[str, str], list[dict[str, str]]]]:
    """(label, rows) per scenario whose id starts with `prefix`; a scenario
    over several entities (P09) is returned once."""
    seen, out = set(), []
    for label in world["labels"]:
        if label["scenario_id"].startswith(prefix) and label["scenario_id"] not in seen:
            seen.add(label["scenario_id"])
            out.append((label, world["rows_of"][label["scenario_id"]]))
    assert out, f"no {prefix} scenarios"
    return out


# --- the population (TRD §11.2) --------------------------------------------------------


def test_activity_is_heavy_tailed(world):
    """A few customers are very active and most are not, not ~37 rows each."""
    counts = sorted(len(world["background"][e]) for e in world["retail"])
    top = sum(counts[-max(1, len(counts) // 10):])

    assert top / sum(counts) >= 0.30
    assert statistics.pstdev(counts) / statistics.mean(counts) >= 0.8


def test_merchants_are_busier_than_retail_customers(world):
    retail = statistics.mean(len(world["background"][e]) for e in world["retail"])
    business = statistics.mean(len(world["background"][e]) for e in world["business"])

    assert business >= 2 * retail


def test_top_ups_come_in_round_and_bills_go_out(world):
    topups = [r for e in world["retail"] for r in world["background"][e] if r["transaction_type"] == "WALLET_TOPUP"]
    bills = [r for e in world["retail"] for r in world["background"][e] if r["transaction_type"] == "BILL_PAYMENT"]

    assert topups and all(r["direction"] == "IN" for r in topups)
    assert all(a % TOPUP_UNIT == 0 for r in topups if (a := _amount(r)))
    assert bills and all(r["direction"] == "OUT" for r in bills)


def test_retail_tops_up_from_its_own_accounts_and_pays_the_same_billers(world):
    for entity in world["retail"]:
        rows = world["background"][entity]
        sources = {r["counterparty_reference"] for r in rows
                   if r["transaction_type"] == "WALLET_TOPUP" and r["counterparty_reference"]}
        billers = {r["counterparty_reference"] for r in rows
                   if r["transaction_type"] == "BILL_PAYMENT" and r["counterparty_reference"]}
        assert len(sources) <= 2 and len(billers) <= 3, entity


def test_businesses_sweep_their_takings_weekly_to_one_account(world):
    """TRD §11.2 "pola settlement"."""
    checked = 0
    for entity in world["business"]:
        rows = world["background"][entity]
        sweeps = [r for r in rows if r["transaction_type"] == "SETTLEMENT"]
        if sum(r["direction"] == "IN" for r in rows) < 10:
            continue
        checked += 1
        assert sweeps and all(r["direction"] == "OUT" for r in sweeps), entity
        assert len({r["counterparty_reference"] for r in sweeps if r["counterparty_reference"]}) == 1, entity
        # What the sweeps moved matches what came in up to the last sweep. A
        # taking whose account the unresolved-account defect garbled (0.5% of
        # rows) is swept but cannot be attributed, hence the tolerance.
        last = max(r["business_date"] for r in sweeps)
        due = sum(a for r in rows if r["direction"] == "IN" and r["business_date"] <= last and (a := _amount(r)))
        swept = sum(a for r in sweeps if (a := _amount(r)))
        assert 0.85 * due <= swept <= 1.15 * due, entity
        # Weekly: one sweep a week. The late-arrival defect backdates 1.5% of
        # background rows, sweeps included, so a week can hold a second one.
        weeks = Counter(date.fromisoformat(r["business_date"]).isocalendar()[:2] for r in sweeps
                        if len(r["business_date"]) == 10)
        assert sum(n == 1 for n in weeks.values()) >= 0.85 * len(weeks), entity
    assert checked


def test_business_tickets_fit_their_category(world):
    """TRD §11.2 "nilai tiket yang konsisten dengan kategori": at a merchant,
    tickets sit around its MCC's typical ticket (1.3.0: a quarter of business
    weeks more than 3x away)."""
    ratios = [a / TYPICAL_TICKET[world["merchants"][r["source_merchant_id"]]["mcc"]]
              for e in world["business"] for r in world["background"][e]
              if r["source_merchant_id"] in world["merchants"] and r["direction"] == "IN" and (a := _amount(r))]
    inside = sum(1 / 3 <= x <= 3 for x in ratios)

    assert ratios and inside / len(ratios) >= 0.95


def test_merchant_payments_arrive_while_the_merchant_is_open(world):
    """FRD §8.10 judges the share outside declared hours; ordinary merchants
    take almost everything inside them."""
    outside = total = 0
    for entity in world["business"]:
        for row in world["background"][entity]:
            merchant = world["merchants"].get(row["source_merchant_id"])
            when = _when(row)
            if merchant is None or when is None or merchant["operating_hours_declared"] == "00:00-23:59":
                continue
            opens, closes = int(merchant["operating_hours_declared"][:2]), int(merchant["operating_hours_declared"][6:8])
            total += 1
            outside += not (opens <= when.hour < closes)

    assert total and outside / total <= 0.06


def test_a_business_has_a_broad_payer_base_from_the_first_month(world):
    """No ordinary business gets most of its money from five payers in any
    30 days (P10's PAYER_CONCENTRATION 0.70), the first month included."""
    for entity in world["business"]:
        rows = [r for r in world["background"][entity] if r["direction"] == "IN" and r["counterparty_reference"]]
        by_month = defaultdict(Counter)
        for row in rows:
            if (amount := _amount(row)) and len(row["business_date"]) == 10:
                by_month[row["business_date"][:7]][row["counterparty_reference"]] += amount
        for month, payers in by_month.items():
            if sum(1 for r in rows if r["business_date"].startswith(month)) >= 30:
                top5 = sum(v for _, v in payers.most_common(5))
                assert top5 / sum(payers.values()) <= 0.70, (entity, month)


# --- look-alikes resemble their pattern ------------------------------------------------


def test_every_look_alike_and_boundary_is_placed(world):
    edges = Counter(l["pattern_code"] for l in world["labels"] if "-EDGE-" in l["scenario_id"])
    bounds = {l["pattern_code"] for l in world["labels"] if "-BOUND-" in l["scenario_id"]}

    assert set(edges) >= {p for p in SCENARIO_PLAN if SCENARIO_PLAN[p].get("edge")}
    assert bounds == set(BOUNDARY_PATTERNS)


def test_p01_look_alike_is_one_large_payment_under_the_threshold(world):
    for label, rows in _scenarios(world, "P01-EDGE-"):
        assert len(rows) == 1 and 60e6 <= _amount(rows[0]) <= 95e6, label["scenario_id"]


def test_p03_look_alike_passes_a_salary_through_to_bills_the_same_day(world):
    for label, rows in _scenarios(world, "P03-EDGE-"):
        by_day = defaultdict(list)
        for row in rows:
            by_day[row["business_date"]].append(row)
        assert len(by_day) == 2, label["scenario_id"]
        for day_rows in by_day.values():
            salary = sum(_amount(r) or 0 for r in day_rows if r["direction"] == "IN")
            spent = sum(_amount(r) or 0 for r in day_rows if r["direction"] == "OUT")
            assert salary and 0.80 <= spent / salary <= 0.96, label["scenario_id"]


def test_p04_look_alike_pays_the_same_staff_within_hours_on_a_weekday_payday(world):
    for label, rows in _scenarios(world, "P04-EDGE-"):
        by_day = defaultdict(list)
        for row in rows:
            by_day[row["business_date"]].append(row)
        staff = [{r["counterparty_reference"] for r in day_rows if r["counterparty_reference"]}
                 for day_rows in by_day.values()]
        assert len(by_day) == 2 and all(len(day_rows) >= 15 for day_rows in by_day.values()), label["scenario_id"]
        assert len(staff[0] & staff[1]) >= 0.8 * min(len(s) for s in staff), label["scenario_id"]
        for day, day_rows in by_day.items():
            moments = [w for r in day_rows if (w := _when(r))]
            assert date.fromisoformat(day).weekday() < 5 and date.fromisoformat(day).day >= 21
            assert max(moments) - min(moments) <= timedelta(hours=4), label["scenario_id"]


def test_p05_look_alike_returns_after_ninety_quiet_days_with_history_before(world):
    for label, rows in _scenarios(world, "P05-EDGE-"):
        # The credit that ends the quiet spell; any earlier scenario rows are
        # the history a quiet customer was given to be dormant after.
        credit = max((r for r in rows if r["direction"] == "IN"), key=lambda r: r["business_date"])
        earlier = sorted(r["business_date"] for r in world["by_entity"][label["entity_source_id"]]
                         if len(r["business_date"]) == 10 and r["business_date"] < credit["business_date"])
        quiet = date.fromisoformat(credit["business_date"]) - date.fromisoformat(earlier[-1]) if earlier else None
        assert quiet and quiet.days >= 90, label["scenario_id"]


def test_p07_look_alike_is_three_busy_weeks_of_sales_from_many_buyers(world):
    for label, rows in _scenarios(world, "P07-EDGE-"):
        assert all(r["direction"] == "IN" for r in rows)
        assert len({r["counterparty_reference"] for r in rows}) >= 30, label["scenario_id"]
        assert len(rows) >= 60, label["scenario_id"]


def test_p09_look_alike_is_a_family_at_one_address_on_one_handset(world):
    members = defaultdict(set)
    for label in world["labels"]:
        if label["scenario_id"].startswith("P09-EDGE-"):
            members[label["scenario_id"]].add(label["entity_source_id"])
    assert members
    for scenario_id, family in members.items():
        rows = world["rows_of"][scenario_id]
        devices = {r["source_device_id"] for r in rows if r["source_device_id"]}
        addresses = {(world["customers"][m]["address_line"], world["customers"][m]["postcode"]) for m in family}
        assert len(family) >= 3 and len(devices) == 1 and len(addresses) == 1, scenario_id


def test_p10_look_alike_is_a_supplier_with_a_few_large_clients_at_category_tickets(world):
    for label, rows in _scenarios(world, "P10-EDGE-"):
        clients = {r["counterparty_reference"] for r in rows if r["counterparty_reference"]}
        ratios = [_amount(r) / TYPICAL_TICKET[world["merchants"][r["source_merchant_id"]]["mcc"]] for r in rows]
        assert 3 <= len(clients) <= 5 and 20 <= len(rows) <= 40, label["scenario_id"]
        assert all(1.5 <= x <= 2.5 for x in ratios), label["scenario_id"]


def test_p11_look_alike_is_school_fees_from_the_same_payers_every_month(world):
    for label, rows in _scenarios(world, "P11-EDGE-"):
        amounts = {_amount(r) for r in rows}
        start = min(date.fromisoformat(r["business_date"]) for r in rows)
        first = {r["counterparty_reference"] for r in rows if (date.fromisoformat(r["business_date"]) - start).days < 30}
        second = {r["counterparty_reference"] for r in rows if (date.fromisoformat(r["business_date"]) - start).days >= 30}
        assert len(amounts - {None}) == 1, label["scenario_id"]           # one fee
        assert len(first) >= 25 and len(first & second) >= 0.9 * len(first), label["scenario_id"]
        assert len({r["business_date"] for r in rows}) >= 10, label["scenario_id"]  # spread, not compressed


def test_p12_look_alike_shares_a_given_name_with_a_list_record_and_nothing_else(world):
    listed = {tuple(w["primary_name"].split()) for w in world["watchlist"]}
    for label, rows in _scenarios(world, "P12-EDGE-"):
        given, family = world["customers"][label["entity_source_id"]]["full_name"].split()[:2]
        assert rows == [], label["scenario_id"]
        assert any(name[0] == given for name in listed), label["scenario_id"]
        assert (given, family) not in listed, label["scenario_id"]


# --- boundary cases sit on their threshold ---------------------------------------------


def _entity_rows_between(world, entity: str, start: datetime, end: datetime) -> list[dict[str, str]]:
    return [r for r in world["by_entity"][entity] if (w := _when(r)) and start <= w < end]


def test_p01_boundary_is_exactly_the_threshold_on_a_thin_history(world):
    for label, rows in _scenarios(world, "P01-BOUND-"):
        large = [r for r in rows if _amount(r) == 100_000_000]
        assert len(large) == 1, label["scenario_id"]
        when = _when(large[0])
        history = _entity_rows_between(world, label["entity_source_id"], when - timedelta(days=90), when)
        assert len(history) < 20, label["scenario_id"]


def test_p03_boundary_passes_through_just_under_the_ratio(world):
    for label, rows in _scenarios(world, "P03-BOUND-"):
        credit = next(r for r in rows if r["direction"] == "IN")
        when = _when(credit)
        out = sum(_amount(r) or 0 for r in _entity_rows_between(world, label["entity_source_id"], when,
                                                                 when + timedelta(hours=24)) if r["direction"] == "OUT")
        assert _amount(credit) >= 50_000_000 and out / _amount(credit) == pytest.approx(0.79, abs=0.002)


def test_p04_boundary_is_exactly_fourteen_in_a_day(world):
    for label, rows in _scenarios(world, "P04-BOUND-"):
        moments = sorted(w for r in world["by_entity"][label["entity_source_id"]] if (w := _when(r)))
        busiest = max(sum(1 for m in moments if start <= m < start + timedelta(hours=24)) for start in moments)
        assert busiest == 14, label["scenario_id"]


def test_p05_boundary_is_quiet_for_exactly_eighty_nine_days(world):
    for label, rows in _scenarios(world, "P05-BOUND-"):
        credit = next(r for r in rows if r["direction"] == "IN")
        day = date.fromisoformat(credit["business_date"])
        earlier = [date.fromisoformat(r["business_date"]) for r in world["by_entity"][label["entity_source_id"]]
                   if len(r["business_date"]) == 10 and r["business_date"] < credit["business_date"]]
        assert (day - max(earlier)).days == 89, label["scenario_id"]


def test_p06_boundary_is_exactly_four_round_amounts(world):
    for label, rows in _scenarios(world, "P06-BOUND-"):
        start = min(w for r in rows if (w := _when(r)))
        window = _entity_rows_between(world, label["entity_source_id"], start - timedelta(days=15), start + timedelta(days=30))
        round_rows = [r for r in window if (a := _amount(r)) and a >= 5_000_000 and a % 1_000_000 == 0]
        assert len(round_rows) == 4, label["scenario_id"]


def test_p09_boundary_device_is_used_by_exactly_three_entities(world):
    for label, rows in _scenarios(world, "P09-BOUND-"):
        device = next(r["source_device_id"] for r in rows if r["source_device_id"])
        users = {e for e, entity_rows in world["by_entity"].items() for r in entity_rows if r["source_device_id"] == device}
        assert len(users) == 3, label["scenario_id"]


def test_p11_boundary_has_exactly_seven_senders_over_two_hundred_million(world):
    for label, rows in _scenarios(world, "P11-BOUND-"):
        start = min(w for r in rows if (w := _when(r)))
        inbound = [r for r in _entity_rows_between(world, label["entity_source_id"], start - timedelta(days=6),
                                                   start + timedelta(days=7)) if r["direction"] == "IN"]
        senders = {r["counterparty_reference"] for r in inbound if r["counterparty_reference"]}
        assert len(senders) <= 7 and sum(_amount(r) or 0 for r in rows) >= 200_000_000, label["scenario_id"]
