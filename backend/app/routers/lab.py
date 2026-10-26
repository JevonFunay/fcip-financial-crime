"""Model lab API (framework prototype).

Data Ops and Analysts use the lab; every role can look at it. Training runs
synchronously inside the request (seconds to about a minute for ~100k rows):
a background job runner is the next step before larger datasets.
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
from app.models.user import User
from app.schemas.lab import (
    LabDatasetOut,
    LabFeatureGroup,
    LabFieldSpec,
    LabMappingIn,
    LabModelSpec,
    LabOptions,
    LabRunIn,
    LabRunOut,
    LabRunSummary,
    LabSavedModelOut,
    LabSavedVersion,
    LabSaveIn,
)
from app.services import lab as lab_service
from app.services.ml_scoring import SYNTHETIC_NOTICE

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
        file_size_bytes=dataset.file_size_bytes, row_count=dataset.row_count, columns=dataset.columns,
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
