"""Model lab service: store a labelled dataset, validate its mapping, train, save a DRAFT.

Every step writes an audit event under the dataset's correlation ID, so one
correlation search shows the dataset, its runs and any model version saved
from them. Saving copies a candidate into the existing `model_version`
registry as DRAFT: the registry's immutability and lifecycle rules apply, and
a DRAFT never scores anything (no FR-405 approval flow exists yet).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ml.lab.features import FEATURE_SET_VERSION, UNIT, definition
from app.ml.lab.mapping import apply_mapping, read_csv_bytes, suggest
from app.ml.lab.trainer import MODELS, SEED, train_run
from app.models.enums import RuleState
from app.models.lab import LAB_DATASET_MAPPED, LAB_DATASET_UPLOADED, LabDataset, LabModel, LabRun
from app.models.ml import ModelVersion
from app.models.user import User
from app.services.audit import OBJECT_LAB_DATASET, OBJECT_LAB_RUN, OBJECT_MODEL_VERSION, record_transition
from app.services.ml_scoring import SIGNAL_NOTICE, SYNTHETIC_NOTICE

LAB_FEATURE_SET_VERSION = f"lab:{FEATURE_SET_VERSION}"
APPROVAL_NOTE = ("DRAFT from the model lab. FR-405's approval flow (analyst submits, MLRO approves) is not built, "
                 "so this version cannot become ACTIVE through the app and never scores uploads.")


class LabConflict(Exception):
    """The request does not fit the dataset's current state; shown to the user."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_dataset(db: Session, *, actor: User, file_name: str, raw_bytes: bytes, name: str | None) -> LabDataset:
    frame = read_csv_bytes(raw_bytes)
    dataset = LabDataset(
        name=(name or "").strip() or file_name,
        file_name=file_name,
        file_sha256=_sha256(raw_bytes),
        file_size_bytes=len(raw_bytes),
        raw_csv=raw_bytes,
        row_count=len(frame),
        columns=[str(c) for c in frame.columns],
        preview=frame.head(8).to_dict("records"),
        suggestion=suggest(frame),
        status=LAB_DATASET_UPLOADED,
        uploaded_by=actor.id,
        correlation_id=uuid.uuid4(),
    )
    db.add(dataset)
    db.flush()
    record_transition(db, correlation_id=dataset.correlation_id, actor=actor, object_type=OBJECT_LAB_DATASET,
                      object_id=dataset.id, from_state=None, to_state=LAB_DATASET_UPLOADED,
                      reason=f"Lab dataset {file_name} uploaded: {len(frame):,} rows, {len(frame.columns)} columns",
                      details={"file_sha256": dataset.file_sha256, "file_size_bytes": len(raw_bytes)})
    db.commit()
    db.refresh(dataset)
    return dataset


def map_dataset(db: Session, dataset: LabDataset, mapping: dict, *, actor: User) -> LabDataset:
    raw = read_csv_bytes(dataset.raw_csv)
    mapped = apply_mapping(raw, mapping)
    previous = dataset.status
    dataset.mapping = mapping
    dataset.validation = mapped.report
    dataset.status = LAB_DATASET_MAPPED
    record_transition(db, correlation_id=dataset.correlation_id, actor=actor, object_type=OBJECT_LAB_DATASET,
                      object_id=dataset.id, from_state=previous, to_state=LAB_DATASET_MAPPED,
                      reason=(f"Mapping validated: {mapped.report['usable_rows']:,} usable rows, "
                              f"{mapped.report['positives']:,} positive, {mapped.report['quarantined']:,} quarantined"),
                      details={"label_column": mapping.get("label_column"), "fields": mapping.get("fields")})
    db.commit()
    db.refresh(dataset)
    return dataset


def train(db: Session, dataset: LabDataset, config: dict, *, actor: User) -> LabRun:
    if dataset.mapping is None:
        raise LabConflict("Petakan dan validasi kolom dataset ini sebelum melatih model.")
    started = time.perf_counter()
    raw = read_csv_bytes(dataset.raw_csv)
    mapped = apply_mapping(raw, dataset.mapping)
    if not mapped.report["ready"]:
        raise LabConflict("Dataset belum memenuhi syarat minimal baris positif dan negatif.")
    outcome = train_run(mapped.frame, mapped.labels, mapped.categories, feature_groups=config["feature_groups"],
                        model_keys=config["models"], train_share=config["train_share"])
    run = LabRun(dataset_id=dataset.id, config=config, results=outcome.results,
                 seconds=round(time.perf_counter() - started, 2), created_by=actor.id,
                 correlation_id=dataset.correlation_id)
    db.add(run)
    db.flush()
    vocabulary = outcome.results["features"]["vocabulary"]
    for model in outcome.models:
        db.add(LabModel(run_id=run.id, model_key=model.key, artefact_type=model.artefact_type,
                        model_text=model.model_text, features=model.features, vocabulary=vocabulary,
                        thresholds=model.thresholds, test_curve=model.test_curve, default_index=model.default_index))
    best = outcome.results["best_model"]
    record_transition(db, correlation_id=dataset.correlation_id, actor=actor, object_type=OBJECT_LAB_RUN,
                      object_id=run.id, from_state=None, to_state="COMPLETED",
                      reason=(f"Lab training on {dataset.name}: {', '.join(MODELS[k]['name'] for k in config['models'])}; "
                              f"best on validation: {MODELS[best]['name']}"),
                      details={"config": config})
    db.commit()
    db.refresh(run)
    return run


def saved_versions(db: Session, run: LabRun) -> list[ModelVersion]:
    return list(db.scalars(select(ModelVersion)
                           .where(ModelVersion.model_card["lab_run_id"].astext == str(run.id))
                           .order_by(ModelVersion.created_at)))


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def save_draft(db: Session, run: LabRun, *, model_key: str, threshold_index: int,
               actor: User) -> tuple[ModelVersion, bool]:
    """The DRAFT model version, and whether it already existed (saving is idempotent)."""
    candidate = db.scalar(select(LabModel).where(LabModel.run_id == run.id, LabModel.model_key == model_key))
    if candidate is None:
        raise LookupError("Model ini tidak dilatih pada run tersebut.")
    if not 0 <= threshold_index < len(candidate.thresholds):
        raise LabConflict("Indeks threshold di luar rentang kurva validasi.")
    version_name = f"lab_{model_key}_{run.id.hex[:8]}_t{threshold_index}"
    existing = db.scalar(select(ModelVersion).where(ModelVersion.version == version_name))
    if existing is not None:
        return existing, True

    summary = next(m for m in run.results["models"] if m["key"] == model_key)
    dataset = db.get(LabDataset, run.dataset_id)
    threshold = float(candidate.thresholds[threshold_index])
    validation_point = summary["validation_curve"][threshold_index]
    test_point = candidate.test_curve[threshold_index]
    spec = MODELS[model_key]
    definition_hash = hashlib.sha256("\n".join((
        candidate.model_text, _canonical(candidate.features), _canonical(candidate.vocabulary),
        LAB_FEATURE_SET_VERSION, repr(threshold),
    )).encode()).hexdigest()
    report = dataset.validation or {}
    card = {
        "version": version_name,
        "artefact_type": candidate.artefact_type,
        "unit": UNIT,
        "state": RuleState.DRAFT.value,
        "definition_hash": definition_hash,
        "model": {
            "algorithm": spec["name"], "training_kind": spec["kind"], "score_kind": spec["score_kind"],
            "explanation_method": "none", "unit": UNIT, "threshold": threshold,
            "threshold_rule": ("chosen by the lab user on the validation curve"
                               + (" (validation max-F1 default)" if threshold_index == candidate.default_index else "")),
            "default_threshold": float(candidate.thresholds[candidate.default_index]),
            "seed": SEED,
        },
        "feature_set": {
            "version": LAB_FEATURE_SET_VERSION, "features": candidate.features,
            "definitions": {f: definition(f) for f in candidate.features}, "vocabulary": candidate.vocabulary,
        },
        "training_data": {
            "source": "model_lab", "profile": f"Lab · {dataset.name}", "seed": SEED, "generator_version": "model lab",
            "dataset_id": str(dataset.id), "dataset_name": dataset.name, "file_name": dataset.file_name,
            "file_sha256": dataset.file_sha256, "usable_rows": report.get("usable_rows"),
            "positives": report.get("positives"), "label_column": (dataset.mapping or {}).get("label_column"),
            "positive_values": (dataset.mapping or {}).get("positive_values"),
            "fields": (dataset.mapping or {}).get("fields"), "split": run.results.get("split"),
        },
        # Flat keys, so the existing model cards in the UI show them like any version's validation.
        "validation": {
            "average_precision": summary["validation_ranking"]["average_precision"],
            "precision": validation_point["precision"], "recall": validation_point["recall"],
            "f1": validation_point["f1"], "threshold": threshold, "at_threshold": validation_point,
        },
        "test": {"at_threshold": test_point, "at_default_threshold": summary["test"]},
        "lab_run_id": str(run.id),
        "lab_model_key": model_key,
        "approval": APPROVAL_NOTE,
        "synthetic_notice": SYNTHETIC_NOTICE,
        "signal_notice": SIGNAL_NOTICE,
    }
    version = ModelVersion(
        artefact_type=candidate.artefact_type, version=version_name, definition_hash=definition_hash,
        state=RuleState.DRAFT, created_by=actor.id, unit=UNIT, feature_set_version=LAB_FEATURE_SET_VERSION,
        threshold=threshold, model_text=candidate.model_text, model_card=card, correlation_id=run.correlation_id,
    )
    db.add(version)
    db.flush()
    record_transition(db, correlation_id=run.correlation_id, actor=actor, object_type=OBJECT_MODEL_VERSION,
                      object_id=version.id, from_state=None, to_state=RuleState.DRAFT.value,
                      reason=(f"Saved from model lab run {run.id}: {spec['name']} at threshold {threshold:.6g}. "
                              + APPROVAL_NOTE),
                      details={"lab_run_id": str(run.id), "model_key": model_key, "threshold_index": threshold_index})
    db.commit()
    db.refresh(version)
    return version, False
