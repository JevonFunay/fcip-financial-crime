"""Test-only data contracts, independent of persisted demo/training datasets.

The small CSV is a 29-row ingestion/rule fixture, never training data. Legacy
ML regression tests generate their own synthetic data in pytest temporary
directories using the unchanged generator; nothing is written to sample_data.
"""

from datetime import date
from pathlib import Path

TRANSACTIONS_CONTRACT = Path(__file__).resolve().parent / "fixtures" / "transactions_contract.csv"
LEGACY_GENERATOR_VERSION = "1.4.0"
LEGACY_SEED = 20260923
LEGACY_REFERENCE_DATE = date(2026, 9, 30)
LEGACY_PROFILES = ("tiny", "small")


def generate_legacy_dataset(output: Path, *, profile: str = "small") -> Path:
    """Generate a deterministic, test-only source drop on demand.

    Importing this helper does not generate data or load the generator. Each
    caller supplies a fresh temporary destination; persisted data is not read.
    """
    from app.scripts.generate_raw_dataset import RawDatasetGenerator
    from app.scripts.raw_contract import GENERATOR_VERSION

    if profile not in LEGACY_PROFILES:
        raise ValueError(f"test fixture profile must be one of {LEGACY_PROFILES}")
    if output.exists():
        raise FileExistsError(f"test fixture destination already exists: {output}")
    assert GENERATOR_VERSION == LEGACY_GENERATOR_VERSION, "legacy regression fixtures require generator 1.4.0"
    generator = RawDatasetGenerator(profile=profile, seed=LEGACY_SEED, reference_date=LEGACY_REFERENCE_DATE)
    generator.generate()
    generator.write(output)
    return output
