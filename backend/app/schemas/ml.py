import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import RuleState
from app.schemas.detection import DetectionRunSummary


class Contribution(BaseModel):
    feature: str
    # The feature's plain definition (feature set registry), so the reason reads without a glossary.
    definition: str
    value: float | None
    # Its scale is given by the score's explanation_method, never assumed.
    contribution: float


class ScoreOut(BaseModel):
    id: uuid.UUID
    model_version: str
    unit: str
    customer_id: uuid.UUID
    customer_ref: str
    customer_name: str
    as_of: datetime
    transaction_ref: str | None
    transaction_amount: float | None
    score: float
    threshold: float
    flagged: bool
    contributions: list[Contribution]
    feature_set_version: str = "fs_v2"
    explanation_method: str = "tree_shap_log_odds"
    score_kind: str = "supervised_score"
    currency: str | None = None
    timestamp_precision: str | None = None
    source_timezone_known: bool | None = None


class ScoreListResponse(BaseModel):
    items: list[ScoreOut]
    total: int
    limit: int
    offset: int
    # FR-406 and TRD §10.6: carried as fields so no surface can drop them.
    signal_notice: str
    synthetic_notice: str


class ModelVersionOut(BaseModel):
    id: uuid.UUID
    version: str
    unit: str
    state: RuleState
    artefact_type: str
    definition_hash: str
    feature_set_version: str
    threshold: float
    trained_on: dict
    validation: dict
    created_at: datetime
    synthetic_notice: str
    explanation_method: str = "tree_shap_log_odds"
    score_kind: str = "supervised_score"
    training_kind: str = "supervised"
    currency: str | None = None


class UnitRunOut(BaseModel):
    model_version: str
    unit: str
    threshold: float
    units_scored: int
    units_flagged: int
    scores_stored: int
    observations_skipped_currency: int = 0


class MlRunSummary(BaseModel):
    detection_run_id: uuid.UUID
    models: list[UnitRunOut]
    # ACTIVE ML_SCORE rule versions; empty while the rule is DRAFT (CF-05), so
    # "the models scored but raised nothing" is stated, not implied.
    ml_rule_versions_evaluated: list[str]
    alerts_created: int
    alerts_already_existing: int
    seconds: float
    signal_notice: str
    synthetic_notice: str


class DetectionRunAll(BaseModel):
    """One run for rules and models (TRD A22)."""

    rules: list[DetectionRunSummary]
    ml: MlRunSummary
