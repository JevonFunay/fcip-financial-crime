"""The model lab: labelled training datasets, training runs and their candidate models.

Prototype of the "framework" direction (upload a labelled dataset, map its
columns, choose features and models, compare, pick a threshold, save a DRAFT
model version). A training dataset is deliberately NOT ingested into the live
`transaction` table: it is study material, not activity to monitor, and it
carries a label column that live data never has.

The original upload is kept byte-for-byte (`raw_csv`, deferred so listings do
not load it) together with its checksum, so a run can always be traced back to
exactly the file it was trained on.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, text
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
    raw_csv: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
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
    # The validation curve is what the threshold slider shows. The test curve
    # (same candidate thresholds) is only read when a model is saved, so the
    # final figures never steer the choice of threshold.
    thresholds: Mapped[list] = mapped_column(JSONB, nullable=False)
    test_curve: Mapped[list] = deferred(mapped_column(JSONB, nullable=False))
    default_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
