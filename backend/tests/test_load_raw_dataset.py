"""The bridge's TRD §6.1 manifest checks, and the shared datasets committed to
the repository.

The shared datasets under sample_data/<Tiny|Small|Full>/ are data anyone with
repository access can edit by hand, so the checks that guard a source drop —
declared synthetic, every file matching its checksum — are what keep them
trustworthy.
"""

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from app.scripts.generate_raw_dataset import RawDatasetGenerator
from app.scripts.load_raw_dataset import (
    SHARED_DATASETS,
    SHARED_ROOT,
    DatasetRejected,
    check_manifest,
    project_transactions,
)
from app.scripts.raw_contract import FILE_SPECS, GENERATOR_VERSION


@pytest.fixture()
def dataset(tmp_path: Path) -> Path:
    generator = RawDatasetGenerator(profile="tiny", seed=20260923, reference_date=date(2026, 9, 30))
    generator.generate()
    generator.write(tmp_path / "raw")
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


# --- the datasets committed to the repository ---------------------------------

def _shared(name: str) -> Path:
    folder = SHARED_ROOT / SHARED_DATASETS[name]
    if not (folder / "manifest.json").exists():
        pytest.skip(f"shared dataset {folder.name} is not present in this checkout")
    return folder


@pytest.mark.parametrize("name", ["tiny", "small", pytest.param("full", marks=pytest.mark.slow)])
def test_shared_dataset_passes_the_manifest_checks(name):
    folder = _shared(name)

    manifest, _ = check_manifest(folder)

    assert manifest["profile"] == name


@pytest.mark.parametrize("name", ["tiny", "small", pytest.param("full", marks=pytest.mark.slow)])
def test_shared_dataset_matches_the_source_contract(name):
    folder = _shared(name)
    for spec in FILE_SPECS:
        with (folder / spec.name).open(encoding="utf-8") as handle:
            assert next(csv.reader(handle)) == spec.header, f"{folder.name}/{spec.name}"
