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


class AlertStatus(str, enum.Enum):
    OPEN = "OPEN"
    DISPOSED = "DISPOSED"
    ESCALATED = "ESCALATED"


class CaseStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"
