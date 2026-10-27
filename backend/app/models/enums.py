import enum


class UserRole(str, enum.Enum):
    # FRD §4.1 — all 9 roles.  The first four existed since migration 0001;
    # the remaining five are added in migration 0010.
    ROLE_DATA_OPS = "ROLE_DATA_OPS"
    ROLE_ANALYST = "ROLE_ANALYST"
    ROLE_TRIAGE = "ROLE_TRIAGE"
    ROLE_INVESTIGATOR = "ROLE_INVESTIGATOR"
    ROLE_SENIOR_INVESTIGATOR = "ROLE_SENIOR_INVESTIGATOR"
    ROLE_MLRO = "ROLE_MLRO"
    ROLE_AUDITOR = "ROLE_AUDITOR"
    ROLE_MANAGEMENT = "ROLE_MANAGEMENT"
    ROLE_ADMIN = "ROLE_ADMIN"


class EntityType(str, enum.Enum):
    INDIVIDUAL = "INDIVIDUAL"
    BUSINESS = "BUSINESS"


class AccountStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class TransactionDirection(str, enum.Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class IngestionBatchStatus(str, enum.Enum):
    REGISTERED = "REGISTERED"
    COMPLETED = "COMPLETED"
    # TRD C-02: a reconciliation mismatch is flagged for follow-up, never
    # allowed to finish quietly as COMPLETED.
    NEEDS_REVIEW = "NEEDS_REVIEW"
    # TRD C-01: a structural failure leaves the batch FAILED with zero rows loaded.
    FAILED = "FAILED"


class AlertStatus(str, enum.Enum):
    OPEN = "OPEN"
    DISPOSED = "DISPOSED"
    ESCALATED = "ESCALATED"


class CaseStatus(str, enum.Enum):
    """FRD §9 / FR-804 — all seven case lifecycle states."""
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_CLOSURE = "PENDING_CLOSURE"   # awaiting maker-checker (FR-814)
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"
    ESCALATED = "ESCALATED"
    REFERRED = "REFERRED"


class EvidenceType(str, enum.Enum):
    """FR-811: types of evidence that can be attached to a case."""
    DOCUMENT = "DOCUMENT"
    SCREENSHOT = "SCREENSHOT"
    NOTE = "NOTE"
    EXTERNAL_LINK = "EXTERNAL_LINK"
    OTHER = "OTHER"


class RuleState(str, enum.Enum):
    """TRD §8.2 / §17.2. All seven values exist now so FR-304 (submit, approve,
    reject) and FR-305 (suspend, retire) need no schema migration; until those
    are built only DRAFT -> IN_SIMULATION is reachable through the API."""

    DRAFT = "DRAFT"
    IN_SIMULATION = "IN_SIMULATION"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class RuleWindowType(str, enum.Enum):
    # FR-307: "any 24 hours" and "one calendar day" are distinct configured types.
    ROLLING = "ROLLING"
    CALENDAR = "CALENDAR"


class RuleEntityScope(str, enum.Enum):
    # FR-301 flow step 2.
    CUSTOMER = "CUSTOMER"
    MERCHANT = "MERCHANT"
    ACCOUNT = "ACCOUNT"
    DEVICE = "DEVICE"


class RuleSeverity(str, enum.Enum):
    """FRD §8.0: "Skala tingkat keparahan: CRITICAL, HIGH, MEDIUM, LOW". It drives
    priority scoring (FR-311) and SLA targets (FR-607). Declared low-to-high so
    the Postgres enum orders, and compares, by severity."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
