import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import RuleState


class LabFieldSpec(BaseModel):
    key: str
    label: str
    required: bool
    help: str


class LabFeatureGroup(BaseModel):
    key: str
    label: str
    description: str
    default: bool
    features: list[str]


class LabModelSpec(BaseModel):
    key: str
    name: str
    kind: Literal["supervised", "unsupervised"]
    description: str
    default: bool


class LabOptions(BaseModel):
    fields: list[LabFieldSpec]
    feature_groups: list[LabFeatureGroup]
    models: list[LabModelSpec]
    max_upload_bytes: int
    min_per_class: int
    synthetic_notice: str


class LabSuggestion(BaseModel):
    fields: dict[str, str]
    label_column: str | None
    positive_values: list[str]
    category_column: str | None
    date_format: Literal["iso", "month_first"]
    low_cardinality: dict[str, list[str]]


class LabMappingIn(BaseModel):
    fields: dict[str, str | None]
    label_column: str = Field(min_length=1)
    positive_values: list[str] = Field(min_length=1)
    category_column: str | None = None
    date_format: Literal["iso", "month_first"] = "iso"
    default_currency: str | None = Field(None, max_length=3)


class LabRunSummary(BaseModel):
    id: uuid.UUID
    created_at: datetime
    seconds: float
    best_model: str | None
    models: list[str]


class LabDatasetOut(BaseModel):
    id: uuid.UUID
    name: str
    file_name: str
    file_sha256: str
    file_size_bytes: int
    file_format: str
    row_count: int
    columns: list[str]
    preview: list[dict]
    suggestion: LabSuggestion
    mapping: dict | None
    validation: dict | None
    status: str
    uploaded_by_email: str | None
    created_at: datetime
    runs: list[LabRunSummary] = []
    evaluation: dict = {}


class LabRunIn(BaseModel):
    feature_groups: list[str] = Field(min_length=1)
    models: list[str] = Field(min_length=1)
    train_share: float = Field(0.6, ge=0.5, le=0.8)


class LabSavedVersion(BaseModel):
    model_key: str
    version: str
    threshold: float


class LabRunOut(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    dataset_name: str
    created_at: datetime
    seconds: float
    config: dict
    results: dict
    saved_versions: list[LabSavedVersion]
    synthetic_notice: str
    evaluation: dict = {}


class LabSaveIn(BaseModel):
    model_key: str
    threshold_index: int = Field(ge=0)


class LabSavedModelOut(BaseModel):
    model_version_id: uuid.UUID
    version: str
    state: RuleState
    threshold: float
    model_key: str
    model_name: str
    validation: dict
    test: dict
    created_at: datetime
    already_saved: bool


class LabHistoryEntry(BaseModel):
    at: datetime
    actor_email: str | None
    actor_role: str | None
    from_state: str | None
    to_state: str
    action: str | None
    reason: str | None


class LabModelVersionOut(BaseModel):
    evaluation_state: str = "LEGACY_EXPOSED"
    id: uuid.UUID
    version: str
    state: RuleState
    artefact_type: str
    model_key: str | None
    model_name: str
    training_kind: str
    threshold: float
    dataset_id: uuid.UUID | None
    dataset_name: str | None
    lab_run_id: uuid.UUID | None
    validation: dict
    test: dict
    created_by_email: str | None
    submitted_by_email: str | None
    approved_by_email: str | None
    approved_at: datetime | None
    superseded_by_version: str | None
    created_at: datetime
    history: list[LabHistoryEntry]


class LabModelAlertsState(BaseModel):
    rule_ref: str
    version: int | None
    state: str | None
    active: bool
    reason_code: str | None
    severity: str | None


class LabModelsOut(BaseModel):
    items: list[LabModelVersionOut]
    model_alerts: LabModelAlertsState
    min_reason_length: int
    synthetic_notice: str
    signal_notice: str


class LabSubmitIn(BaseModel):
    justification: str = Field(min_length=1, max_length=2000)


class LabApproveIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000)
    # None leaves the ML_SCORE switch as it is; True/False switches it with this approval.
    model_alerts: bool | None = None


class LabReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class LabModelAlertsIn(BaseModel):
    enabled: bool
    reason: str = Field(min_length=1, max_length=2000)


class LabMonitorModelRun(BaseModel):
    model_version: str
    unit: str
    threshold: float
    units_scored: int
    units_flagged: int
    scores_stored: int
    observations_skipped_currency: int = 0


class LabMonitorOut(BaseModel):
    file_name: str
    file_format: str
    mapping_report: dict
    batch_ref: str | None
    batch_status: str | None
    total_rows: int
    accepted: int
    quarantined: int
    quarantine_preview: list[dict]
    account_proxies_created: int
    rule_alerts_created: int
    models: list[LabMonitorModelRun]
    model_alerts_created: int
    model_alerts_active: bool
    seconds: float
    signal_notice: str
    synthetic_notice: str
