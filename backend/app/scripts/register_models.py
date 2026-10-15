"""Register trained model versions in the app (FR-405; TRD §10.5).

    python -m app.scripts.register_models --models-dir ml_models --version aml_if_v1 --version fraud_if_v1 --state DRAFT

Reads a model.txt and model_card.json, checks the model
against its definition hash and against this checkout's feature set and MCC
reference, and stores the model itself, its threshold and its card as an
immutable model_version row. Registering the same version again does nothing.

TEMPORARY BYPASS OF MAKER-CHECKER, as for RUL-0001 (migration 0006). A model
version should only reach ACTIVE through FR-405 (an analyst submits, the MLRO
approves). Neither FR-405's flow nor ROLE_MLRO exists yet. ACTIVE registration
keeps the existing explicit bypass; DRAFT registration stores a model for
review without activation and without claiming an approval bypass.
"""

from __future__ import annotations

import argparse
import json
import re
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


def register(db: Session, folder: Path, *, state: RuleState = RuleState.ACTIVE) -> ModelVersion | None:
    """The new ModelVersion, or None when that version is already registered."""
    try:
        state = RuleState(state)
    except ValueError as exc:
        raise RegistrationRefused("Registration state must be DRAFT or ACTIVE") from exc
    if state not in {RuleState.DRAFT, RuleState.ACTIVE}:
        raise RegistrationRefused("Registration state must be DRAFT or ACTIVE")
    card = json.loads((folder / "model_card.json").read_text(encoding="utf-8"))
    version_name = card["version"]
    if not isinstance(version_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", version_name):
        raise RegistrationRefused("Model version must be a plain version identifier")
    unit = ((card.get("unit") or card.get("model", {}).get("unit"))
            if card.get("artefact_type") == "ISOLATION_FOREST_JSON" else UNITS.get(version_name))
    if unit not in {UNIT_AML, UNIT_FRAUD}:
        unit = None
    if unit is None:
        raise RegistrationRefused(f"{version_name}: model card must declare unit AML or FRAUD")

    version = ModelVersion(
        artefact_type=card["artefact_type"],
        version=version_name,
        definition_hash=card["definition_hash"],
        state=state,
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
    existing = db.scalar(select(ModelVersion).where(ModelVersion.version == version_name))
    if existing is not None:
        if any(getattr(existing, field) != getattr(version, field) for field in (
            "definition_hash", "artefact_type", "unit", "feature_set_version", "threshold", "model_text", "model_card",
        )):
            raise RegistrationRefused(f"{version_name}: already registered with different immutable content")
        if existing.state != state:
            raise RegistrationRefused(f"{version_name}: already {existing.state.value}; registration cannot change its lifecycle")
        return None
    if state is RuleState.ACTIVE:
        live = db.scalar(select(ModelVersion).where(ModelVersion.unit == unit, ModelVersion.state == RuleState.ACTIVE))
        if live is not None:
            raise RegistrationRefused(f"{live.version} is already ACTIVE for {unit}; retire it first (FR-405)")
    db.add(version)
    db.flush()
    record_transition(
        db, correlation_id=version.correlation_id, actor=None, object_type=OBJECT_MODEL_VERSION,
        object_id=version.id, from_state=None, to_state=state.value,
        reason=(BYPASS_NOTE if state is RuleState.ACTIVE else
                "Registered DRAFT for model quality review; activation and approval have not been requested."),
        action=f"MODEL_REGISTERED_{state.value}",
        details={"version": version_name, "unit": unit, "definition_hash": version.definition_hash,
                 "threshold": version.threshold,
                 **({"maker_checker": "BYPASSED", "awaiting": ["FR-405", "ROLE_MLRO"]}
                    if state is RuleState.ACTIVE else {"activation_requested": False}),
                 "training_data": card["training_data"]},
    )
    db.commit()
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description="Register trained model versions in the app (FR-405).")
    parser.add_argument("--models-dir", type=Path, default=Path("ml_models"))
    parser.add_argument("--version", action="append", help="Model directory/version; validated against its immutable card")
    parser.add_argument("--state", choices=["DRAFT", "ACTIVE"], default="ACTIVE",
                        help="DRAFT stores a review candidate; ACTIVE retains the documented maker-checker bypass")
    args = parser.parse_args()
    with SessionLocal() as db:
        names = args.version or sorted(path.parent.name for path in args.models_dir.glob("*/model_card.json"))
        if not names:
            raise SystemExit(f"No trained model cards under {args.models_dir}")
        for name in names:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
                raise SystemExit("--version must be a plain version identifier")
            folder = args.models_dir / name
            if not (folder / "model_card.json").exists():
                raise SystemExit(f"no trained model card at {folder}")
            try:
                version = register(db, folder, state=RuleState(args.state))
            except RegistrationRefused as exc:
                raise SystemExit(f"REFUSED: {exc}") from exc
            if version is None:
                print(f"{name}: already registered {args.state}")
            else:
                bypass = " (maker-checker bypassed)" if version.state is RuleState.ACTIVE else ""
                print(f"{name}: registered {version.state.value}{bypass}, threshold {version.threshold:.4f}")


if __name__ == "__main__":
    main()
