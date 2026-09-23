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
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.scripts.generate_raw_dataset import DEFECTS, RawDatasetGenerator
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


def test_injected_p02_positives_satisfy_the_implemented_detector(dataset):
    """The label says these are structuring; the detector has to agree, or the
    recall figure it produces means nothing."""
    out_dir, _ = dataset
    params = P02Parameters()
    grouped = _in_band_by_entity(out_dir, params)
    positives = [r for r in _rows(out_dir, LABELS.name)
                 if r["pattern_code"] == "P02" and r["label_type"] == "INJECTED_POSITIVE"]

    assert positives
    for label in positives:
        clusters = find_clusters(grouped.get(label["entity_source_id"], []), params)
        assert clusters, f"{label['scenario_id']} is labelled P02 but raises no cluster"


def test_p02_edge_cases_do_not_satisfy_the_detector(dataset):
    """The P02 edge constructions are legitimate activity deliberately spread
    past the 7-day window, and the boundary cases sit exactly on the threshold
    (which is exclusive). Neither may fire at default parameters."""
    out_dir, _ = dataset
    params = P02Parameters()
    grouped = _in_band_by_entity(out_dir, params)
    edges = [r for r in _rows(out_dir, LABELS.name)
             if r["pattern_code"] == "P02" and r["label_type"] == "EDGE_CASE"]

    assert edges
    for label in edges:
        clusters = find_clusters(grouped.get(label["entity_source_id"], []), params)
        assert not clusters, f"{label['scenario_id']} is a legitimate look-alike but fired: {label['note']}"


def test_control_cohort_raises_nothing_at_default_parameters(dataset):
    """FRD §8.14: an alert on the control cohort is a rule defect, not a finding."""
    out_dir, _ = dataset
    params = P02Parameters()
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
