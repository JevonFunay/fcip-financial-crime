"""The model lab: labelled training datasets, training runs and their candidate models.

Prototype of the "framework" direction (upload a labelled dataset, map its
columns, choose features and models, compare, pick a threshold, save a DRAFT
model version). A training dataset is deliberately NOT ingested into the live
`transaction` table: it is study material, not activity to monitor, and it
carries a label column that live data never has.

The original upload — CSV or Parquet — is kept byte-for-byte (`raw_file`,
deferred so listings do not load it) together with its checksum and format,
so a run can always be traced back to exactly the file it was trained on.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column
from sqlalchemy.sql import func

from app.database import Base

LAB_DATASET_UPLOADED = "UPLOADED"
LAB_DATASET_MAPPED = "MAPPED"


class LabDataset(Base):
    __tablename__ = "lab_dataset"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_file: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    file_format: Mapped[str] = mapped_column(String, nullable=False, default="csv")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    columns: Mapped[list] = mapped_column(JSONB, nullable=False)
    preview: Mapped[list] = mapped_column(JSONB, nullable=False)
    # Column-mapping suggestions and small per-column value lists for the UI.
    suggestion: Mapped[dict] = mapped_column(JSONB, nullable=False)
    mapping: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default=LAB_DATASET_UPLOADED)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LabRun(Base):
    """One training run: the configuration and the comparison it produced."""

    __tablename__ = "lab_run"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    protocol_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("lab_evaluation_protocol.id"), nullable=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lab_dataset.id"), nullable=False, index=True
    )
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    results: Mapped[dict] = mapped_column(JSONB, nullable=False)
    seconds: Mapped[float] = mapped_column(Float, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LabModel(Base):
    """A trained candidate inside a run. Saving one copies it into `model_version`
    as DRAFT; the candidate itself stays here unchanged."""

    __tablename__ = "lab_model"
    __table_args__ = (UniqueConstraint("run_id", "model_key", name="uq_lab_model_run_key"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lab_run.id"), nullable=False, index=True)
    model_key: Mapped[str] = mapped_column(String, nullable=False)
    artefact_type: Mapped[str] = mapped_column(String, nullable=False)
    model_text: Mapped[str] = deferred(mapped_column(Text, nullable=False))
    features: Mapped[list] = mapped_column(JSONB, nullable=False)
    vocabulary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Legacy test curves stay for historical rows. Sealed-final candidates
    # store [] and compute only the one locked choice at save.
    thresholds: Mapped[list] = mapped_column(JSONB, nullable=False)
    test_curve: Mapped[list] = deferred(mapped_column(JSONB, nullable=False))
    default_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LabEvaluationProtocol(Base):
    """One logical final evaluation per exact uploaded byte checksum.

    Re-encoding or altering data creates another checksum; this is a local
    workflow lock, never a claim of adversarially hidden source labels.
    """
    __tablename__ = "lab_evaluation_protocol"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                         server_default=text("gen_random_uuid()"))
    source_file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    protocol_version: Mapped[str] = mapped_column(String, nullable=False)
    mapping_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    mapping_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    train_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    legacy_exposed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    final_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("lab_run.id", use_alter=True, name="fk_lab_protocol_final_run"), nullable=True)
    final_model_key: Mapped[str | None] = mapped_column(String, nullable=True)
    final_threshold_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_model_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("model_version.id"), nullable=True)
    final_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
