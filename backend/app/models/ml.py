"""Model versions and their scores (FR-401, FR-405; TRD §10.2, §10.5).

A model version is a scoring artefact with the TRD §10.5 envelope. Its content
(the model itself, its threshold, its card) never changes once registered;
migration 0008 enforces that, and the definition hash makes any silent edit
detectable when the model is loaded. A stored score keeps the version that
produced it and is never rewritten (BR-405.1, FR-405 AC3).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import RuleState

UNIT_AML = "AML"      # one (entity, calendar week)
UNIT_FRAUD = "FRAUD"  # one transaction


class ModelVersion(Base):
    __tablename__ = "model_version"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # TRD §10.5 envelope.
    artefact_type: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, unique=True, nullable=False)  # e.g. aml_gbm_v1
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Same lifecycle as a rule (FR-405: "sama seperti FR-204").
    state: Mapped[RuleState] = mapped_column(
        SAEnum(RuleState, name="rule_state", create_type=False), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    simulation_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_version.id"), nullable=True
    )

    unit: Mapped[str] = mapped_column(String, nullable=False)
    feature_set_version: Mapped[str] = mapped_column(String, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    # LightGBM text or validated Isolation Forest JSON: the artefact, never a path.
    model_text: Mapped[str] = mapped_column(Text, nullable=False)
    # model_card.json as written by training: provenance, parameters, metrics, caveat.
    model_card: Mapped[dict] = mapped_column(JSONB, nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MlScore(Base):
    """A model's score for one unit. Stored for every unit at or above the
    threshold, and for every week of an entity that has a rule alert, so the
    alert can show the model's view of it (CF-05 option a)."""

    __tablename__ = "ml_score"
    __table_args__ = (
        Index("uq_ml_score_transaction", "model_version_id", "transaction_id", unique=True,
              postgresql_where=text("transaction_id IS NOT NULL")),
        Index("uq_ml_score_entity_week", "model_version_id", "customer_id", "as_of", unique=True,
              postgresql_where=text("transaction_id IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_version.id"), nullable=False, index=True
    )
    unit: Mapped[str] = mapped_column(String, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer.id"), nullable=False, index=True
    )
    # Set for a Fraud score (one transaction); null for an AML score (one week).
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transaction.id"), nullable=True, index=True
    )
    # The moment the features describe: the week's end, or the transaction's time.
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    # Named features (BR-401.3); model_card.explanation_method defines the scale.
    contributions: Mapped[list] = mapped_column(JSONB, nullable=False)
    detection_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
