"""Model lab API: train and compare, then approve a model and monitor new data with it.

Who does what (FRD roles):
- Data Ops and Analysts upload, map, train and save drafts.
- An Analyst submits a draft for approval; an MLRO other than the submitter
  approves, returns or suspends it, and switches model alerts (ML_SCORE) on or
  off (FR-405, FR-304 BR-304.1).
- Data Ops uploads new data in a dataset's layout to be monitored (FR-101).
- Every role can look.

Training and monitoring run synchronously inside the request (about 25 s for
100k rows); a background job runner is the next step before larger datasets.
"""

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.ml.lab.features import FEATURE_GROUPS
from app.ml.lab.mapping import FIELDS, MIN_PER_CLASS, LabDataError
from app.ml.lab.trainer import MODELS, LabTrainingError
from app.models.enums import UserRole
from app.models.lab import LabDataset, LabRun
from app.models.ml import ModelVersion
from app.models.user import User
from app.schemas.lab import (
    LabApproveIn,
    LabDatasetOut,
    LabFeatureGroup,
    LabFieldSpec,
    LabHistoryEntry,
    LabMappingIn,
    LabModelAlertsIn,
    LabModelAlertsState,
    LabModelSpec,
    LabModelsOut,
    LabModelVersionOut,
    LabMonitorOut,
    LabOptions,
    LabReasonIn,
    LabRunIn,
    LabRunOut,
    LabRunSummary,
    LabSavedModelOut,
    LabSavedVersion,
    LabSaveIn,
    LabSubmitIn,
)
from app.services import lab as lab_service
from app.services.ml_scoring import SIGNAL_NOTICE, SYNTHETIC_NOTICE

router = APIRouter()

ALL_ROLES = tuple(UserRole)
WRITER_ROLES = (UserRole.ROLE_DATA_OPS, UserRole.ROLE_ANALYST)
# Same cap as the transaction upload (app/routers/ingestion.py).
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _dataset_out(db: Session, dataset: LabDataset) -> LabDatasetOut:
    email = db.scalar(select(User.email).where(User.id == dataset.uploaded_by)) if dataset.uploaded_by else None
    runs = db.scalars(select(LabRun).where(LabRun.dataset_id == dataset.id).order_by(LabRun.created_at.desc())).all()
    return LabDatasetOut(
        id=dataset.id, name=dataset.name, file_name=dataset.file_name, file_sha256=dataset.file_sha256,
        file_size_bytes=dataset.file_size_bytes, file_format=dataset.file_format, row_count=dataset.row_count,
        columns=dataset.columns,
        preview=dataset.preview, suggestion=dataset.suggestion, mapping=dataset.mapping,
        validation=dataset.validation, status=dataset.status, uploaded_by_email=email,
        created_at=dataset.created_at,
        runs=[LabRunSummary(id=r.id, created_at=r.created_at, seconds=r.seconds,
                            best_model=r.results.get("best_model"), models=r.config.get("models", [])) for r in runs],
    )


def _run_out(db: Session, run: LabRun) -> LabRunOut:
    dataset = db.get(LabDataset, run.dataset_id)
    return LabRunOut(
        id=run.id, dataset_id=run.dataset_id, dataset_name=dataset.name if dataset else "", created_at=run.created_at,
        seconds=run.seconds, config=run.config, results=run.results,
        saved_versions=[LabSavedVersion(model_key=v.model_card.get("lab_model_key", ""), version=v.version,
                                        threshold=v.threshold)
                        for v in lab_service.saved_versions(db, run)],
        synthetic_notice=SYNTHETIC_NOTICE,
    )


def _get_dataset(db: Session, dataset_id: uuid.UUID) -> LabDataset:
    dataset = db.get(LabDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset lab tidak ditemukan")
    return dataset


def _get_run(db: Session, run_id: uuid.UUID) -> LabRun:
    run = db.get(LabRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run pelatihan tidak ditemukan")
    return run


@router.get("/options", response_model=LabOptions, dependencies=[Depends(require_role(*ALL_ROLES))])
def options() -> LabOptions:
    return LabOptions(
        fields=[LabFieldSpec(key=f["key"], label=f["label"], required=f["required"], help=f["help"]) for f in FIELDS],
        feature_groups=[LabFeatureGroup(key=g["key"], label=g["label"], description=g["description"],
                                        default=g["default"], features=list(g["features"])) for g in FEATURE_GROUPS],
        models=[LabModelSpec(key=k, name=m["name"], kind=m["kind"], description=m["description"], default=m["default"])
                for k, m in MODELS.items()],
        max_upload_bytes=MAX_UPLOAD_BYTES, min_per_class=MIN_PER_CLASS, synthetic_notice=SYNTHETIC_NOTICE,
    )


@router.get("/datasets", response_model=list[LabDatasetOut], dependencies=[Depends(require_role(*ALL_ROLES))])
def list_datasets(db: Session = Depends(get_db)) -> list[LabDatasetOut]:
    datasets = db.scalars(select(LabDataset).order_by(LabDataset.created_at.desc()).limit(50)).all()
    return [_dataset_out(db, d) for d in datasets]


@router.post("/datasets", response_model=LabDatasetOut, status_code=status.HTTP_201_CREATED)
def upload_dataset(
    file: UploadFile,
    name: str | None = Form(None),
    user: User = Depends(require_role(*WRITER_ROLES)),
    db: Session = Depends(get_db),
) -> LabDatasetOut:
    raw_bytes = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"File melebihi batas {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        dataset = lab_service.create_dataset(db, actor=user, file_name=file.filename or "dataset.csv",
                                             raw_bytes=raw_bytes, name=name)
    except LabDataError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _dataset_out(db, dataset)


@router.get("/datasets/{dataset_id}", response_model=LabDatasetOut, dependencies=[Depends(require_role(*ALL_ROLES))])
def get_dataset(dataset_id: uuid.UUID, db: Session = Depends(get_db)) -> LabDatasetOut:
    return _dataset_out(db, _get_dataset(db, dataset_id))


@router.post("/datasets/{dataset_id}/mapping", response_model=LabDatasetOut)
def map_dataset(
    dataset_id: uuid.UUID,
    payload: LabMappingIn,
    user: User = Depends(require_role(*WRITER_ROLES)),
    db: Session = Depends(get_db),
) -> LabDatasetOut:
    dataset = _get_dataset(db, dataset_id)
    try:
        dataset = lab_service.map_dataset(db, dataset, payload.model_dump(), actor=user)
    except LabDataError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _dataset_out(db, dataset)


@router.post("/datasets/{dataset_id}/runs", response_model=LabRunOut, status_code=status.HTTP_201_CREATED)
def train(
    dataset_id: uuid.UUID,
    payload: LabRunIn,
    user: User = Depends(require_role(*WRITER_ROLES)),
    db: Session = Depends(get_db),
) -> LabRunOut:
    dataset = _get_dataset(db, dataset_id)
    try:
        run = lab_service.train(db, dataset, payload.model_dump(), actor=user)
    except lab_service.LabConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (LabDataError, LabTrainingError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _run_out(db, run)


@router.get("/runs/{run_id}", response_model=LabRunOut, dependencies=[Depends(require_role(*ALL_ROLES))])
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> LabRunOut:
    return _run_out(db, _get_run(db, run_id))


@router.post("/runs/{run_id}/save", response_model=LabSavedModelOut)
def save_model(
    run_id: uuid.UUID,
    payload: LabSaveIn,
    user: User = Depends(require_role(*WRITER_ROLES)),
    db: Session = Depends(get_db),
) -> LabSavedModelOut:
    run = _get_run(db, run_id)
    try:
        version, existed = lab_service.save_draft(db, run, model_key=payload.model_key,
                                                  threshold_index=payload.threshold_index, actor=user)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except lab_service.LabConflict as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    card = version.model_card
    return LabSavedModelOut(
        model_version_id=version.id, version=version.version, state=version.state, threshold=version.threshold,
        model_key=payload.model_key, model_name=card["model"]["algorithm"],
        validation=card["validation"]["at_threshold"], test=card["test"]["at_threshold"],
        created_at=version.created_at, already_saved=existed,
    )


# --- model versions: status, approval (FR-405) and model alerts (CF-05) ------------------------


def _emails(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i is not None}
    return dict(db.execute(select(User.id, User.email).where(User.id.in_(ids))).all()) if ids else {}


def _version_out(db: Session, version: ModelVersion, emails: dict, names: dict) -> LabModelVersionOut:
    card = version.model_card
    training = card.get("training_data", {})
    history = lab_service.version_history(db, version)
    history_emails = _emails(db, {h.actor_user_id for h in history})
    successor = db.get(ModelVersion, version.superseded_by) if version.superseded_by else None
    dataset_id = training.get("dataset_id")
    run_id = card.get("lab_run_id")
    return LabModelVersionOut(
        id=version.id, version=version.version, state=version.state, artefact_type=version.artefact_type,
        model_key=card.get("lab_model_key"), model_name=card.get("model", {}).get("algorithm", version.artefact_type),
        training_kind=card.get("model", {}).get("training_kind", "supervised"), threshold=version.threshold,
        dataset_id=uuid.UUID(dataset_id) if dataset_id else None,
        dataset_name=names.get(dataset_id) or training.get("dataset_name"),
        lab_run_id=uuid.UUID(run_id) if run_id else None,
        validation=card.get("validation", {}).get("at_threshold", {}),
        test=card.get("test", {}).get("at_threshold", {}),
        created_by_email=emails.get(version.created_by), submitted_by_email=emails.get(version.submitted_by),
        approved_by_email=emails.get(version.approved_by), approved_at=version.approved_at,
        superseded_by_version=successor.version if successor else None, created_at=version.created_at,
        history=[LabHistoryEntry(at=h.created_at, actor_email=history_emails.get(h.actor_user_id),
                                 actor_role=h.actor_role, from_state=h.from_state, to_state=h.to_state,
                                 action=h.action, reason=h.reason) for h in history],
    )


def _models_out(db: Session) -> LabModelsOut:
    versions = lab_service.lab_versions(db)
    emails = _emails(db, {v.created_by for v in versions} | {v.submitted_by for v in versions}
                     | {v.approved_by for v in versions})
    names = {str(d.id): d.name for d in db.scalars(select(LabDataset))}
    return LabModelsOut(items=[_version_out(db, v, emails, names) for v in versions],
                        model_alerts=LabModelAlertsState(**lab_service.model_alerts_state(db)),
                        min_reason_length=lab_service.MIN_JUSTIFICATION,
                        synthetic_notice=SYNTHETIC_NOTICE, signal_notice=SIGNAL_NOTICE)


def _get_version(db: Session, version_id: uuid.UUID) -> ModelVersion:
    version = lab_service.get_lab_version(db, version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model lab tidak ditemukan")
    return version


def _transition(db: Session, action) -> LabModelsOut:
    try:
        action()
    except lab_service.LabConflict as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _models_out(db)


@router.get("/models", response_model=LabModelsOut, dependencies=[Depends(require_role(*ALL_ROLES))])
def list_models(db: Session = Depends(get_db)) -> LabModelsOut:
    return _models_out(db)


@router.post("/models/{version_id}/submit", response_model=LabModelsOut)
def submit_model(version_id: uuid.UUID, payload: LabSubmitIn,
                 user: User = Depends(require_role(UserRole.ROLE_ANALYST)), db: Session = Depends(get_db)) -> LabModelsOut:
    version = _get_version(db, version_id)
    return _transition(db, lambda: lab_service.submit_version(db, version, actor=user,
                                                              justification=payload.justification))


@router.post("/models/{version_id}/approve", response_model=LabModelsOut)
def approve_model(version_id: uuid.UUID, payload: LabApproveIn,
                  user: User = Depends(require_role(UserRole.ROLE_MLRO)), db: Session = Depends(get_db)) -> LabModelsOut:
    version = _get_version(db, version_id)
    return _transition(db, lambda: lab_service.approve_version(db, version, actor=user, note=payload.note,
                                                               model_alerts=payload.model_alerts))


@router.post("/models/{version_id}/return", response_model=LabModelsOut)
def return_model(version_id: uuid.UUID, payload: LabReasonIn,
                 user: User = Depends(require_role(UserRole.ROLE_MLRO)), db: Session = Depends(get_db)) -> LabModelsOut:
    version = _get_version(db, version_id)
    return _transition(db, lambda: lab_service.return_version(db, version, actor=user, reason=payload.reason))


@router.post("/models/{version_id}/suspend", response_model=LabModelsOut)
def suspend_model(version_id: uuid.UUID, payload: LabReasonIn,
                  user: User = Depends(require_role(UserRole.ROLE_MLRO)), db: Session = Depends(get_db)) -> LabModelsOut:
    version = _get_version(db, version_id)
    return _transition(db, lambda: lab_service.suspend_version(db, version, actor=user, reason=payload.reason))


@router.post("/model-alerts", response_model=LabModelsOut)
def switch_model_alerts(payload: LabModelAlertsIn, user: User = Depends(require_role(UserRole.ROLE_MLRO)),
                        db: Session = Depends(get_db)) -> LabModelsOut:
    def action():
        lab_service._require_reason(payload.reason, "Alasan")
        lab_service.set_model_alerts(db, actor=user, enabled=payload.enabled, reason=payload.reason)
    return _transition(db, action)


# --- monitoring new data in a dataset's layout --------------------------------------------------


@router.post("/datasets/{dataset_id}/monitor", response_model=LabMonitorOut)
def monitor(
    dataset_id: uuid.UUID,
    file: UploadFile,
    user: User = Depends(require_role(UserRole.ROLE_DATA_OPS)),
    db: Session = Depends(get_db),
) -> LabMonitorOut:
    dataset = _get_dataset(db, dataset_id)
    raw_bytes = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"File melebihi batas {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        result = lab_service.monitor_upload(db, dataset, actor=user, file_name=file.filename or "monitor.csv",
                                            raw_bytes=raw_bytes)
    except lab_service.LabConflict as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LabDataError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return LabMonitorOut(**result, signal_notice=SIGNAL_NOTICE, synthetic_notice=SYNTHETIC_NOTICE)
