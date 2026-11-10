"""Model lab service: dataset, mapping, training, DRAFT, approval, monitoring.

Every step writes an audit event under the dataset's correlation ID, so one
correlation search shows the dataset, its runs, any model version saved from
them and its approval. Saving copies a candidate into the existing
`model_version` registry as DRAFT; the registry's immutability and lifecycle
rules apply.

Approval follows FR-405 ("sama seperti FR-204"): an Analyst submits a DRAFT
with a justification, an MLRO other than the submitter approves it, and only
then does it become ACTIVE, for the next detection run only. The previously
ACTIVE model for the same unit is RETIRED and points to its successor.

Model alerts (CF-05, answered by the mentors on 27 Oct 2026): a score at or
above an ACTIVE model's threshold raises an alert without any pattern rule
firing, through the seeded ML_SCORE rule (RUL-0002). Only an MLRO switches
that rule on or off (FR-304 BR-304.1); the switch is recorded like any rule
state change.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path
import time
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ml.behavioral.features import build_features
from app.ml.lab.features import UNIT, definition
from app.ml.lab.mapping import LabDataError, apply_mapping, map_for_monitoring, read_table_bytes, suggest
from app.ml.lab.scoring import (
    EXPLANATION_METHOD,
    LAB_ARTEFACTS,
    LAB_FEATURE_SET_VERSION,
    SCORE_KIND,
    definition_hash as lab_definition_hash,
)
from app.ml.lab.trainer import (EVALUATION_PROTOCOL, MODELS, SEED, chronological_split, point, ranking, score, train_run)
from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.enums import AccountStatus, EntityType, RuleState
from app.models.lab import LAB_DATASET_MAPPED, LAB_DATASET_UPLOADED, LabDataset, LabEvaluationProtocol, LabModel, LabRun
from app.models.ml import ModelVersion
from app.models.rule import Rule, RuleVersion
from app.models.user import User
from app.services.audit import (
    OBJECT_LAB_DATASET,
    OBJECT_LAB_RUN,
    OBJECT_MODEL_VERSION,
    OBJECT_RULE_VERSION,
    record_transition,
)
from app.services.ml_scoring import ML_PATTERN_CODE, SIGNAL_NOTICE, SYNTHETIC_NOTICE

APPROVAL_NOTE = ("DRAFT from the model lab. It scores nothing until an Analyst submits it and an MLRO other than "
                 "the submitter approves it (FR-405).")
MIN_JUSTIFICATION = 20
CF05_DECISION = ("CF-05 answered by the V-TEKI mentors on 27 Oct 2026 (relayed by the team): a model score at or "
                 "above its approved threshold may raise an alert without a pattern rule firing, so detection is "
                 "not only rule-dependent. Reason code RC-ML-01 and severity MEDIUM remain the team's proposal.")


class LabConflict(Exception):
    """The request does not fit the dataset's current state; shown to the user."""


class LabInvalidSelection(LabConflict):
    """A supplied selection index is outside its candidate contract."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_dataset(db: Session, *, actor: User, file_name: str, raw_bytes: bytes, name: str | None) -> LabDataset:
    frame, file_format = read_table_bytes(raw_bytes)
    dataset = LabDataset(
        name=(name or "").strip() or file_name,
        file_name=file_name,
        file_sha256=_sha256(raw_bytes),
        file_size_bytes=len(raw_bytes),
        raw_file=raw_bytes,
        file_format=file_format,
        row_count=len(frame),
        columns=[str(c) for c in frame.columns],
        preview=frame.head(8).to_dict("records"),
        suggestion=suggest(frame),
        status=LAB_DATASET_UPLOADED,
        uploaded_by=actor.id,
        correlation_id=uuid.uuid4(),
    )
    protocol = _protocol(db, dataset.file_sha256)
    if protocol is not None and not protocol.legacy_exposed:
        # Exact duplicate uploads adopt the first source contract; their UI
        # cannot create a new mapping or a new final partition.
        dataset.mapping = deepcopy(protocol.mapping_snapshot)
        dataset.validation = apply_mapping(frame, dataset.mapping).report
        dataset.status = LAB_DATASET_MAPPED
    db.add(dataset)
    db.flush()
    record_transition(db, correlation_id=dataset.correlation_id, actor=actor, object_type=OBJECT_LAB_DATASET,
                      object_id=dataset.id, from_state=None, to_state=LAB_DATASET_UPLOADED,
                      reason=(f"Lab dataset {file_name} uploaded ({file_format}): {len(frame):,} rows, "
                              f"{len(frame.columns)} columns"),
                      details={"file_sha256": dataset.file_sha256, "file_size_bytes": len(raw_bytes),
                               "file_format": file_format})
    db.commit()
    db.refresh(dataset)
    return dataset


def _json_hash(value) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def _source_lock(db: Session, checksum: str) -> None:
    # Serialises identical-file uploads/runs/saves, including first protocol
    # creation before a row exists. No session/user advisory lock survives a
    # transaction; same-key hash collisions only cause harmless extra waiting.
    key = int.from_bytes(bytes.fromhex(checksum)[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _protocol(db: Session, checksum: str, *, lock: bool = False) -> LabEvaluationProtocol | None:
    query = select(LabEvaluationProtocol).where(LabEvaluationProtocol.source_file_sha256 == checksum)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalar(query)


def _code_hashes() -> dict:
    app = Path(__file__).resolve().parents[1]
    names = ("ml/lab/trainer.py", "ml/lab/features.py", "ml/lab/mapping.py",
             "ml/behavioral/features.py", "ml/behavioral/model.py", "ml/behavioral/standardize.py",
             "services/tabular.py", "services/lab.py")
    return {name: _sha256((app / name).read_bytes()) for name in names}


def _split_hash(frame, parts) -> str:
    return _json_hash([[str(ref), str(part)] for ref, part in zip(frame.transaction_ref, parts)])


def _candidate_hash(candidate) -> str:
    return _json_hash({"model_key": candidate.model_key if hasattr(candidate, "model_key") else candidate.key,
                       "model_text": candidate.model_text, "features": candidate.features,
                       "vocabulary": candidate.vocabulary if hasattr(candidate, "vocabulary") else None,
                       "thresholds": candidate.thresholds, "default_index": candidate.default_index})


def evaluation_state(db: Session, dataset: LabDataset, run: LabRun | None = None) -> dict:
    protocol = _protocol(db, dataset.file_sha256)
    if protocol is None:
        legacy = run is not None or db.scalar(select(LabRun.id).join(LabDataset, LabRun.dataset_id == LabDataset.id)
                                               .where(LabDataset.file_sha256 == dataset.file_sha256,
                                                      LabRun.protocol_id.is_(None)).limit(1)) is not None
        return {"state": "LEGACY_EXPOSED" if legacy else "UNCONFIGURED",
                "source_sha256": dataset.file_sha256, "selected_final": None}
    state = "LEGACY_EXPOSED" if protocol.legacy_exposed else ("REVEALED" if protocol.final_model_version_id else "SEALED")
    version = db.get(ModelVersion, protocol.final_model_version_id) if protocol.final_model_version_id else None
    selected = None
    if version is not None:
        final = version.model_card.get("test", {})
        selected = {"model_key": protocol.final_model_key, "version": version.version,
                    "threshold_index": protocol.final_threshold_index,
                    "at_threshold": final.get("at_threshold", {}), "by_category": final.get("by_category", [])}
    return {"state": state, "protocol_id": str(protocol.id), "source_sha256": protocol.source_file_sha256,
            "mapping_sha256": protocol.mapping_sha256, "train_share": protocol.train_share,
            "final_run_id": str(protocol.final_run_id) if protocol.final_run_id else None,
            "model_key": protocol.final_model_key, "threshold_index": protocol.final_threshold_index,
            "version": version.version if version else None,
            "evaluated_at": protocol.final_evaluated_at.isoformat() if protocol.final_evaluated_at else None,
            "selected_final": selected, "identity_scope": "exact uploaded bytes (SHA-256); re-encoding is not detected"}


def public_run_results(run: LabRun) -> dict:
    # Legacy results stay byte-for-byte in storage, but candidate TEST metrics
    # must not leak through the comparison endpoint after the gate is added.
    results = deepcopy(run.results)
    results.pop("positive_rate_test", None)
    if isinstance(results.get("split", {}).get("test"), dict):
        results["split"]["test"].pop("positives", None)
    for model in results.get("models", []):
        model.pop("test", None)
        model.pop("test_by_category", None)
    return results


def map_dataset(db: Session, dataset: LabDataset, mapping: dict, *, actor: User) -> LabDataset:
    _source_lock(db, dataset.file_sha256)
    protocol = _protocol(db, dataset.file_sha256, lock=True)
    if protocol is not None:
        if protocol.legacy_exposed or _json_hash(mapping) != protocol.mapping_sha256:
            raise LabConflict("Pemetaan sumber ini sudah dikunci oleh pelatihan; gunakan dataset baru untuk pemetaan berbeda.")
        if dataset.mapping == mapping:
            return dataset
    raw, _ = read_table_bytes(dataset.raw_file)
    mapped = apply_mapping(raw, mapping)
    previous = dataset.status
    dataset.mapping = deepcopy(mapping)
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
    _source_lock(db, dataset.file_sha256)
    dataset = db.scalar(select(LabDataset).where(LabDataset.id == dataset.id).with_for_update()
                        .execution_options(populate_existing=True))
    if dataset.mapping is None:
        raise LabConflict("Petakan dan validasi kolom dataset ini sebelum melatih model.")
    if _sha256(dataset.raw_file) != dataset.file_sha256:
        raise LabConflict("Checksum sumber berubah; pelatihan ditolak.")
    protocol = _protocol(db, dataset.file_sha256, lock=True)
    if protocol is not None and (protocol.legacy_exposed or protocol.final_run_id is not None):
        raise LabConflict("Final sumber ini sudah dibuka; file identik tidak dapat dipakai sebagai final baru.")
    if protocol is not None and (protocol.mapping_sha256 != _json_hash(dataset.mapping)
                                 or protocol.train_share != config["train_share"]):
        raise LabConflict("Pemetaan dan porsi latih sudah dikunci pada protokol sumber ini.")
    started = time.perf_counter()
    raw, _ = read_table_bytes(dataset.raw_file)
    mapped = apply_mapping(raw, dataset.mapping)
    if not mapped.report["ready"]:
        raise LabConflict("Dataset belum memenuhi syarat minimal baris positif dan negatif.")
    if protocol is None:
        # Handles old rows inserted before the migration as well as normal
        # migration backfill; a duplicate upload cannot make them unseen.
        prior = db.scalar(select(LabRun.id).join(LabDataset, LabRun.dataset_id == LabDataset.id)
                          .where(LabDataset.file_sha256 == dataset.file_sha256, LabRun.protocol_id.is_(None)).limit(1))
        if prior is not None:
            raise LabConflict("Sumber ini memiliki evaluasi lama yang sudah terbuka; gunakan data final baru.")
        protocol = LabEvaluationProtocol(source_file_sha256=dataset.file_sha256,
                                         protocol_version=EVALUATION_PROTOCOL,
                                         mapping_snapshot=deepcopy(dataset.mapping),
                                         mapping_sha256=_json_hash(dataset.mapping), train_share=config["train_share"],
                                         legacy_exposed=False)
        db.add(protocol)
        db.flush()
    code_hashes = _code_hashes()
    outcome = train_run(mapped.frame, mapped.labels, mapped.categories, feature_groups=config["feature_groups"],
                        model_keys=config["models"], train_share=config["train_share"])
    if code_hashes != _code_hashes():
        raise LabConflict("Kode preprocessing berubah selama pelatihan; jalankan ulang dengan versi tetap.")
    parts = chronological_split(mapped.frame, config["train_share"])
    vocabulary = outcome.results["features"]["vocabulary"]
    candidates = [LabModel(model_key=model.key, artefact_type=model.artefact_type,
                           model_text=model.model_text, features=model.features, vocabulary=vocabulary,
                           thresholds=model.thresholds, test_curve=[], default_index=model.default_index)
                  for model in outcome.models]
    snapshot = {**deepcopy(config), "protocol_version": EVALUATION_PROTOCOL,
                "source_file_sha256": dataset.file_sha256, "mapping_snapshot": deepcopy(protocol.mapping_snapshot),
                "mapping_sha256": protocol.mapping_sha256, "validation_snapshot": deepcopy(mapped.report),
                "split_sha256": _split_hash(mapped.frame, parts), "code_sha256": code_hashes,
                "candidate_sha256": {c.model_key: _candidate_hash(c) for c in candidates}}
    run = LabRun(dataset_id=dataset.id, protocol_id=protocol.id, config=snapshot, results=outcome.results,
                 seconds=round(time.perf_counter() - started, 2), created_by=actor.id,
                 correlation_id=dataset.correlation_id)
    db.add(run)
    db.flush()
    for candidate in candidates:
        candidate.run_id = run.id
        db.add(candidate)
    best = outcome.results["best_model"]
    record_transition(db, correlation_id=dataset.correlation_id, actor=actor, object_type=OBJECT_LAB_RUN,
                      object_id=run.id, from_state=None, to_state="COMPLETED",
                      reason=(f"Lab training on {dataset.name}: {', '.join(MODELS[k]['name'] for k in config['models'])}; "
                              f"best on validation: {MODELS[best]['name']}"),
                      details={"config": config, "protocol_id": str(protocol.id), "mapping_sha256": protocol.mapping_sha256})
    db.commit()
    db.refresh(run)
    return run


def saved_versions(db: Session, run: LabRun) -> list[ModelVersion]:
    return list(db.scalars(select(ModelVersion)
                           .where(ModelVersion.model_card["lab_run_id"].astext == str(run.id))
                           .order_by(ModelVersion.created_at)))


def _selected_final(db: Session, run: LabRun, candidate: LabModel, dataset: LabDataset,
                    threshold: float) -> tuple[dict, list]:
    """Called only after the one choice is flushed under the source lock."""
    if _sha256(dataset.raw_file) != run.config["source_file_sha256"]:
        raise LabConflict("Checksum dataset tidak cocok dengan snapshot run.")
    if _code_hashes() != run.config["code_sha256"]:
        raise LabConflict("Versi kode berubah sejak pelatihan; evaluasi final run lama ditolak.")
    if _candidate_hash(candidate) != run.config["candidate_sha256"][candidate.model_key]:
        raise LabConflict("Artefak kandidat tidak cocok dengan kontrak yang dibekukan.")
    raw, _ = read_table_bytes(dataset.raw_file)
    mapped = apply_mapping(raw, run.config["mapping_snapshot"])
    parts = chronological_split(mapped.frame, run.config["train_share"])
    if _split_hash(mapped.frame, parts) != run.config["split_sha256"]:
        raise LabConflict("Batas final tidak cocok dengan snapshot pelatihan.")
    features = build_features(mapped.frame, UNIT, candidate.vocabulary)
    order = pd.Series(np.arange(len(mapped.frame)), index=mapped.frame.transaction_ref)
    features = features.set_index(order.loc[features.transaction_ref].to_numpy()).sort_index()
    mask = parts == "test"
    values = np.asarray(score(candidate.model_key, candidate.model_text, features.loc[mask, candidate.features]))
    if values.shape != (int(mask.sum()),) or not np.isfinite(values).all():
        raise LabConflict("Skor final tidak finite atau tidak sejajar dengan observasi.")
    truth = mapped.labels[mask]
    metrics = {**point(values, truth, threshold), **ranking(values, truth), "rows": len(truth),
               "positives": int(truth.sum())}
    categories = []
    if mapped.categories is not None:
        supplied = mapped.categories[mask].to_numpy()
        for category in sorted(set(supplied[truth == 1])):
            selected = (truth == 1) & (supplied == category)
            categories.append({"category": category or "(kosong)", "positives": int(selected.sum()),
                               "caught": int((selected & (values >= threshold)).sum())})
    return metrics, categories


def save_draft(db: Session, run: LabRun, *, model_key: str, threshold_index: int,
               actor: User) -> tuple[ModelVersion, bool]:
    """Freeze and evaluate exactly one candidate/threshold for this source."""
    version_name = f"lab_{model_key}_{run.id.hex[:8]}_t{threshold_index}"
    if run.protocol_id is None:
        # Historical versions remain readable/idempotent; no new final claim.
        existing = db.scalar(select(ModelVersion).where(ModelVersion.version == version_name))
        if existing is not None:
            return existing, True
        raise LabConflict("Run lama sudah mengekspos final; hanya versi yang telah disimpan dapat dibaca kembali.")
    checksum = run.config["source_file_sha256"]
    _source_lock(db, checksum)
    protocol = _protocol(db, checksum, lock=True)
    if protocol is None or protocol.legacy_exposed or protocol.id != run.protocol_id:
        raise LabConflict("Run tidak memiliki protokol final tertutup yang sah.")
    if protocol.final_run_id is not None:
        if (protocol.final_run_id == run.id and protocol.final_model_key == model_key
                and protocol.final_threshold_index == threshold_index and protocol.final_model_version_id is not None):
            return db.get(ModelVersion, protocol.final_model_version_id), True
        raise LabConflict("Final sumber ini sudah dikunci untuk satu pilihan; model atau threshold lain tidak dapat dievaluasi.")
    candidate = db.scalar(select(LabModel).where(LabModel.run_id == run.id, LabModel.model_key == model_key))
    if candidate is None:
        raise LookupError("Model ini tidak dilatih pada run tersebut.")
    if not 0 <= threshold_index < len(candidate.thresholds):
        raise LabInvalidSelection("Indeks threshold di luar rentang kurva validasi.")
    dataset = db.get(LabDataset, run.dataset_id)
    summary = next(m for m in run.results["models"] if m["key"] == model_key)
    threshold = float(candidate.thresholds[threshold_index])
    validation_point = summary["validation_curve"][threshold_index]
    protocol.final_run_id = run.id
    protocol.final_model_key = model_key
    protocol.final_threshold_index = threshold_index
    protocol.final_locked_at = datetime.now(timezone.utc)
    db.flush()
    test_point, by_category = _selected_final(db, run, candidate, dataset, threshold)
    spec = MODELS[model_key]
    definition_hash = lab_definition_hash(candidate.model_text, candidate.features, candidate.vocabulary, threshold)
    report = run.config["validation_snapshot"]
    mapping = run.config["mapping_snapshot"]
    card = {
        "version": version_name,
        "artefact_type": candidate.artefact_type,
        "unit": UNIT,
        "state": RuleState.DRAFT.value,
        "definition_hash": definition_hash,
        "model": {
            "algorithm": spec["name"], "training_kind": spec["kind"],
            "score_kind": SCORE_KIND[candidate.artefact_type],
            "explanation_method": EXPLANATION_METHOD[candidate.artefact_type], "unit": UNIT, "threshold": threshold,
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
            "positives": report.get("positives"), "label_column": mapping.get("label_column"),
            "positive_values": mapping.get("positive_values"),
            "fields": mapping.get("fields"), "split": run.results.get("split"),
            "mapping_snapshot": mapping, "mapping_sha256": run.config["mapping_sha256"],
            "protocol_id": str(protocol.id), "protocol_version": EVALUATION_PROTOCOL,
        },
        # Flat keys, so the existing model cards in the UI show them like any version's validation.
        "validation": {
            "average_precision": summary["validation_ranking"]["average_precision"],
            "precision": validation_point["precision"], "recall": validation_point["recall"],
            "f1": validation_point["f1"], "threshold": threshold, "at_threshold": validation_point,
        },
        "test": {"at_threshold": test_point, "by_category": by_category, "revealed_once": True,
                 "locked_at": protocol.final_locked_at.isoformat()},
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
    protocol.final_model_version_id = version.id
    protocol.final_evaluated_at = datetime.now(timezone.utc)
    record_transition(db, correlation_id=run.correlation_id, actor=actor, object_type=OBJECT_MODEL_VERSION,
                      object_id=version.id, from_state=None, to_state=RuleState.DRAFT.value,
                      reason=(f"Saved from model lab run {run.id}: {spec['name']} at threshold {threshold:.6g}. "
                              + APPROVAL_NOTE),
                      details={"lab_run_id": str(run.id), "model_key": model_key, "threshold_index": threshold_index})
    db.commit()
    db.refresh(version)
    return version, False


# --- approval (FR-405) and model alerts (CF-05) ------------------------------------------------


def lab_versions(db: Session) -> list[ModelVersion]:
    return list(db.scalars(select(ModelVersion).where(ModelVersion.artefact_type.in_(LAB_ARTEFACTS))
                           .order_by(ModelVersion.created_at.desc())))


def get_lab_version(db: Session, version_id: uuid.UUID) -> ModelVersion | None:
    version = db.get(ModelVersion, version_id)
    return version if version is not None and version.artefact_type in LAB_ARTEFACTS else None


def version_history(db: Session, version: ModelVersion) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).where(AuditLog.object_type == OBJECT_MODEL_VERSION,
                                                  AuditLog.object_id == version.id)
                           .order_by(AuditLog.created_at, AuditLog.id)))


def _ml_rule(db: Session) -> tuple[Rule, list[RuleVersion]]:
    rule = db.scalar(select(Rule).where(Rule.pattern_code == ML_PATTERN_CODE).order_by(Rule.rule_ref))
    if rule is None:
        raise LabConflict("Rule ML_SCORE (RUL-0002) tidak ditemukan; jalankan migrasi database terbaru.")
    versions = list(db.scalars(select(RuleVersion).where(RuleVersion.rule_id == rule.id)
                               .order_by(RuleVersion.version.desc())))
    return rule, versions


def model_alerts_state(db: Session) -> dict:
    rule, versions = _ml_rule(db)
    active = next((v for v in versions if v.state == RuleState.ACTIVE), None)
    shown = active or (versions[0] if versions else None)
    return {"rule_ref": rule.rule_ref, "version": shown.version if shown else None,
            "state": shown.state.value if shown else None, "active": active is not None,
            "reason_code": shown.reason_code if shown else None,
            "severity": shown.severity.value if shown else None}


def set_model_alerts(db: Session, *, actor: User, enabled: bool, reason: str, commit: bool = True) -> dict:
    """Switch ML_SCORE on or off. Only the state of the existing rule version moves
    (rule versions are immutable otherwise); the MLRO's reason is recorded."""
    rule, versions = _ml_rule(db)
    active = next((v for v in versions if v.state == RuleState.ACTIVE), None)
    if enabled and active is None:
        target = next((v for v in versions if v.state in (RuleState.DRAFT, RuleState.SUSPENDED)), None)
        if target is None:
            raise LabConflict("Tidak ada versi rule ML_SCORE yang dapat diaktifkan.")
        previous = target.state
        target.state = RuleState.ACTIVE
        record_transition(db, correlation_id=rule.correlation_id, actor=actor, object_type=OBJECT_RULE_VERSION,
                          object_id=target.id, from_state=previous.value, to_state=RuleState.ACTIVE.value,
                          reason=f"{reason.strip()} — {CF05_DECISION}", action="RULE_VERSION_ACTIVATED",
                          details={"rule_ref": rule.rule_ref, "version": target.version, "pattern": ML_PATTERN_CODE,
                                   "maker_checker": "activated directly by an MLRO; the seeded rule has no maker "
                                                    "submission, as documented for RUL-0001"})
    elif not enabled and active is not None:
        active.state = RuleState.SUSPENDED
        record_transition(db, correlation_id=rule.correlation_id, actor=actor, object_type=OBJECT_RULE_VERSION,
                          object_id=active.id, from_state=RuleState.ACTIVE.value,
                          to_state=RuleState.SUSPENDED.value, reason=reason.strip(),
                          action="RULE_VERSION_SUSPENDED", details={"rule_ref": rule.rule_ref,
                                                                    "version": active.version})
    if commit:
        db.commit()
    return model_alerts_state(db)


def _require_reason(text: str | None, what: str) -> str:
    text = (text or "").strip()
    if len(text) < MIN_JUSTIFICATION:
        raise LabConflict(f"{what} minimal {MIN_JUSTIFICATION} karakter.")
    return text


def submit_version(db: Session, version: ModelVersion, *, actor: User, justification: str) -> ModelVersion:
    if version.state != RuleState.DRAFT:
        raise LabConflict(f"Hanya model berstatus DRAFT yang dapat diajukan (status sekarang {version.state.value}).")
    justification = _require_reason(justification, "Justifikasi pengajuan")
    version.state = RuleState.PENDING_APPROVAL
    version.submitted_by = actor.id
    version.simulation_reference = f"lab_run:{version.model_card.get('lab_run_id')}"
    record_transition(db, correlation_id=version.correlation_id, actor=actor, object_type=OBJECT_MODEL_VERSION,
                      object_id=version.id, from_state=RuleState.DRAFT.value,
                      to_state=RuleState.PENDING_APPROVAL.value, reason=justification,
                      action="ANOMALY_MODEL_VERSION_SUBMITTED")
    db.commit()
    db.refresh(version)
    return version


def approve_version(db: Session, version: ModelVersion, *, actor: User, note: str,
                    model_alerts: bool | None) -> ModelVersion:
    if version.state != RuleState.PENDING_APPROVAL:
        raise LabConflict(f"Hanya model yang menunggu persetujuan yang dapat disetujui (status {version.state.value}).")
    note = _require_reason(note, "Catatan persetujuan")
    if actor.id in {version.submitted_by, version.created_by}:
        # FR-405 E1 / AC2: self-approval is refused and recorded.
        record_transition(db, correlation_id=version.correlation_id, actor=actor, object_type=OBJECT_MODEL_VERSION,
                          object_id=version.id, from_state=version.state.value, to_state=version.state.value,
                          reason="Self-approval refused (FR-405 E1)", action="ANOMALY_MODEL_SELF_APPROVAL_REFUSED")
        db.commit()
        raise LabConflict("Persetujuan atas pengajuan sendiri tidak diizinkan (FR-405).")
    now = datetime.now(timezone.utc)
    for previous in db.scalars(select(ModelVersion).where(ModelVersion.unit == version.unit,
                                                          ModelVersion.state == RuleState.ACTIVE,
                                                          ModelVersion.id != version.id)):
        previous.state = RuleState.RETIRED
        previous.superseded_by = version.id
        record_transition(db, correlation_id=previous.correlation_id, actor=actor, object_type=OBJECT_MODEL_VERSION,
                          object_id=previous.id, from_state=RuleState.ACTIVE.value, to_state=RuleState.RETIRED.value,
                          reason=f"Superseded by {version.version}, approved by the MLRO",
                          action="ANOMALY_MODEL_VERSION_RETIRED")
    # uq_model_version_one_active_per_unit (migration 0008): the old version must
    # leave ACTIVE in the database before the new one enters it.
    db.flush()
    version.state = RuleState.ACTIVE
    version.approved_by = actor.id
    version.approved_at = now
    version.effective_from = now
    record_transition(db, correlation_id=version.correlation_id, actor=actor, object_type=OBJECT_MODEL_VERSION,
                      object_id=version.id, from_state=RuleState.PENDING_APPROVAL.value,
                      to_state=RuleState.ACTIVE.value, reason=note, action="ANOMALY_MODEL_VERSION_APPROVED",
                      details={"effective_from": now.isoformat(), "applies_to": "the next detection run only"})
    if model_alerts is not None:
        set_model_alerts(db, actor=actor, enabled=model_alerts, reason=note, commit=False)
    db.commit()
    db.refresh(version)
    return version


def return_version(db: Session, version: ModelVersion, *, actor: User, reason: str) -> ModelVersion:
    if version.state != RuleState.PENDING_APPROVAL:
        raise LabConflict(f"Hanya model yang menunggu persetujuan yang dapat dikembalikan (status {version.state.value}).")
    reason = _require_reason(reason, "Alasan pengembalian")
    version.state = RuleState.DRAFT
    record_transition(db, correlation_id=version.correlation_id, actor=actor, object_type=OBJECT_MODEL_VERSION,
                      object_id=version.id, from_state=RuleState.PENDING_APPROVAL.value,
                      to_state=RuleState.DRAFT.value, reason=reason, action="ANOMALY_MODEL_VERSION_RETURNED")
    db.commit()
    db.refresh(version)
    return version


def suspend_version(db: Session, version: ModelVersion, *, actor: User, reason: str) -> ModelVersion:
    if version.state != RuleState.ACTIVE:
        raise LabConflict(f"Hanya model ACTIVE yang dapat dinonaktifkan (status {version.state.value}).")
    reason = _require_reason(reason, "Alasan penonaktifan")
    version.state = RuleState.SUSPENDED
    record_transition(db, correlation_id=version.correlation_id, actor=actor, object_type=OBJECT_MODEL_VERSION,
                      object_id=version.id, from_state=RuleState.ACTIVE.value, to_state=RuleState.SUSPENDED.value,
                      reason=reason, action="ANOMALY_MODEL_VERSION_SUSPENDED")
    db.commit()
    db.refresh(version)
    return version


# --- monitoring new data in a training dataset's layout ----------------------------------------

MONITOR_SOURCE_SYSTEM = "MODEL_LAB"
CANONICAL_ORDER = (
    "transaction_ref", "account_number", "transaction_date", "amount", "currency", "direction", "channel",
    "device_ref", "merchant_ref", "location", "ip_address", "login_attempts", "transaction_duration",
    "account_balance", "customer_age", "customer_occupation", "timestamp_precision", "source_timezone_known",
    "counterparty_ref", "source_status", "description", "transaction_type", "counterparty_country",
)


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat") and hasattr(value, "tzinfo"):
        return value.isoformat()
    return str(value)


def _canonical_csv(raw, mapping: dict, accepted) -> str:
    """Every row of the upload in FCIP's canonical columns, in file order.

    Rows that passed validation are written in their validated form; rows that
    did not are written as mapped, so ingestion quarantines them with a reason
    and the batch accounts for every row of the file (FR-102, FR-105)."""
    from app.ml.lab.mapping import DIRECTION_SYNONYMS

    fields = {k: v for k, v in (mapping.get("fields") or {}).items() if v}
    default_currency = (mapping.get("default_currency") or "XXX").strip().upper()
    good = {int(row["source_ordinal"]): row for row in accepted.to_dict("records")}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CANONICAL_ORDER)
    for position in range(len(raw)):
        row = good.get(position + 1)
        if row is not None:
            writer.writerow([_cell(row.get(c)) for c in CANONICAL_ORDER])
            continue
        source = {key: str(raw[column].iat[position]) for key, column in fields.items()}
        source.setdefault("transaction_ref", f"ROW-{position + 1:07d}")
        direction = source.get("direction", "DEBIT").strip().upper()
        source["direction"] = DIRECTION_SYNONYMS.get(direction, direction)
        source.setdefault("channel", "UNKNOWN")
        if not source.get("currency", "").strip():
            source["currency"] = default_currency
        writer.writerow([source.get(c, "") for c in CANONICAL_ORDER])
    return out.getvalue()


def _ensure_account_proxies(db: Session, accepted) -> int:
    """An anonymous observation proxy for every account the platform has not seen,
    as app/scripts/load_behavioral_dataset.py builds them: no person, no identity
    facts. Existing accounts — proxies or real master data — are used as they are."""
    from app.services.ingestion import LOOKUP_CHUNK_SIZE

    currencies = accepted.groupby("account_number").currency.unique()
    ambiguous = [number for number, values in currencies.items() if len(values) != 1]
    if ambiguous:
        raise LabDataError(f"Rekening {', '.join(map(str, ambiguous[:5]))} memakai lebih dari satu mata uang; "
                           "FCIP tidak mengonversi kurs. Pisahkan file per mata uang.")
    numbers = [str(n) for n in currencies.index]
    existing: set[str] = set()
    for start in range(0, len(numbers), LOOKUP_CHUNK_SIZE):
        existing.update(db.scalars(select(Account.account_number)
                                   .where(Account.account_number.in_(numbers[start:start + LOOKUP_CHUNK_SIZE]))))
    missing = [n for n in numbers if n not in existing]
    refs = [f"OBS-{n}" for n in missing]
    customers: dict[str, Customer] = {}
    for start in range(0, len(refs), LOOKUP_CHUNK_SIZE):
        customers.update({c.customer_ref: c for c in db.scalars(
            select(Customer).where(Customer.customer_ref.in_(refs[start:start + LOOKUP_CHUNK_SIZE])))})
    accounts = []
    for number in missing:
        customer = customers.get(f"OBS-{number}")
        if customer is None:
            # INDIVIDUAL satisfies the technical schema only; no person is inferred.
            customer = Customer(id=uuid.uuid4(), customer_ref=f"OBS-{number}", full_name=number,
                                entity_type=EntityType.INDIVIDUAL)
            db.add(customer)
        accounts.append(Account(account_number=number, customer_id=customer.id, account_type="OBSERVATION_PROXY",
                                currency=str(currencies[number][0]), status=AccountStatus.ACTIVE, opened_date=None))
    # No ORM relationship orders these inserts, so customers go first (as load_account_proxies does).
    db.flush()
    db.add_all(accounts)
    db.flush()
    return len(missing)


def _monitor_mapping(db: Session, dataset: LabDataset) -> dict:
    """Use the approved model's provenance, never a mutable later remapping."""
    active = list(db.scalars(select(ModelVersion).where(ModelVersion.state == RuleState.ACTIVE,
                                                       ModelVersion.artefact_type.in_(LAB_ARTEFACTS))))
    matching = [v for v in active if v.model_card.get("training_data", {}).get("dataset_id") == str(dataset.id)
                or v.model_card.get("training_data", {}).get("file_sha256") == dataset.file_sha256]
    if matching:
        data = matching[0].model_card.get("training_data", {})
        mapping = data.get("mapping_snapshot")
        if not mapping or _json_hash(mapping) != data.get("mapping_sha256"):
            raise LabConflict("Model lama tidak memiliki snapshot pemetaan lengkap; siapkan versi baru sebelum monitoring file sumber.")
        return deepcopy(mapping)
    if active:
        raise LabConflict("Dataset ini bukan sumber model ACTIVE; pilih dataset yang pemetaannya disetujui bersama model.")
    protocol = _protocol(db, dataset.file_sha256)
    if protocol is not None:
        if protocol.legacy_exposed or not protocol.mapping_snapshot:
            raise LabConflict("Sumber lama tidak memiliki kontrak pemetaan monitoring yang dibekukan.")
        return deepcopy(protocol.mapping_snapshot)
    if dataset.mapping is None:
        raise LabConflict("Petakan kolom dataset ini terlebih dahulu; pemantauan memakai pemetaan yang sama.")
    return deepcopy(dataset.mapping)


def monitor_upload(db: Session, dataset: LabDataset, *, actor: User, file_name: str, raw_bytes: bytes) -> dict:
    """Ingest a new file in this dataset's layout as a monitored batch, then run detection:
    every ACTIVE rule, then every ACTIVE model (and ML_SCORE alerts when switched on)."""
    from app.services.detection.registry import run_all_rule_patterns
    from app.services.ingestion import DuplicateBatchError, IngestionFileError, ingest_transactions_csv, register_batch
    from app.services.ml_scoring import run_ml_scoring

    mapping = _monitor_mapping(db, dataset)
    started = time.perf_counter()
    raw, file_format = read_table_bytes(raw_bytes)
    mapped = map_for_monitoring(raw, mapping)
    proxies = _ensure_account_proxies(db, mapped.frame) if len(mapped.frame) else 0
    content = _canonical_csv(raw, mapping, mapped.frame)
    try:
        batch = register_batch(db, actor=actor, file_name=file_name, raw_bytes=raw_bytes,
                               source_system=MONITOR_SOURCE_SYSTEM, business_date=datetime.now(timezone.utc).date(),
                               expected_records=len(raw))
    except DuplicateBatchError as exc:
        db.rollback()
        raise LabConflict(f"File ini sudah pernah diunggah untuk pemantauan hari ini: {exc}") from exc
    try:
        summary = ingest_transactions_csv(db, file_name=file_name, content=content, batch=batch, actor=actor)
    except IngestionFileError as exc:
        db.rollback()
        raise LabDataError(f"Ingestion gagal: {exc}") from exc
    record_transition(db, correlation_id=dataset.correlation_id, actor=actor, object_type=OBJECT_LAB_DATASET,
                      object_id=dataset.id, from_state=dataset.status, to_state=dataset.status,
                      action="LAB_MONITOR_UPLOAD",
                      reason=(f"{file_name} ({file_format}) ingested in this dataset's layout as {summary.batch_ref}: "
                              f"{summary.accepted:,} accepted, {summary.quarantined:,} quarantined, "
                              f"{proxies:,} account proxies created"),
                      details={"batch_ref": summary.batch_ref, "file_format": file_format,
                               "account_proxies_created": proxies})
    db.commit()
    rules = run_all_rule_patterns(db, actor=actor)
    ml = run_ml_scoring(db, actor=actor)
    return {
        "file_name": file_name, "file_format": file_format, "mapping_report": mapped.report,
        "batch_ref": summary.batch_ref, "batch_status": summary.batch_status, "total_rows": summary.total_rows,
        "accepted": summary.accepted, "quarantined": summary.quarantined,
        "quarantine_preview": [p.model_dump() for p in summary.quarantine_preview],
        "account_proxies_created": proxies,
        "rule_alerts_created": sum(rule.alerts_created for rule in rules),
        "models": [r.__dict__ for r in ml.models],
        "model_alerts_created": ml.alerts_created,
        "model_alerts_active": bool(ml.ml_rule_versions_evaluated),
        "seconds": round(time.perf_counter() - started, 1),
    }
