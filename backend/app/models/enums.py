import enum


class UserRole(str, enum.Enum):
    ROLE_DATA_OPS = "ROLE_DATA_OPS"
    ROLE_ANALYST = "ROLE_ANALYST"
    ROLE_TRIAGE = "ROLE_TRIAGE"
    ROLE_INVESTIGATOR = "ROLE_INVESTIGATOR"


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
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"
