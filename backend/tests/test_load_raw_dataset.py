"""The bridge's TRD §6.1 manifest checks on generated test-only source drops.

No persisted demo/training data is required. Temporary fixtures preserve the
legacy source contract, checksums and deterministic regeneration coverage.
"""

import csv
import json
import shutil
from pathlib import Path

import pytest

from app.scripts.load_raw_dataset import (
    DatasetRejected,
    check_manifest,
    project_transactions,
)
from app.scripts.raw_contract import FILE_SPECS, GENERATOR_VERSION
from data_helpers import (
    LEGACY_GENERATOR_VERSION,
    LEGACY_PROFILES,
    LEGACY_REFERENCE_DATE,
    LEGACY_SEED,
    generate_legacy_dataset,
)


@pytest.fixture()
def dataset(tmp_path: Path, legacy_tiny_raw: Path) -> Path:
    # Every manifest rejection test mutates a private copy.
    shutil.copytree(legacy_tiny_raw, tmp_path / "raw")
    return tmp_path / "raw"


def _manifest(raw_dir: Path) -> dict:
    return json.loads((raw_dir / "manifest.json").read_text())


def _write_manifest(raw_dir: Path, manifest: dict) -> None:
    (raw_dir / "manifest.json").write_text(json.dumps(manifest))


def test_a_freshly_generated_dataset_passes_with_no_warnings(dataset):
    manifest, warnings = check_manifest(dataset)

    assert manifest["synthetic_declaration"] is True
    assert warnings == []


def test_a_dataset_not_declared_synthetic_is_refused(dataset):
    """FR-501: nothing loads without synthetic_declaration = true."""
    manifest = _manifest(dataset)
    manifest["synthetic_declaration"] = False
    _write_manifest(dataset, manifest)

    with pytest.raises(DatasetRejected, match="synthetic_declaration"):
        check_manifest(dataset)


def test_a_file_edited_after_generation_is_refused(dataset):
    """TRD §6.1: checksums are verified before parsing."""
    with (dataset / "customers.csv").open("a", encoding="utf-8") as handle:
        handle.write('"CUST-HAND","Hand Edited","","1990-01-01","","M","ID","9900000000000000",'
                     '"","","","","","","ID","","","2020-01-01","COMPLETE","ACTIVE"\n')

    with pytest.raises(DatasetRejected, match="customers.csv"):
        check_manifest(dataset)


def test_a_missing_file_is_refused(dataset):
    (dataset / "devices.csv").unlink()

    with pytest.raises(DatasetRejected, match="devices.csv"):
        check_manifest(dataset)


def test_a_dataset_without_a_manifest_is_refused(dataset):
    (dataset / "manifest.json").unlink()

    with pytest.raises(DatasetRejected, match="no manifest"):
        check_manifest(dataset)


def test_a_dataset_from_another_generator_version_loads_with_a_warning(dataset):
    """TRD §11.7: the version tells two datasets from the same seed apart. An
    older one is still valid source data, so it is loaded — but not silently."""
    manifest = _manifest(dataset)
    manifest["generator_version"] = "0.9.0"
    _write_manifest(dataset, manifest)

    _, warnings = check_manifest(dataset)

    assert len(warnings) == 1
    assert "0.9.0" in warnings[0] and GENERATOR_VERSION in warnings[0]


def test_malformed_currency_codes_reach_quarantine_rather_than_being_dropped(dataset, tmp_path):
    """Only a well-formed ISO 4217 code other than IDR counts as foreign. The
    INVALID_CURRENCY defect values (1DR, IDRR, "id r", ID) must pass through."""
    stats = project_transactions(dataset, tmp_path / "projected.csv")
    with (tmp_path / "projected.csv").open(encoding="utf-8") as handle:
        currencies = {row["currency"] for row in csv.DictReader(handle)}

    assert stats["non_idr"] == 0
    assert stats["written"] == stats["total"]
    assert currencies - {"IDR"}, "the invalid-currency defect rows should still be present"


# --- generated fixture contracts ---------------------------------------------


@pytest.fixture(params=LEGACY_PROFILES)
def generated_profile(request) -> tuple[str, Path]:
    name = request.param
    return name, request.getfixturevalue(f"legacy_{name}_raw")


def test_generated_fixture_passes_the_manifest_checks_without_warnings(generated_profile):
    name, folder = generated_profile
    manifest, warnings = check_manifest(folder)

    assert manifest["profile"] == name
    assert manifest["generator_version"] == LEGACY_GENERATOR_VERSION
    assert manifest["seed"] == LEGACY_SEED
    assert manifest["business_date"] == LEGACY_REFERENCE_DATE.isoformat()
    assert manifest["synthetic_declaration"] is True
    assert warnings == []


def test_generated_fixture_is_reproducible_byte_for_byte(generated_profile, tmp_path):
    """The fixed profile, seed and date reproduce source and ground-truth bytes.

    Manifest/report wall-clock metadata is deliberately excluded, as before.
    """
    name, folder = generated_profile
    fresh = generate_legacy_dataset(tmp_path / "fresh", profile=name)

    changed = [
        name_ for name_ in (*(spec.name for spec in FILE_SPECS), "labels.csv", "label_transactions.csv",
                            "seeds.json", "data_dictionary.md", "scenario_catalogue.md")
        if (fresh / name_).read_bytes() != (folder / name_).read_bytes()
    ]
    assert changed == [], f"non-deterministic {name} fixture files: {', '.join(changed)}"


def test_the_full_profile_is_not_a_persisted_regression_fixture(tmp_path):
    with pytest.raises(ValueError, match="test fixture profile"):
        generate_legacy_dataset(tmp_path / "full", profile="full")
    assert not (tmp_path / "full").exists()


def test_generated_fixture_matches_the_source_contract(generated_profile):
    name, folder = generated_profile
    for spec in FILE_SPECS:
        with (folder / spec.name).open(encoding="utf-8") as handle:
            assert next(csv.reader(handle)) == spec.header, f"{name}/{spec.name}"
