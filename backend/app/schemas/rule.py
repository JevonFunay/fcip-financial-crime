"""Pydantic schemas for the Rule Management API (FR-301, FR-302, FR-303)."""

import uuid
from datetime import datetime, timedelta

from pydantic import BaseModel

from app.models.enums import RuleEntityScope, RuleSeverity, RuleState, RuleWindowType


class RuleSummary(BaseModel):
    """One row in the rule list — identity only, no version details."""

    id: uuid.UUID
    rule_ref: str
    pattern_code: str
    created_at: datetime


class RuleVersionSummary(BaseModel):
    """Version-level summary inside a RuleDetail or the version list."""

    id: uuid.UUID
    version: int
    state: RuleState
    severity: RuleSeverity
    reason_code: str
    description: str
    changed_at: datetime
    change_summary: str


class RuleVersionDetail(RuleVersionSummary):
    """Full version detail including parameters and window settings (FR-301 AC3)."""

    parent_version: int | None
    template_version: str
    parameters: dict
    window_type: RuleWindowType
    window_length: timedelta
    entity_scope: RuleEntityScope
    changed_by: uuid.UUID | None


class RuleDetail(RuleSummary):
    """Full rule: identity + all versions ordered newest first."""

    correlation_id: uuid.UUID
    versions: list[RuleVersionDetail]


class RuleListResponse(BaseModel):
    items: list[RuleSummary]
    total: int


# --- Simulation ---


class SimulateRequest(BaseModel):
    """Optional period to restrict the simulation to.

    FR-303 / TRD §9.5: the simulation runs the template's evaluate() on
    historical data without writing alerts, and stores the result.
    """

    period_start: datetime | None = None
    period_end: datetime | None = None


class SimulationSampleAlert(BaseModel):
    customer_ref: str
    window_start: datetime
    window_end: datetime
    txn_count: int
    aggregate_amount: str


class SimulationResultOut(BaseModel):
    """FR-303: what the rule version would have raised if it had been ACTIVE."""

    id: uuid.UUID
    rule_ref: str
    version: int
    period_start: datetime
    period_end: datetime
    transactions_scanned: int
    would_be_alert_count: int
    unique_entity_count: int
    daily_distribution: dict
    sample_alerts: list[SimulationSampleAlert]
    run_at: datetime
