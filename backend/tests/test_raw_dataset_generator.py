"""Tests for the synthetic raw-source generator (TRD §11).

The generator is the foundation every detection metric rests on, so the
properties tested here are the ones that make those metrics meaningful:
reproducibility (T-GEN-01), contract conformance (TRD §6.1), the deliberate
defects (§11.4), and — the important one — that the labelled scenarios actually
behave the way their labels claim.
"""

import csv
import hashlib
import json
import random
import time
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.scripts.generate_raw_dataset import (
    COHORT_SHARES,
    DEFECTS,
    EDGE_PER_PATTERN,
    BOUNDARY_PER_PATTERN,
    FULL_TARGETS,
    JAKARTA,
    MIN_TARGET_TRANSACTIONS,
    SCENARIO_TARGETS,
    RawDatasetGenerator,
)
from app.scripts.load_raw_dataset import check_manifest
from app.scripts.raw_contract import CONTRACT_VERSION, FILE_SPECS, LABELS
from app.services.detection.p02_structuring import P02Parameters, find_clusters

REFERENCE_DATE = date(2026, 9, 30)
SEED = 20260923


def _build(tmp_path: Path, *, seed: int = SEED, profile: str = "tiny", name: str = "out") -> tuple[Path, dict]:
    generator = RawDatasetGenerator(profile=profile, seed=seed, reference_date=REFERENCE_DATE)
    generator.generate()
    out_dir = tmp_path / name
    report = generator.write(out_dir)
    return out_dir, report


def _rows(out_dir: Path, name: str) -> list[dict[str, str]]:
    with (out_dir / name).open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def dataset(tmp_path_factory) -> tuple[Path, dict]:
    return _build(tmp_path_factory.mktemp("raw"))


# --- T-GEN-01: reproducibility ------------------------------------------------

def test_same_seed_reproduces_every_file_byte_for_byte(tmp_path):
    """T-GEN-01 (TRD §11.7). Without this, no detection metric is reproducible."""
    first, _ = _build(tmp_path, name="a")
    second, _ = _build(tmp_path, name="b")

    for spec in (*FILE_SPECS, LABELS):
        assert (first / spec.name).read_bytes() == (second / spec.name).read_bytes(), spec.name


def test_a_different_seed_produces_different_data(tmp_path):
    first, _ = _build(tmp_path, name="a")
    other, _ = _build(tmp_path, seed=SEED + 1, name="c")

    assert (first / "transactions.csv").read_bytes() != (other / "transactions.csv").read_bytes()


# --- TRD §6.1 file contract ---------------------------------------------------

def test_every_contract_file_is_written_with_its_declared_header(dataset):
    out_dir, _ = dataset
    for spec in FILE_SPECS:
        with (out_dir / spec.name).open(encoding="utf-8") as handle:
            assert next(csv.reader(handle)) == spec.header, spec.name


def test_manifest_declares_synthetic_and_matches_the_files_on_disk(dataset):
    out_dir, _ = dataset
    manifest = json.loads((out_dir / "manifest.json").read_text())

    # FR-501 / TRD §6.1: ingestion must refuse a batch without this.
    assert manifest["synthetic_declaration"] is True
    assert manifest["contract_version"] == CONTRACT_VERSION
    assert {f["name"] for f in manifest["files"]} == {s.name for s in FILE_SPECS}
    for entry in manifest["files"]:
        digest = hashlib.sha256((out_dir / entry["name"]).read_bytes()).hexdigest()
        assert entry["sha256"] == digest, entry["name"]
        assert entry["record_count"] == len(_rows(out_dir, entry["name"]))


def test_labels_are_not_part_of_the_source_contract(dataset):
    """Ground truth ships beside the data; it is not something a source system
    would ever send, so it stays out of the manifest."""
    out_dir, _ = dataset
    manifest = json.loads((out_dir / "manifest.json").read_text())

    assert LABELS.name not in {f["name"] for f in manifest["files"]}
    assert (out_dir / LABELS.name).exists()


def test_mandatory_fields_are_always_populated(dataset):
    out_dir, _ = dataset
    for spec in FILE_SPECS:
        required = [f.name for f in spec.fields if f.required]
        for row in _rows(out_dir, spec.name):
            for name in required:
                # source_account_id and amount carry deliberate defects, which
                # replace the value rather than blanking it.
                assert row[name] != "", f"{spec.name}.{name} was blank"


# --- validation rules the contract states -------------------------------------

def test_customers_satisfy_the_contract_validations(dataset):
    out_dir, _ = dataset
    cutoff = REFERENCE_DATE - timedelta(days=17 * 365)
    for row in _rows(out_dir, "customers.csv"):
        assert date.fromisoformat(row["date_of_birth"]) <= cutoff
        assert row["kyc_status"] in ("COMPLETE", "PARTIAL", "PENDING")
        assert len(row["nationality"]) == 2 and len(row["country"]) == 2
        assert row["phone"].startswith("+62")


def test_national_id_is_structurally_valid_and_provably_synthetic(dataset):
    """NIK-shaped, encoding gender the real way (+40 on a female's birth day),
    but on province prefix 99 — which is never issued, so a generated value can
    never collide with a real one."""
    out_dir, _ = dataset
    checked = 0
    for row in _rows(out_dir, "customers.csv"):
        nik = row["national_id_reference"]
        assert len(nik) == 16 and nik.isdigit()
        assert nik.startswith("99")
        if row["source_customer_id"].startswith("CUST-ER"):
            continue  # ER twins deliberately carry conflicting identifiers
        dob = date.fromisoformat(row["date_of_birth"])
        expected_day = dob.day + 40 if row["gender"] == "F" else dob.day
        assert nik[6:12] == f"{expected_day:02d}{dob.month:02d}{dob.strftime('%y')}"
        checked += 1
    assert checked > 0


def test_accounts_satisfy_the_contract_validations(dataset):
    out_dir, _ = dataset
    for row in _rows(out_dir, "accounts.csv"):
        assert row["status"] in ("ACTIVE", "DORMANT", "CLOSED", "RESTRICTED")
        assert row["owner_type"] in ("INDIVIDUAL", "BUSINESS")
        if row["closed_date"]:
            assert date.fromisoformat(row["closed_date"]) >= date.fromisoformat(row["opened_date"])


def test_beneficial_ownership_percentages_are_in_range(dataset):
    out_dir, _ = dataset
    for row in _rows(out_dir, "beneficial_owners.csv"):
        share = Decimal(row["ownership_percentage"])
        assert Decimal(0) < share <= Decimal(100)


def test_every_account_owner_and_bo_business_resolves(dataset):
    """TRD §6.2 loads party before account; a forward reference that never
    resolves would be an UNRESOLVED_* defect the generator did not intend."""
    out_dir, _ = dataset
    owners = {r["source_customer_id"] for r in _rows(out_dir, "customers.csv")}
    owners |= {r["source_business_id"] for r in _rows(out_dir, "business_customers.csv")}
    businesses = {r["source_business_id"] for r in _rows(out_dir, "business_customers.csv")}

    assert all(r["owner_source_id"] in owners for r in _rows(out_dir, "accounts.csv"))
    assert all(r["source_business_id"] in businesses for r in _rows(out_dir, "beneficial_owners.csv"))


def test_watchlist_records_follow_the_declared_type_mix(dataset):
    out_dir, _ = dataset
    rows = _rows(out_dir, "watchlist.csv")
    mix = Counter(r["list_type"] for r in rows)

    assert set(mix) <= {"PEP_SYNTHETIC", "SANCTIONS_SYNTHETIC", "INTERNAL_WATCH"}
    # TRD §11.1 targets 60/30/10; a small sample is noisy, so this only asserts
    # the ordering the mix implies.
    assert mix["PEP_SYNTHETIC"] >= mix["SANCTIONS_SYNTHETIC"] >= mix["INTERNAL_WATCH"]


def test_transactions_use_explicit_jakarta_offsets_and_valid_directions(dataset):
    out_dir, _ = dataset
    malformed = 0
    for row in _rows(out_dir, "transactions.csv"):
        assert row["direction"] in ("IN", "OUT")
        try:
            parsed = datetime.fromisoformat(row["value_datetime"])
        except ValueError:
            malformed += 1  # the deliberate INVALID_DATE_FORMAT defect
            continue
        assert parsed.utcoffset() is not None, "value_datetime must carry an explicit offset"
    assert malformed > 0, "the malformed-date defect should be present"


# --- TRD §11.4 deliberate defects ---------------------------------------------

def test_every_declared_defect_type_is_actually_injected(dataset):
    _, report = dataset
    missing = [name for name in DEFECTS if report["defect_counts"].get(name, 0) == 0]

    assert missing == [], f"declared but never produced: {missing}"


def test_quarantinable_defects_appear_at_roughly_their_declared_rate(dataset):
    """Rates are what makes the quality pipeline's job realistic; an order of
    magnitude out in either direction would make the demo misleading."""
    out_dir, report = dataset
    total = len(_rows(out_dir, "transactions.csv"))

    for name in ("malformed_date", "invalid_currency", "invalid_amount", "unresolved_account"):
        rate = report["defect_counts"][name] / total
        assert DEFECTS[name] / 4 <= rate <= DEFECTS[name] * 4, f"{name} at {rate:.4f}"


class _AlwaysDefective(random.Random):
    """A defect stream that applies every defect to every row."""

    def random(self) -> float:
        return 0.0


def test_a_row_that_is_both_late_and_malformed_stays_malformed():
    """Late arrival rewrites the timestamp. Applied after the malformed-date
    defect it used to overwrite the malformed value with a valid one, so the
    defect vanished while still being counted."""
    generator = RawDatasetGenerator(profile="tiny", seed=SEED, reference_date=REFERENCE_DATE)
    generator.rng["defect"] = _AlwaysDefective(1)

    row = generator._emit(
        account_id="ACC-0000001", when=datetime(2026, 9, 1, 10, 0, tzinfo=JAKARTA), amount=1000.0,
        direction="OUT", channel="QRIS", transaction_type="MERCHANT_PAYMENT",
    )

    assert {"late_arrival", "malformed_date"} <= set(row["_defects"])
    with pytest.raises(ValueError):
        datetime.fromisoformat(row["value_datetime"])
    assert row["business_date"] < "2026-09-01", "late arrival must still backdate the row"


def test_defect_counts_are_bounded_by_the_symptoms_in_the_written_rows(dataset):
    """Counts come from the rows written, not from a running tally. Each
    quarantine-driving defect must show up on at least as many rows as it is
    counted for, and on no more than that plus the copied rows (exact
    duplicates and idempotency conflicts copy whatever their source carried)."""
    out_dir, report = dataset
    counts = report["defect_counts"]
    accounts = {r["source_account_id"] for r in _rows(out_dir, "accounts.csv")}
    rows = _rows(out_dir, "transactions.csv")

    def unparseable(value: str) -> bool:
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return True
        return False

    symptoms = {
        "malformed_date": sum(unparseable(r["value_datetime"]) for r in rows),
        "invalid_currency": sum(r["currency_original"] != "IDR" for r in rows),
        "invalid_amount": sum(Decimal(r["amount_original"]) <= 0 for r in rows),
        "unresolved_account": sum(r["source_account_id"] not in accounts for r in rows),
    }
    copies = counts["exact_duplicate"] + counts["idempotency_conflict"]
    for name, seen in symptoms.items():
        assert counts[name] <= seen <= counts[name] + copies, f"{name}: counted {counts[name]}, on {seen} rows"


# --- TRD §11.2 population ------------------------------------------------------

def test_cohorts_hit_the_declared_shares_exactly(dataset):
    """Assigned as exact counts, so the shares hold even at the CI profile,
    where per-party draws used to drift by several points."""
    _, report = dataset
    population = report["population"]
    total = population["parties_excluding_er_test"]
    cohorts = population["cohorts"]

    for cohort, share in COHORT_SHARES.items():
        assert cohorts[cohort]["count"] == round(total * share), cohort
    assert sum(entry["count"] for entry in cohorts.values()) == total
    assert population["real_businesses"] + population["individual_sole_traders_in_business_cohort"] == (
        cohorts["BUSINESS_NORMAL"]["count"]
    )


def test_every_entity_carries_at_most_one_label(dataset):
    """One scenario per entity. A party labelled both positive and edge makes
    the edge label a lie, P05's dormancy gap deletes another scenario's
    history, and a scenario on a control entity breaks the control cohort."""
    out_dir, _ = dataset
    per_entity = Counter(r["entity_source_id"] for r in _rows(out_dir, LABELS.name))

    assert [entity for entity, n in per_entity.items() if n > 1] == []


def test_edge_cases_are_placed_on_the_ambiguous_cohort_before_spilling(dataset):
    _, report = dataset
    population = report["population"]
    placed = population["scenario_placement"]["edge_and_boundary"]
    from_own_cohort = placed.get("EDGE_AMBIGUOUS", 0)

    assert from_own_cohort == min(sum(placed.values()), population["cohorts"]["EDGE_AMBIGUOUS"]["count"])


# --- TRD §11.3 labels and scenario behaviour ----------------------------------

def test_every_pattern_has_labelled_positives_and_edge_cases(dataset):
    out_dir, _ = dataset
    labels = _rows(out_dir, LABELS.name)
    by_pattern = Counter((r["pattern_code"], r["label_type"]) for r in labels)

    for pattern in (f"P{n:02d}" for n in range(1, 13)):
        assert by_pattern[(pattern, "INJECTED_POSITIVE")] > 0, f"{pattern} has no positives"
        assert by_pattern[(pattern, "EDGE_CASE")] > 0, f"{pattern} has no edge cases"


def test_control_cohort_is_labelled(dataset):
    out_dir, _ = dataset
    control = [r for r in _rows(out_dir, LABELS.name) if r["label_type"] == "CONTROL_CLEAN"]

    assert control, "the control cohort must be labelled so it can be tested (FRD §8.14)"
    assert all(r["expected_reason_code"] == "" for r in control)


def _in_band_by_entity(out_dir: Path, params: P02Parameters) -> dict[str, list]:
    """Group in-band transactions per owning party, the way the detector does."""
    owner_of = {r["source_account_id"]: r["owner_source_id"] for r in _rows(out_dir, "accounts.csv")}
    grouped: dict[str, list] = {}
    for row in _rows(out_dir, "transactions.csv"):
        owner = owner_of.get(row["source_account_id"])
        if owner is None or row["currency_original"] != params.currency:
            continue
        try:
            amount = Decimal(row["amount_original"])
            when = datetime.fromisoformat(row["value_datetime"])
        except (ValueError, ArithmeticError):
            continue
        if params.band_lower <= amount < params.reporting_threshold:
            grouped.setdefault(owner, []).append(SimpleNamespace(transaction_date=when, amount=amount))
    for rows in grouped.values():
        rows.sort(key=lambda t: t.transaction_date)
    return grouped


def test_injected_p02_positives_satisfy_the_implemented_detector(dataset, p02_params):
    """The label says these are structuring; the detector has to agree, or the
    recall figure it produces means nothing."""
    out_dir, _ = dataset
    params = p02_params
    grouped = _in_band_by_entity(out_dir, params)
    positives = [r for r in _rows(out_dir, LABELS.name)
                 if r["pattern_code"] == "P02" and r["label_type"] == "INJECTED_POSITIVE"]

    assert positives
    for label in positives:
        clusters = find_clusters(grouped.get(label["entity_source_id"], []), params)
        assert clusters, f"{label['scenario_id']} is labelled P02 but raises no cluster"


def test_p02_edge_cases_do_not_satisfy_the_detector(dataset, p02_params):
    """The P02 edge constructions are legitimate activity deliberately spread
    past the 7-day window, and the boundary cases sit exactly on the threshold
    (which is exclusive). Neither may fire at default parameters."""
    out_dir, _ = dataset
    params = p02_params
    grouped = _in_band_by_entity(out_dir, params)
    edges = [r for r in _rows(out_dir, LABELS.name)
             if r["pattern_code"] == "P02" and r["label_type"] == "EDGE_CASE"]

    assert edges
    for label in edges:
        clusters = find_clusters(grouped.get(label["entity_source_id"], []), params)
        assert not clusters, f"{label['scenario_id']} is a legitimate look-alike but fired: {label['note']}"


def test_control_cohort_raises_nothing_at_default_parameters(dataset, p02_params):
    """FRD §8.14: an alert on the control cohort is a rule defect, not a finding."""
    out_dir, _ = dataset
    params = p02_params
    grouped = _in_band_by_entity(out_dir, params)
    control = [r["entity_source_id"] for r in _rows(out_dir, LABELS.name) if r["label_type"] == "CONTROL_CLEAN"]

    assert control
    for entity in control:
        assert not find_clusters(grouped.get(entity, []), params), f"control entity {entity} fired"


def test_entity_resolution_population_is_present_and_labelled(dataset):
    """TRD §11.5: each construction states what resolution must do with it."""
    out_dir, _ = dataset
    er = Counter(r["expected_reason_code"] for r in _rows(out_dir, LABELS.name) if r["pattern_code"] == "ER")

    for construction in ("SAME_ID_NAME_VARIANT", "SAME_PHONE_DOB", "SAME_NAME_ONLY",
                         "IDENTIFIER_CONFLICT", "AMBIGUOUS_BAND"):
        assert er[construction] > 0, f"{construction} population missing"


# --- TRD §11.7 deliverables ---------------------------------------------------

def test_generation_report_and_dictionaries_are_written(dataset):
    out_dir, report = dataset

    for name in ("generation_report.json", "seeds.json", "data_dictionary.md", "scenario_catalogue.md"):
        assert (out_dir / name).exists(), name
    assert report["seed"] == SEED
    assert sum(report["row_counts"].values()) > 0


@pytest.mark.slow
def test_full_profile_lands_inside_the_declared_volume_targets(tmp_path):
    """TRD §11.1. Excluded from the default run (it generates ~400k rows);
    run explicitly with `pytest -m slow`."""
    out_dir, report = _build(tmp_path, profile="full", name="full")
    counts = report["row_counts"]

    bands = {
        "customers.csv": (9_500, 10_500),
        "business_customers.csv": (1_100, 1_300),
        "merchants.csv": (1_000, 2_000),
        "accounts.csv": (12_000, 15_000),
        "transactions.csv": (300_000, 500_000),
        "devices.csv": (8_000, 12_000),
        "watchlist.csv": (2_400, 2_600),
    }
    for name, (low, high) in bands.items():
        assert low <= counts[name] <= high, f"{name} at {counts[name]:,}, target {low:,}-{high:,}"
    assert (out_dir / "manifest.json").exists()


def test_data_dictionary_documents_every_generated_column(dataset):
    """It is rendered from the same specs the CSVs are written from, so this
    guards the rendering, not the authoring."""
    out_dir, _ = dataset
    dictionary = (out_dir / "data_dictionary.md").read_text()

    for spec in FILE_SPECS:
        assert f"`{spec.name}`" in dictionary
        for field in spec.fields:
            assert f"`{field.name}`" in dictionary, f"{spec.name}.{field.name} undocumented"


# --- custom scale (--target-transactions) --------------------------------------

def _build_custom(tmp_path: Path, target: int, name: str = "custom") -> tuple[Path, dict]:
    generator = RawDatasetGenerator(seed=SEED, reference_date=REFERENCE_DATE, target_transactions=target)
    generator.generate()
    out_dir = tmp_path / name
    return out_dir, generator.write(out_dir)


def _expected_count(full_count: int, factor: float, floor: int = 5) -> int:
    return max(floor, round(full_count * factor))


def test_custom_target_lands_exactly_on_the_requested_count(tmp_path):
    out_dir, report = _build_custom(tmp_path, MIN_TARGET_TRANSACTIONS)

    assert report["row_counts"]["transactions.csv"] == MIN_TARGET_TRANSACTIONS
    calibration = report["scale"]["calibration"]
    assert calibration["final"] == MIN_TARGET_TRANSACTIONS
    assert calibration["generated_before_calibration"] + calibration["added"] - calibration["removed"] == (
        MIN_TARGET_TRANSACTIONS
    )


def test_custom_scale_floors_every_pattern_and_records_where_it_did(tmp_path):
    """At the smallest custom scale pure proportion would leave most patterns
    with one or zero samples; every one must get the floor, be placed on a
    distinct entity, and be reported as an adjustment."""
    _, report = _build_custom(tmp_path, MIN_TARGET_TRANSACTIONS)
    scale = report["scale"]
    factor = scale["scale_factor"]

    for pattern, full in SCENARIO_TARGETS.items():
        for kind, full_count in (("positives", full), ("edge", EDGE_PER_PATTERN), ("boundary", BOUNDARY_PER_PATTERN)):
            entry = scale["scenarios"][pattern][kind]
            assert entry["requested"] == _expected_count(full_count, factor), f"{pattern} {kind}"
            assert entry["placed"] == entry["requested"], f"{pattern} {kind} placed {entry['placed']}"
    for adjustment in scale["floor_adjustments"]:
        assert adjustment["proportional"] < scale["min_per_pattern"] == adjustment["applied"]


def test_a_profile_and_a_target_together_are_rejected():
    with pytest.raises(ValueError, match="either a profile or target_transactions"):
        RawDatasetGenerator(profile="small", seed=SEED, reference_date=REFERENCE_DATE, target_transactions=50_000)


def test_a_target_below_the_minimum_is_rejected():
    """Below the minimum the per-pattern floor needs more distinct entities
    than the population has, and calibration can no longer land exactly."""
    with pytest.raises(ValueError, match="at least"):
        RawDatasetGenerator(seed=SEED, reference_date=REFERENCE_DATE, target_transactions=MIN_TARGET_TRANSACTIONS - 1)


def test_the_presets_carry_no_custom_scale_metadata(dataset):
    """The custom option is purely additive: preset outputs are unchanged."""
    out_dir, report = dataset

    assert "scale" not in report
    assert "target_transactions" not in json.loads((out_dir / "manifest.json").read_text())
    assert "calibrate" not in json.loads((out_dir / "seeds.json").read_text())["derived"]


def _without_generated_at(path: Path) -> dict:
    payload = json.loads(path.read_text())
    payload.pop("generated_at", None)
    return payload


@pytest.mark.slow
def test_100k_custom_dataset_is_exact_proportional_labelled_and_reproducible(tmp_path, p02_params):
    """--target-transactions 100000, the scale the office asked for.

    Excluded from the default run; run explicitly with
        pytest -m slow -v -s tests/test_raw_dataset_generator.py
    """
    target = 100_000
    started = time.perf_counter()
    out_dir, report = _build_custom(tmp_path, target, name="a")
    elapsed = time.perf_counter() - started
    scale = report["scale"]
    factor = target / FULL_TARGETS["transactions"]
    counts = report["row_counts"]
    print(f"\n100k custom dataset generated in {elapsed:.1f}s: {counts}")

    # Exactly the requested number of transactions (B1 calibration).
    assert counts["transactions.csv"] == target

    # Every entity volume derived by the full-profile ratio. Accounts follow
    # from the parties (1-3 each), so they are held to a tolerance instead.
    for name, file in (("customers", "customers.csv"), ("business_customers", "business_customers.csv"),
                       ("merchants", "merchants.csv"), ("devices", "devices.csv"), ("watchlist", "watchlist.csv")):
        assert counts[file] == round(FULL_TARGETS[name] * factor), name
    assert abs(counts["accounts.csv"] - round(FULL_TARGETS["accounts"] * factor)) <= 0.02 * FULL_TARGETS["accounts"] * factor

    # TRD §11.2 composition, exact.
    population = report["population"]
    total = population["parties_excluding_er_test"]
    for cohort, share in COHORT_SHARES.items():
        assert population["cohorts"][cohort]["count"] == round(total * share), cohort

    # TRD §11.3 per-pattern counts: proportional, floored at 5, all placed.
    floored = set()
    for pattern, full in SCENARIO_TARGETS.items():
        for kind, full_count in (("positives", full), ("edge", EDGE_PER_PATTERN), ("boundary", BOUNDARY_PER_PATTERN)):
            entry = scale["scenarios"][pattern][kind]
            assert entry["requested"] == _expected_count(full_count, factor), f"{pattern} {kind}"
            assert entry["placed"] == entry["requested"], f"{pattern} {kind}"
            if round(full_count * factor) < 5:
                floored.add((pattern, kind))
    assert {(a["pattern"], a["kind"]) for a in scale["floor_adjustments"]} == floored

    # TRD §11.4 defects at their declared rates, not fixed counts.
    for name in ("malformed_date", "invalid_currency", "invalid_amount", "unresolved_account", "late_arrival",
                 "missing_counterparty", "missing_device"):
        rate = report["defect_counts"][name] / target
        assert DEFECTS[name] / 2 <= rate <= DEFECTS[name] * 2, f"{name} at {rate:.4%}"
    assert abs(report["defect_counts"]["exact_duplicate"] / target - DEFECTS["exact_duplicate"]) < 0.002

    # Calibration touched no labelled entity: the labels still tell the truth.
    params = p02_params
    grouped = _in_band_by_entity(out_dir, params)
    labels = _rows(out_dir, LABELS.name)
    fired = {entity for entity, rows in grouped.items() if find_clusters(rows, params)}
    positives = {l["entity_source_id"] for l in labels if l["pattern_code"] == "P02" and l["label_type"] == "INJECTED_POSITIVE"}
    look_alikes = {l["entity_source_id"] for l in labels if l["pattern_code"] == "P02" and l["label_type"] == "EDGE_CASE"}
    control = {l["entity_source_id"] for l in labels if l["label_type"] == "CONTROL_CLEAN"}
    assert positives <= fired
    assert not (look_alikes & fired)
    assert not (control & fired)
    assert max(Counter(l["entity_source_id"] for l in labels).values()) == 1

    # Contract and deliverables, and the bridge accepts it without warnings.
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["synthetic_declaration"] is True
    assert (manifest["profile"], manifest["target_transactions"]) == ("custom", target)
    for name in ("labels.csv", "data_dictionary.md", "scenario_catalogue.md", "generation_report.json", "seeds.json"):
        assert (out_dir / name).exists(), name
    assert check_manifest(out_dir)[1] == []

    # T-GEN-01 at this scale: the same seed reproduces it.
    again, _ = _build_custom(tmp_path, target, name="b")
    for spec in (*FILE_SPECS, LABELS):
        assert (out_dir / spec.name).read_bytes() == (again / spec.name).read_bytes(), spec.name
    for name in ("seeds.json", "data_dictionary.md", "scenario_catalogue.md"):
        assert (out_dir / name).read_bytes() == (again / name).read_bytes(), name
    for name in ("manifest.json", "generation_report.json"):
        assert _without_generated_at(out_dir / name) == _without_generated_at(again / name), name
