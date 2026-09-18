from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.detection import DetectionRunSummary
from app.services.detection.p02_structuring import run_p02_structuring

router = APIRouter()


@router.post("/p02/run", response_model=DetectionRunSummary)
def run_p02(
    user: User = Depends(require_role(UserRole.ROLE_DATA_OPS, UserRole.ROLE_ANALYST)),
    db: Session = Depends(get_db),
) -> DetectionRunSummary:
    return run_p02_structuring(db, actor=user)
