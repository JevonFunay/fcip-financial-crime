"""P01 and P03-P10 (FRD §8, TRD §9.3). Each pattern has five kinds of test: a positive,
a look-alike negative, the exact boundary, a complete explanation, and what happens
when there is too little data. Parameters are the seeded ACTIVE versions, so these
tests also pin the defaults the FRD proposes.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.models.enums import EntityType
from pattern_world import M, MONDAY, World, run_evaluation, seeded_params

H = timedelta(hours=1)
D = timedelta(days=1)


def _alerted(evaluation):
    return {cand.customer_ref for cand in evaluation.candidates}


# ============================================================ P01 large transaction

P01 = "P01_LARGE_TXN"


def _history(world, account, *, count, amount, end, spacing=D):
    for i in range(count):
        world.tx(account, end - spacing * (i + 1), amount)


def test_p01_positive_absolute_and_relative(db):
    w = World(db)
    person = w.customer("P01-POS")
    acct = w.account(person)
    _history(w, acct, count=25, amount=8 * M, end=MONDAY + 60 * D)
    big = w.tx(acct, MONDAY + 60 * D, 120 * M)
    w.done()
    ev = run_evaluation(db, P01)
    [cand] = ev.candidates
    assert cand.transactions == (next(t for t in cand.transactions if t.ref == big.transaction_ref),)
    assert cand.factors["CONDITIONS_MET"] == ["ABSOLUTE", "RELATIVE"]
    assert cand.factors["SUGGESTED_SEVERITY"] == "HIGH"


def test_p01_merchant_flagged_on_the_relative_condition_only(db):
    w = World(db)
    biz = w.customer("P01-BIZ", EntityType.BUSINESS)
    acct = w.account(biz)
    _history(w, acct, count=25, amount=40 * M, end=MONDAY + 60 * D)
    w.tx(acct, MONDAY + 60 * D, 260 * M)
    w.done()
    [cand] = run_evaluation(db, P01).candidates
    assert cand.factors["CONDITIONS_MET"] == ["RELATIVE"]
    assert cand.factors["SUGGESTED_SEVERITY"] == "MEDIUM"


def test_p01_negative_a_customer_whose_history_is_all_large(db):
    w = World(db)
    biz = w.customer("P01-NEG", EntityType.BUSINESS)
    acct = w.account(biz)
    _history(w, acct, count=25, amount=300 * M, end=MONDAY + 60 * D)
    w.tx(acct, MONDAY + 60 * D, 400 * M)  # 1.33x its own p95 and under the merchant threshold
    w.done()
    ev = run_evaluation(db, P01)
    assert ev.candidates == []


def test_p01_boundary_exactly_at_the_absolute_threshold_is_flagged(db):
    w = World(db)
    on, under = w.customer("P01-ON"), w.customer("P01-UNDER")
    w.tx(w.account(on), MONDAY, 100 * M)
    w.tx(w.account(under), MONDAY, Decimal("99999999.99"))
    w.done()
    assert _alerted(run_evaluation(db, P01)) == {"P01-ON"}


def test_p01_explanation_carries_value_threshold_and_evidence(db):
    w = World(db)
    person = w.customer("P01-EXPL")
    acct = w.account(person)
    w.tx(acct, MONDAY, 150 * M, counterparty="CP-1", channel="TRANSFER_INTERNAL")
    w.done()
    [cand] = run_evaluation(db, P01).candidates
    f = cand.factors
    assert f["AMOUNT_IDR"] == "150000000.00" and f["ABS_THRESHOLD_APPLIED"] == "100000000.00"
    assert f["COUNTERPARTY_ID"] == "CP-1" and f["CHANNEL"] == "TRANSFER_INTERNAL"
    assert len(cand.transactions) == 1 and cand.reason.startswith("P01")


def test_p01_without_enough_history_the_relative_condition_is_not_judged(db):
    w = World(db)
    person = w.customer("P01-THIN")
    acct = w.account(person)
    _history(w, acct, count=8, amount=5 * M, end=MONDAY + 30 * D)
    w.tx(acct, MONDAY + 30 * D, 60 * M)  # far above 5x its p95, but only 8 earlier transactions
    w.done()
    ev = run_evaluation(db, P01)
    assert ev.candidates == []
    assert ev.notes  # "not judged" is reported, not silent


# ============================================================ P03 rapid movement

P03 = "P03_RAPID_MOVEMENT"


def test_p03_positive_credit_leaves_within_hours(db):
    w = World(db)
    acct = w.account(w.customer("P03-POS"))
    w.tx(acct, MONDAY, 300 * M, direction="CREDIT")
    for i in range(3):
        w.tx(acct, MONDAY + 2 * H * (i + 1), 95 * M, counterparty=f"CP-{i}")
    w.done()
    [cand] = run_evaluation(db, P03).candidates
    assert len(cand.transactions) == 4
    assert cand.factors["PASSTHROUGH_RATIO"] == "0.9500"
    assert cand.factors["OUTBOUND_COUNTERPARTY_COUNT"] == 3
    assert cand.factors["RESIDUAL_BALANCE_RATIO"] == "0.0500"


def test_p03_negative_small_outflow_or_too_late(db):
    w = World(db)
    small, late = w.account(w.customer("P03-SMALL")), w.account(w.customer("P03-LATE"))
    w.tx(small, MONDAY, 300 * M, direction="CREDIT")
    w.tx(small, MONDAY + 5 * H, 40 * M)
    w.tx(late, MONDAY, 300 * M, direction="CREDIT")
    w.tx(late, MONDAY + 40 * H, 290 * M)
    w.done()
    assert run_evaluation(db, P03).candidates == []


def test_p03_boundaries_ratio_elapsed_time_and_midnight(db):
    w = World(db)
    exact, short, edge, late = (w.account(w.customer(n)) for n in ("P03-EXACT", "P03-SHORT", "P03-24H", "P03-LATE"))
    w.tx(exact, MONDAY, 300 * M, direction="CREDIT")
    w.tx(exact, MONDAY + H, 240 * M)                      # exactly 0.80
    w.tx(short, MONDAY, 300 * M, direction="CREDIT")
    w.tx(short, MONDAY + H, Decimal("239999999.99"))      # a cent under
    w.tx(edge, MONDAY, 300 * M, direction="CREDIT")
    w.tx(edge, MONDAY + 24 * H, 290 * M)                  # exactly 24 hours later
    w.tx(late, MONDAY, 300 * M, direction="CREDIT")
    w.tx(late, MONDAY + 24 * H + timedelta(seconds=1), 290 * M)
    w.done()
    assert _alerted(run_evaluation(db, P03)) == {"P03-EXACT", "P03-24H"}


def test_p03_rolling_window_is_not_cut_by_the_calendar_day(db):
    w = World(db)
    acct = w.account(w.customer("P03-MIDNIGHT"))
    w.tx(acct, MONDAY - 10 * 60 * timedelta(minutes=1), 100 * M, direction="CREDIT")  # 23:50 Jakarta
    w.tx(acct, MONDAY + 10 * timedelta(minutes=1), 95 * M)                              # 00:10 next day
    w.done()
    assert _alerted(run_evaluation(db, P03)) == {"P03-MIDNIGHT"}


def test_p03_a_debit_is_evidence_for_one_alert_only_and_inbound_below_minimum_is_ignored(db):
    w = World(db)
    acct = w.account(w.customer("P03-ONCE"))
    w.tx(acct, MONDAY, 100 * M, direction="CREDIT")
    w.tx(acct, MONDAY + H, 100 * M, direction="CREDIT")
    w.tx(acct, MONDAY + 2 * H, 100 * M)
    tiny = w.account(w.customer("P03-TINY"))
    w.tx(tiny, MONDAY, 40 * M, direction="CREDIT")   # under MIN_INBOUND_AMOUNT
    w.tx(tiny, MONDAY + H, 40 * M)
    w.done()
    ev = run_evaluation(db, P03)
    assert len(ev.candidates) == 1 and _alerted(ev) == {"P03-ONCE"}


# ============================================================ P04 high velocity

P04 = "P04_HIGH_VELOCITY"


def _daily(world, account, *, start, days, per_day, amount=1 * M):
    for d in range(days):
        for k in range(per_day(d) if callable(per_day) else per_day):
            world.tx(account, start + d * D + k * timedelta(minutes=10), amount)


def test_p04_positive_burst_against_a_steady_baseline(db):
    w = World(db)
    acct = w.account(w.customer("P04-POS"))
    _daily(w, acct, start=MONDAY, days=60, per_day=4)
    _daily(w, acct, start=MONDAY + 60 * D, days=1, per_day=38)
    w.done()
    [cand] = run_evaluation(db, P04).candidates
    assert cand.factors["TXN_COUNT_WINDOW"] == 38 and cand.factors["BASELINE_MEAN_COUNT"] == 4.0
    assert cand.factors["BASELINE_DAYS_AVAILABLE"] == 60


def test_p04_negative_below_the_floor_or_inside_normal_variation(db):
    w = World(db)
    low, noisy = w.account(w.customer("P04-LOW")), w.account(w.customer("P04-NOISY"))
    _daily(w, low, start=MONDAY, days=60, per_day=1)
    _daily(w, low, start=MONDAY + 60 * D, days=1, per_day=5)   # 1 -> 5 is a rise, but under the floor of 15
    _daily(w, noisy, start=MONDAY, days=60, per_day=lambda d: 11 if d % 2 else 17)
    _daily(w, noisy, start=MONDAY + 60 * D, days=1, per_day=17)  # at the floor, but within normal variation
    w.done()
    assert run_evaluation(db, P04).candidates == []


def test_p04_boundary_the_floor_is_inclusive(db):
    w = World(db)
    at, under = w.account(w.customer("P04-AT15")), w.account(w.customer("P04-AT14"))
    for acct, n in ((at, 15), (under, 14)):
        _daily(w, acct, start=MONDAY, days=40, per_day=1)
        _daily(w, acct, start=MONDAY + 40 * D, days=1, per_day=n)
    w.done()
    assert _alerted(run_evaluation(db, P04)) == {"P04-AT15"}


def test_p04_explanation_has_baseline_and_both_applied_thresholds(db):
    w = World(db)
    acct = w.account(w.customer("P04-EXPL"))
    _daily(w, acct, start=MONDAY, days=45, per_day=2)
    _daily(w, acct, start=MONDAY + 45 * D, days=1, per_day=20)
    w.done()
    [cand] = run_evaluation(db, P04).candidates
    for key in ("BASELINE_MEAN_COUNT", "BASELINE_SD_COUNT", "DEVIATION_SIGMA_APPLIED", "ALT_MULTIPLE_APPLIED",
                "MIN_COUNT_FLOOR_APPLIED", "VALUE_WINDOW", "BASELINE_DAYS_AVAILABLE", "CONDITIONS_MET"):
        assert key in cand.factors
    assert len(cand.transactions) == 20


def test_p04_with_too_little_history_the_window_is_not_judged_and_says_so(db):
    w = World(db)
    acct = w.account(w.customer("P04-NEW"))
    _daily(w, acct, start=MONDAY, days=20, per_day=1)
    _daily(w, acct, start=MONDAY + 20 * D, days=1, per_day=40)
    w.done()
    ev = run_evaluation(db, P04)
    assert ev.candidates == [] and ev.notes


# ============================================================ P05 dormant reactivation

P05 = "P05_DORMANT_REACTIVATION"


def test_p05_positive_long_silence_then_material_activity(db):
    w = World(db)
    acct = w.account(w.customer("P05-POS"))
    w.tx(acct, MONDAY, 2 * M, counterparty="OLD", device="DEV-OLD")
    back = MONDAY + 210 * D
    for i, amount in enumerate((60 * M, 60 * M, 60 * M)):
        w.tx(acct, back + i * D, amount, counterparty=f"NEW-{i % 2}", device="DEV-NEW")
    w.done()
    [cand] = run_evaluation(db, P05).candidates
    f = cand.factors
    assert f["DORMANCY_DAYS_OBSERVED"] == 210 and f["REACTIVATION_TXN_COUNT"] == 3
    assert f["NEW_COUNTERPARTY_COUNT"] == 2 and f["NEW_DEVICE_FLAG"] is True
    assert f["SUGGESTED_SEVERITY"] == "HIGH"


def test_p05_negative_short_gap_and_brand_new_account(db):
    w = World(db)
    short, new = w.account(w.customer("P05-SHORT")), w.account(w.customer("P05-NEW"))
    w.tx(short, MONDAY, 2 * M)
    w.tx(short, MONDAY + 85 * D, 2 * M)
    w.tx(new, MONDAY, 200 * M)   # the first transaction ever: new, not dormant
    w.done()
    assert run_evaluation(db, P05).candidates == []


def test_p05_boundary_exactly_ninety_days(db):
    w = World(db)
    exact, under = w.account(w.customer("P05-90")), w.account(w.customer("P05-89"))
    for acct, gap in ((exact, 90 * D), (under, 90 * D - timedelta(seconds=1))):
        w.tx(acct, MONDAY, 1 * M)
        w.tx(acct, MONDAY + gap, 30 * M)
    w.done()
    assert _alerted(run_evaluation(db, P05)) == {"P05-90"}


def test_p05_a_count_alone_is_enough_but_four_small_transactions_are_not(db):
    w = World(db)
    five, four = w.account(w.customer("P05-FIVE")), w.account(w.customer("P05-FOUR"))
    for acct, n in ((five, 5), (four, 4)):
        w.tx(acct, MONDAY, 1 * M)
        for i in range(n):
            w.tx(acct, MONDAY + 120 * D + i * H, 1 * M)
    w.done()
    assert _alerted(run_evaluation(db, P05)) == {"P05-FIVE"}


def test_p05_explanation_names_last_activity_and_reactivation_dates(db):
    w = World(db)
    acct = w.account(w.customer("P05-EXPL"))
    w.tx(acct, MONDAY, 1 * M)
    w.tx(acct, MONDAY + 100 * D, 40 * M)
    w.done()
    [cand] = run_evaluation(db, P05).candidates
    assert cand.factors["LAST_ACTIVITY_DATE"] == "2026-01-04"  # Sunday 17:00 UTC is Monday 00:00 in Jakarta
    assert cand.factors["REACTIVATION_DATE"] == "2026-04-14"
    assert cand.factors["SUGGESTED_SEVERITY"] == "MEDIUM"


# ============================================================ P06 round amounts

P06 = "P06_ROUND_AMOUNTS"


def _amounts(world, account, amounts, start=MONDAY, spacing=D):
    for i, a in enumerate(amounts):
        world.tx(account, start + spacing * i, a)


def test_p06_positive_most_qualifying_transactions_are_round(db):
    w = World(db)
    acct = w.account(w.customer("P06-POS"))
    _amounts(w, acct, [50 * M] * 9 + [Decimal("7345123"), Decimal("12310457")])
    w.done()
    [cand] = run_evaluation(db, P06).candidates
    assert cand.factors["ROUND_TXN_COUNT"] == 9 and cand.factors["TOTAL_QUALIFYING_TXN_COUNT"] == 11
    assert cand.factors["AMOUNT_SET"] == ["50000000.00"]


def test_p06_negative_round_values_are_common_but_not_dominant(db):
    w = World(db)
    topups, few = w.account(w.customer("P06-TOPUPS")), w.account(w.customer("P06-FEW"))
    _amounts(w, topups, [10 * M] * 5 + [Decimal(6_000_000 + 13_579 * i) for i in range(35)], spacing=timedelta(hours=12))
    _amounts(w, few, [10 * M] * 4)
    w.done()
    assert run_evaluation(db, P06).candidates == []


def test_p06_boundaries_exactness_count_and_proportion(db):
    w = World(db)
    near, six, five_of_nine = (w.account(w.customer(n)) for n in ("P06-NEAR", "P06-6OF10", "P06-5OF9"))
    _amounts(w, near, [Decimal("5000001")] * 8)                               # one over a round value: not round
    _amounts(w, six, [10 * M] * 6 + [Decimal(7_000_001 + i) for i in range(4)])   # 6/10 = 0.60 exactly
    _amounts(w, five_of_nine, [10 * M] * 5 + [Decimal(7_000_001 + i) for i in range(4)])  # 5/9 = 0.556
    w.done()
    assert _alerted(run_evaluation(db, P06)) == {"P06-6OF10"}


def test_p06_amounts_below_the_minimum_are_not_considered(db):
    w = World(db)
    acct = w.account(w.customer("P06-SMALL"))
    _amounts(w, acct, [1 * M] * 12)  # perfectly round, but under MIN_AMOUNT of 5,000,000
    w.done()
    assert run_evaluation(db, P06).candidates == []


def test_p06_explanation_shows_unit_count_and_proportion_applied(db):
    w = World(db)
    acct = w.account(w.customer("P06-EXPL"))
    _amounts(w, acct, [20 * M] * 6, )
    w.done()
    [cand] = run_evaluation(db, P06).candidates
    for key in ("ROUND_PROPORTION", "MIN_PROPORTION_APPLIED", "ROUNDING_UNIT_APPLIED", "DISTINCT_COUNTERPARTIES"):
        assert key in cand.factors
    assert cand.factors["SUGGESTED_SEVERITY"] == "LOW"


# ============================================================ P07 sudden spike

P07 = "P07_SUDDEN_SPIKE"
WEEK = 7 * D


def _weekly(world, account, values, start=MONDAY):
    for i, v in enumerate(values):
        if v:
            world.tx(account, start + i * WEEK + 2 * D + 5 * H, v)


def test_p07_positive_one_week_far_above_twelve_weeks_of_history(db):
    w = World(db)
    acct = w.account(w.customer("P07-POS"))
    _weekly(w, acct, [60 * M] * 12 + [520 * M])
    w.done()
    [cand] = run_evaluation(db, P07).candidates
    assert cand.factors["PERIOD_VALUE"] == "520000000.00" and cand.factors["BASELINE_MEAN_WEEKLY"] == "60000000.00"
    assert cand.factors["SPIKE_MULTIPLE"] == "8.67" and cand.factors["BASELINE_WEEKS_AVAILABLE"] == 12


def test_p07_negative_big_baseline_small_relative_rise(db):
    w = World(db)
    acct = w.account(w.customer("P07-NEG"))
    _weekly(w, acct, [400 * M] * 12 + [700 * M])
    w.done()
    assert run_evaluation(db, P07).candidates == []


def test_p07_boundary_both_conditions_are_inclusive(db):
    w = World(db)
    exact, under = w.account(w.customer("P07-EXACT")), w.account(w.customer("P07-UNDER"))
    _weekly(w, exact, [20 * M] * 12 + [120 * M])                  # +100M exactly and 6x
    _weekly(w, under, [20 * M] * 12 + [Decimal("119999999.99")])
    w.done()
    assert _alerted(run_evaluation(db, P07)) == {"P07-EXACT"}


def test_p07_weeks_are_calendar_weeks_in_jakarta_time(db):
    w = World(db)
    acct = w.account(w.customer("P07-WEEK"))
    _weekly(w, acct, [20 * M] * 12)
    # Sunday 23:30 Jakarta (16:30 UTC) belongs to the week that ends, Monday 00:30 Jakarta to the next one.
    w.tx(acct, MONDAY + 13 * WEEK - timedelta(hours=1, minutes=30), 60 * M)
    w.tx(acct, MONDAY + 12 * WEEK + 6 * D + 23 * H + 30 * timedelta(minutes=1) - 7 * H, 5 * M)
    w.done()
    [cand] = run_evaluation(db, P07, spike_multiple=Decimal(3), min_absolute_increase=Decimal(1)).candidates
    assert cand.window_start == MONDAY + 12 * WEEK  # the 60M falls in the week starting Monday


def test_p07_explanation_and_the_cap_on_cited_transactions(db):
    w = World(db)
    acct = w.account(w.customer("P07-EXPL"))
    _weekly(w, acct, [10 * M] * 12)
    for i in range(30):
        w.tx(acct, MONDAY + 12 * WEEK + D + i * H, 20 * M)
    w.done()
    [cand] = run_evaluation(db, P07).candidates
    assert len(cand.transactions) == 20 and cand.factors["WEEK_TXN_COUNT"] == 30
    assert len(cand.factors["TOP_CONTRIBUTING_TXN_IDS"]) == 20
    assert "BASELINE_P95_WEEKLY" in cand.factors


def test_p07_with_four_weeks_of_history_it_is_not_judged_and_says_so(db):
    w = World(db)
    acct = w.account(w.customer("P07-THIN"))
    _weekly(w, acct, [10 * M] * 4 + [900 * M])
    w.done()
    ev = run_evaluation(db, P07)
    assert ev.candidates == [] and ev.notes


# ============================================================ P08 high-risk geography

P08 = "P08_HIGH_RISK_GEO"


def _background(world, account, total, *, start=MONDAY, n=10):
    for i in range(n):
        world.tx(account, start + i * H, total / n, country="SG")


def test_p08_positive_repeated_transfers_to_listed_countries(db):
    w = World(db)
    acct = w.account(w.customer("P08-POS"))
    for i in range(6):
        w.tx(acct, MONDAY + i * 3 * D, 30 * M, country="KY", counterparty=f"CP-{i}")
    _background(w, acct, 400 * M, start=MONDAY + 30 * H)
    w.done()
    [cand] = run_evaluation(db, P08).candidates
    assert cand.factors["COUNTRY_CODES"] == ["KY"] and cand.factors["TXN_COUNT"] == 6
    assert cand.factors["AGGREGATE_AMOUNT"] == "180000000.00"


def test_p08_negative_one_small_listed_transfer_and_unlisted_countries(db):
    w = World(db)
    one, other = w.account(w.customer("P08-ONE")), w.account(w.customer("P08-OTHER"))
    w.tx(one, MONDAY, 5 * M, country="KY")
    _background(w, one, 400 * M, start=MONDAY + D)
    for i in range(8):
        w.tx(other, MONDAY + i * D, 40 * M, country="SG")   # same size and frequency, unlisted country
    w.done()
    assert run_evaluation(db, P08).candidates == []


def test_p08_boundaries_each_condition_is_inclusive(db):
    w = World(db)
    three, two, agg, share = (w.account(w.customer(n)) for n in ("P08-3", "P08-2", "P08-AGG", "P08-SHARE"))
    for acct, n in ((three, 3), (two, 2)):
        for i in range(n):
            w.tx(acct, MONDAY + i * D, 1 * M, country="PA")
        _background(w, acct, 1000 * M, start=MONDAY + 5 * D)
    w.tx(agg, MONDAY, 50 * M, country="VU")                       # exactly MIN_AGGREGATE, share 5%
    _background(w, agg, 950 * M, start=MONDAY + D)
    w.tx(share, MONDAY, 25 * M, country="SC")                     # exactly 25% of the subject's value
    _background(w, share, 75 * M, start=MONDAY + D)
    w.done()
    assert _alerted(run_evaluation(db, P08)) == {"P08-3", "P08-AGG", "P08-SHARE"}


def test_p08_explanation_names_the_list_version_countries_and_weights(db):
    w = World(db)
    acct = w.account(w.customer("P08-EXPL"))
    for i in range(3):
        w.tx(acct, MONDAY + i * D, 2 * M, country="BZ" if i else "KY")
    _background(w, acct, 600 * M, start=MONDAY + 5 * D)
    w.done()
    [cand] = run_evaluation(db, P08).candidates
    assert cand.factors["LIST_VERSION"] == "geo_list_v1"
    assert cand.factors["COUNTRY_CODES"] == ["BZ", "KY"]
    assert set(cand.factors["COUNTRY_WEIGHTS"]) == {"BZ", "KY"}
    assert cand.factors["EXPOSURE_SOURCE"] == "COUNTERPARTY_COUNTRY"


def test_p08_ip_country_is_reported_as_unavailable_not_invented(db):
    w = World(db)
    acct = w.account(w.customer("P08-IP"))
    w.tx(acct, MONDAY, 60 * M, country="KY")
    w.done()
    [cand] = run_evaluation(db, P08).candidates
    assert cand.factors["IP_VS_REGISTERED_MISMATCH"] == "DATA_UNAVAILABLE"


# ============================================================ P09 shared device

P09 = "P09_SHARED_DEVICE"


def _device_users(world, device, count, *, accounts_each=1, start=MONDAY, prefix="U"):
    people = []
    for i in range(count):
        person = world.customer(f"{prefix}-{i}")
        for a in range(accounts_each):
            acct = world.account(person)
            world.tx(acct, start + i * D + a * H, 1 * M, device=device)
        people.append(person)
    return people


def test_p09_positive_one_device_many_entities_and_accounts(db):
    w = World(db)
    _device_users(w, "DEV-X", 5, accounts_each=1, prefix="P09-POS")
    heavy = w.customer("P09-HEAVY")          # the entity with most transactions becomes the subject
    acct = w.account(heavy)
    for i in range(4):
        w.tx(acct, MONDAY + 6 * D + i * H, 1 * M, device="DEV-X")
    w.done()
    [cand] = run_evaluation(db, P09).candidates
    assert cand.customer_ref == "P09-HEAVY"
    assert cand.factors["DISTINCT_ENTITY_COUNT"] == 6 and cand.factors["DEVICE_ID"] == "DEV-X"


def test_p09_negative_three_entities_do_not_reach_the_minimum(db):
    w = World(db)
    _device_users(w, "DEV-FEW", 3, prefix="P09-NEG")
    w.done()
    assert run_evaluation(db, P09).candidates == []


def test_p09_boundaries_four_entities_or_six_accounts(db):
    w = World(db)
    _device_users(w, "DEV-4", 4, prefix="P09-FOUR")
    _device_users(w, "DEV-3x5", 3, accounts_each=2, prefix="P09-THREE")      # 3 entities, 6 accounts
    _device_users(w, "DEV-3x1", 3, accounts_each=1, prefix="P09-QUIET")      # 3 entities, 3 accounts
    one = w.customer("P09-ONEPERSON")
    for i in range(6):                                                        # 1 entity, 6 accounts
        w.tx(w.account(one), MONDAY + i * H, 1 * M, device="DEV-6ACCT")
    w.done()
    ev = run_evaluation(db, P09)
    assert {c.factors["DEVICE_ID"] for c in ev.candidates} == {"DEV-4", "DEV-3x5", "DEV-6ACCT"}


def test_p09_entities_spread_over_more_than_the_window_do_not_count_together(db):
    w = World(db)
    for i in range(4):
        w.tx(w.account(w.customer(f"P09-SLOW-{i}")), MONDAY + i * 11 * D, 1 * M, device="DEV-SLOW")
    w.done()
    assert run_evaluation(db, P09).candidates == []


def test_p09_explanation_and_the_context_it_cannot_yet_assess(db):
    w = World(db)
    _device_users(w, "DEV-EXPL", 4, prefix="P09-EXPL")
    w.done()
    [cand] = run_evaluation(db, P09).candidates
    assert cand.factors["ENTITY_IDS"] == sorted(f"P09-EXPL-{i}" for i in range(4))
    assert cand.factors["HOUSEHOLD_RELATION_COUNT"] == "DATA_UNAVAILABLE"
    assert cand.factors["SPECIFICITY_FLAG"] == "UNASSESSED"
    assert len(cand.transactions) == 4


# ============================================================ P10 unusual merchant

P10 = "P10_UNUSUAL_MERCHANT"


def _merchant_world(db, ref="MER-1", **merchant_kwargs):
    w = World(db)
    owner = w.customer(f"OWN-{ref}", EntityType.BUSINESS)
    w.merchant(ref, owner, **merchant_kwargs)
    payers = [w.account(w.customer(f"PAYER-{ref}-{i}")) for i in range(10)]
    return w, owner, payers


def _sales(world, ref, payers, *, count, amount, start=MONDAY + 10 * H, status_for=lambda i: None, hour_step=timedelta(hours=1)):
    for i in range(count):
        world.tx(payers[i % len(payers)], start + (i // 8) * D + (i % 8) * hour_step, amount, merchant=ref,
                 status=status_for(i))


def test_p10_positive_ticket_far_above_the_declared_band(db):
    w, owner, payers = _merchant_world(db)
    _sales(w, "MER-1", payers, count=40, amount=100_000)          # 4x the 25,000 midpoint of LT_50K
    w.done()
    [cand] = run_evaluation(db, P10).candidates
    assert cand.customer_ref == owner.customer_ref                 # the alert belongs to the merchant's owner
    assert cand.factors["SUB_CONDITION_MET"] == ["TICKET"] and cand.factors["TICKET_MULTIPLE"] == "4.00"


def test_p10_positive_reversal_rate_and_payer_concentration_and_off_hours(db):
    w, _, payers = _merchant_world(db, "MER-R")
    _sales(w, "MER-R", payers, count=40, amount=20_000, status_for=lambda i: "REVERSED" if i < 8 else None)
    w2, _, payers2 = _merchant_world(db, "MER-C")
    big = payers2[:5]
    for i in range(40):
        w2.tx(big[i % 5], MONDAY + 10 * H + i * H, 20_000, merchant="MER-C")
    for p in payers2[5:7]:
        w2.tx(p, MONDAY + 11 * H, 1_000, merchant="MER-C")
    w3, _, payers3 = _merchant_world(db, "MER-N")
    _sales(w3, "MER-N", payers3, count=40, amount=20_000, start=MONDAY + 1 * H, hour_step=timedelta(minutes=1))  # 01:00 Jakarta
    w3.done()
    met = {c.factors["MERCHANT_ID"]: c.factors["SUB_CONDITION_MET"] for c in run_evaluation(db, P10).candidates}
    assert met["MER-R"] == ["REFUND"] and "CONCENTRATION" in met["MER-C"] and met["MER-N"] == ["OFF_HOURS"]


def test_p10_negative_an_ordinary_merchant_and_one_with_too_few_transactions(db):
    w, _, payers = _merchant_world(db, "MER-OK")
    _sales(w, "MER-OK", payers, count=40, amount=20_000)
    _, _, few_payers = _merchant_world(db, "MER-FEW")
    _sales(w, "MER-FEW", few_payers, count=18, amount=900_000, status_for=lambda i: "REVERSED")
    w.done()
    assert run_evaluation(db, P10).candidates == []


def test_p10_boundary_the_reversal_rate_must_exceed_not_equal(db):
    w, _, payers = _merchant_world(db, "MER-6")
    _sales(w, "MER-6", payers, count=40, amount=20_000, status_for=lambda i: "REVERSED" if i < 6 else None)   # 0.15
    w2, _, payers2 = _merchant_world(db, "MER-7")
    _sales(w2, "MER-7", payers2, count=40, amount=20_000, status_for=lambda i: "REVERSED" if i < 7 else None)  # 0.175
    w2.done()
    assert {c.factors["MERCHANT_ID"] for c in run_evaluation(db, P10).candidates} == {"MER-7"}


def test_p10_explanation_lists_what_fired_and_what_could_not_be_judged(db):
    w, _, payers = _merchant_world(db, band=None, hours=None)    # no declared band or hours
    _sales(w, "MER-1", payers, count=40, amount=20_000, status_for=lambda i: "REVERSED" if i < 10 else None)
    w.done()
    [cand] = run_evaluation(db, P10).candidates
    assert cand.factors["SUB_CONDITION_MET"] == ["REFUND"]
    assert set(cand.factors["SUB_CONDITION_NOT_JUDGED"]) == {"TICKET", "OFF_HOURS"}
    assert cand.factors["MCC"] == "5411" and cand.factors["TXN_COUNT_WINDOW"] == 40


# ============================================================ every template: parameters and runs

PATTERNS = [P01, P03, P04, P05, P06, P07, P08, P09, P10]


@pytest.mark.parametrize("code", PATTERNS)
def test_each_pattern_is_seeded_active_with_parameters_its_template_accepts(db, code):
    params = seeded_params(db, code)
    assert params.currency == "IDR"


@pytest.mark.parametrize("code", PATTERNS)
def test_a_rule_version_missing_a_parameter_is_refused_not_approximated(db, code):
    from copy import copy

    from app.services.detection.active_rules import active_rule_versions
    from app.services.detection.registry import TEMPLATES

    [(_, version)] = active_rule_versions(db, code)
    db.expunge(version)
    broken = copy(version)
    broken.parameters = {k: v for k, v in version.parameters.items() if k != "currency"}
    with pytest.raises(ValueError, match="currency"):
        TEMPLATES[code].parameters_from(broken)


def test_a_run_raises_an_alert_with_audit_and_is_idempotent(db):
    from sqlalchemy import select

    from app.models.alert import Alert, AlertTransaction
    from app.services.detection.common import run_pattern
    from app.services.detection.registry import TEMPLATES

    w = World(db)
    acct = w.account(w.customer("RUN-1"))
    w.tx(acct, MONDAY, 150 * M)
    w.done()
    first = run_pattern(db, TEMPLATES[P01], actor=None)
    second = run_pattern(db, TEMPLATES[P01], actor=None)
    assert (first.alerts_created, second.alerts_created, second.alerts_already_existing) == (1, 0, 1)
    [alert] = db.scalars(select(Alert).where(Alert.pattern_code == P01)).all()
    assert alert.detection_details["TEMPLATE_VERSION"] == "p01_large_txn_v1"
    assert alert.detection_details["ABS_THRESHOLD_APPLIED"] == "100000000.00"
    assert db.scalars(select(AlertTransaction).where(AlertTransaction.alert_id == alert.id)).all()


# ============================================================ through the API

def test_the_run_endpoint_runs_every_rule_pattern_and_reports_each(client, auth_headers, db):
    from app.models.enums import UserRole

    w = World(db)
    w.tx(w.account(w.customer("API-RUN")), MONDAY, 150 * M)
    db.commit()
    r = client.post("/detection/run", headers=auth_headers(UserRole.ROLE_DATA_OPS))
    assert r.status_code == 200, r.text
    rules = {s["pattern_code"]: s for s in r.json()["rules"]}
    assert set(rules) == {"P02_STRUCTURING", *PATTERNS}
    assert rules[P01]["alerts_created"] == 1 and rules[P03]["alerts_created"] == 0


def test_one_pattern_can_be_run_alone_and_an_unknown_one_is_404(client, auth_headers, db):
    from app.models.enums import UserRole

    w = World(db)
    w.tx(w.account(w.customer("API-ONE")), MONDAY, 150 * M)
    db.commit()
    headers = auth_headers(UserRole.ROLE_ANALYST)
    ok = client.post(f"/detection/patterns/{P01}/run", headers=headers)
    assert ok.status_code == 200 and ok.json()["alerts_created"] == 1
    assert client.post("/detection/patterns/P99_NOPE/run", headers=headers).status_code == 404
    assert client.post(f"/detection/patterns/{P01}/run", headers=auth_headers(UserRole.ROLE_TRIAGE)).status_code == 403


def test_a_draft_version_of_a_new_pattern_can_be_simulated_without_writing_alerts(client, auth_headers, db):
    from sqlalchemy import func, select

    from app.models.alert import Alert
    from app.models.enums import RuleState, UserRole
    from app.models.rule import Rule, RuleVersion

    w = World(db)
    w.tx(w.account(w.customer("API-SIM")), MONDAY, 150 * M)
    rule = db.scalar(select(Rule).where(Rule.pattern_code == P01))
    v1 = db.scalar(select(RuleVersion).where(RuleVersion.rule_id == rule.id, RuleVersion.version == 1))
    db.add(RuleVersion(
        rule_id=rule.id, version=2, parent_version=1, template_version=v1.template_version,
        parameters={**v1.parameters, "abs_threshold_individual": "200000000.00"}, window_type=v1.window_type,
        window_length=v1.window_length, entity_scope=v1.entity_scope, severity=v1.severity,
        reason_code="RC-LARGE-02-DRAFT", description="Stricter draft", state=RuleState.DRAFT, change_summary="test",
    ))
    db.commit()
    r = client.post(f"/rules/{rule.rule_ref}/versions/2/simulate", json={}, headers=auth_headers(UserRole.ROLE_ANALYST))
    assert r.status_code == 201, r.text
    assert r.json()["would_be_alert_count"] == 0       # 150M is under the stricter 200M threshold
    assert db.scalar(select(func.count()).select_from(Alert)) == 0
