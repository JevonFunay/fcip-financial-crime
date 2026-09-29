"""Rules as data (FR-301, FR-302, FR-303; TRD §7.3, ADR-006).

A pattern's logic is code (a versioned template); a rule is data: one typed
parameter set bound to that template. `rule` is the rule's identity and never
changes. Every change to what a rule does creates a new `rule_version` row.

What the ORM mapping below cannot express lives in migration
0005_rule_as_data: CHECK constraints, and the triggers that make a version's
content immutable (only `state` ever changes; ACTIVE may only move to
SUSPENDED or RETIRED) and forbid deleting any version (BR-302.1).
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Interval,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.enums import RuleEntityScope, RuleSeverity, RuleState, RuleWindowType


class Rule(Base):
    __tablename__ = "rule"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # RUL-0001, ... from rule_ref_seq (same approach as case_number_seq).
    rule_ref: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    # A different pattern is a different rule, so this is fixed for life.
    pattern_code: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Shared by every audit event of this rule (all versions, all simulations),
    # so one correlation search reads as the rule's full history.
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Null only for rules seeded by a migration rather than created by a person.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RuleVersion(Base):
    __tablename__ = "rule_version"
    __table_args__ = (
        UniqueConstraint("rule_id", "version", name="uq_rule_version_rule_id_version"),
        ForeignKeyConstraint(
            ["rule_id", "parent_version"],
            ["rule_version.rule_id", "rule_version.version"],
            name="fk_rule_version_parent",
        ),
        # FR-301 AC2: a reason code is used by at most one ACTIVE version.
        Index(
            "uq_rule_version_active_reason_code",
            func.upper(text("reason_code")),
            unique=True,
            postgresql_where=text("state = 'ACTIVE'"),
        ),
        # A detection run evaluates a rule's ACTIVE version; two would double-alert.
        Index(
            "uq_rule_version_one_active_per_rule",
            "rule_id",
            unique=True,
            postgresql_where=text("state = 'ACTIVE'"),
        ),
    )

    # Surrogate key: the audit trail and alert/simulation foreign keys need a
    # single UUID. (rule_id, version) is the natural key (TRD §7.3).
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Which version of the pattern's code evaluates these parameters. The
    # (template_version, parameters) pair is what makes an alert reproducible.
    template_version: Mapped[str] = mapped_column(String, nullable=False)
    # Typed per pattern: validated against the pattern's parameter schema before
    # it is written. Decimals are stored as strings so no float rounding creeps in.
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    window_type: Mapped[RuleWindowType] = mapped_column(
        SAEnum(RuleWindowType, name="rule_window_type"), nullable=False
    )
    window_length: Mapped[timedelta] = mapped_column(Interval, nullable=False)
    entity_scope: Mapped[RuleEntityScope] = mapped_column(
        SAEnum(RuleEntityScope, name="rule_entity_scope"), nullable=False
    )
    severity: Mapped[RuleSeverity] = mapped_column(SAEnum(RuleSeverity, name="rule_severity"), nullable=False)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    # Plain-language, mandatory, shown verbatim on every alert (BR-301.2, FR-301 AC3).
    description: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[RuleState] = mapped_column(
        SAEnum(RuleState, name="rule_state"), nullable=False, default=RuleState.DRAFT
    )

    change_summary: Mapped[str] = mapped_column(Text, nullable=False)
    # Null only for versions seeded by a migration.
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SimulationResult(Base):
    """FR-303: a rule version evaluated against historical data with alert
    writing switched off (TRD §9.5). Insert-only."""

    __tablename__ = "simulation_result"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    rule_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule_version.id"), nullable=False, index=True
    )
    # The period actually evaluated, end exclusive.
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    transactions_scanned: Mapped[int] = mapped_column(Integer, nullable=False)
    would_be_alert_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unique_entity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_distribution: Mapped[dict] = mapped_column(JSONB, nullable=False)
    overlap_by_rule: Mapped[list] = mapped_column(JSONB, nullable=False)
    sample_alerts: Mapped[list] = mapped_column(JSONB, nullable=False)

    run_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    # BR-303.2 (checked by FR-304, not built yet): a result older than 7 days
    # cannot support an activation request.
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
