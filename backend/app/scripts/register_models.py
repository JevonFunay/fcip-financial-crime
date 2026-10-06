"""Register trained model versions in the app (FR-405; TRD §10.5).

    python -m app.scripts.register_models                 # ml_models/aml_gbm_v1 and ml_models/fraud_gbm_v1
    python -m app.scripts.register_models --models-dir ml_models --version aml_gbm_v1

Reads what stage 2 wrote (model.txt and model_card.json), checks the model
against its definition hash and against this checkout's feature set and MCC
reference, and stores the model itself, its threshold and its card as an
immutable model_version row. Registering the same version again does nothing.

TEMPORARY BYPASS OF MAKER-CHECKER, as for RUL-0001 (migration 0006). A model
version should only reach ACTIVE through FR-405 (an analyst submits, the MLRO
approves). Neither FR-405's flow nor ROLE_MLRO exists yet, so a registered
version is written ACTIVE, and the bypass is stated in an audit event.
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.enums import RuleState
from app.models.ml import UNIT_AML, UNIT_FRAUD, ModelVersion
from app.services.audit import OBJECT_MODEL_VERSION, record_transition
from app.services.ml_scoring import verify

UNITS = {"aml_gbm_v1": UNIT_AML, "fraud_gbm_v1": UNIT_FRAUD}
BYPASS_NOTE = (
    "Registered ACTIVE by app.scripts.register_models. TEMPORARY BYPASS: no maker-checker, because FR-405's "
    "approval flow and ROLE_MLRO are not built yet (same approach as RUL-0001)."
)


class RegistrationRefused(Exception):
    pass


def register(db: Session, folder: Path) -> ModelVersion | None:
    """The new ModelVersion, or None when that version is already registered."""
    card = json.loads((folder / "model_card.json").read_text(encoding="utf-8"))
    version_name = card["version"]
    if db.scalar(select(ModelVersion).where(ModelVersion.version == version_name)) is not None:
        return None
    unit = UNITS.get(version_name)
    if unit is None:
        raise RegistrationRefused(f"{version_name}: unknown model; expected one of {', '.join(UNITS)}")
    live = db.scalar(select(ModelVersion).where(ModelVersion.unit == unit, ModelVersion.state == RuleState.ACTIVE))
    if live is not None:
        raise RegistrationRefused(f"{live.version} is already ACTIVE for {unit}; retire it first (FR-405)")

    version = ModelVersion(
        artefact_type=card["artefact_type"],
        version=version_name,
        definition_hash=card["definition_hash"],
        state=RuleState.ACTIVE,
        unit=unit,
        feature_set_version=card["feature_set"]["version"],
        threshold=card["model"]["threshold"],
        model_text=(folder / "model.txt").read_text(encoding="utf-8"),
        model_card=card,
        correlation_id=uuid.uuid4(),
    )
    try:
        verify(version)
    except Exception as exc:
        raise RegistrationRefused(str(exc)) from exc
    db.add(version)
    db.flush()
    record_transition(
        db, correlation_id=version.correlation_id, actor=None, object_type=OBJECT_MODEL_VERSION,
        object_id=version.id, from_state=None, to_state=RuleState.ACTIVE.value, reason=BYPASS_NOTE,
        action="MODEL_REGISTERED_ACTIVE",
        details={"version": version_name, "unit": unit, "definition_hash": version.definition_hash,
                 "threshold": version.threshold, "maker_checker": "BYPASSED", "awaiting": ["FR-405", "ROLE_MLRO"],
                 "training_data": card["training_data"]},
    )
    db.commit()
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description="Register trained model versions in the app (FR-405).")
    parser.add_argument("--models-dir", type=Path, default=Path("ml_models"))
    parser.add_argument("--version", action="append", choices=sorted(UNITS))
    args = parser.parse_args()
    with SessionLocal() as db:
        for name in args.version or sorted(UNITS):
            folder = args.models_dir / name
            if not (folder / "model_card.json").exists():
                raise SystemExit(f"no trained model at {folder}: run python -m app.ml.train first")
            try:
                version = register(db, folder)
            except RegistrationRefused as exc:
                raise SystemExit(f"REFUSED: {exc}") from exc
            print(f"{name}: " + ("already registered" if version is None else
                                 f"registered ACTIVE (maker-checker bypassed), threshold {version.threshold:.4f}"))


if __name__ == "__main__":
    main()
