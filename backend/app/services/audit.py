import uuid

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.user import User

OBJECT_ALERT = "ALERT"
OBJECT_CASE = "CASE"


def record_transition(
    db: Session,
    *,
    correlation_id: uuid.UUID,
    actor: User | None,
    object_type: str,
    object_id: uuid.UUID,
    from_state: str | None,
    to_state: str,
    reason: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        correlation_id=correlation_id,
        actor_user_id=actor.id if actor is not None else None,
        actor_role=actor.role.value if actor is not None else None,
        object_type=object_type,
        object_id=object_id,
        from_state=from_state,
        to_state=to_state,
        reason=reason,
    )
    db.add(entry)
    return entry
