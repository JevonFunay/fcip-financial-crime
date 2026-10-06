from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.detection import DetectionRunSummary
from app.schemas.ml import DetectionRunAll, MlRunSummary, UnitRunOut
from app.services.detection.p02_structuring import run_p02_structuring
from app.services.ml_scoring import SIGNAL_NOTICE, SYNTHETIC_NOTICE, run_ml_scoring

router = APIRouter()


@router.post("/p02/run", response_model=DetectionRunSummary)
def run_p02(
    user: User = Depends(require_role(UserRole.ROLE_DATA_OPS, UserRole.ROLE_ANALYST)),
    db: Session = Depends(get_db),
) -> DetectionRunSummary:
    return run_p02_structuring(db, actor=user)


@router.post("/run", response_model=DetectionRunAll)
def run_all(
    user: User = Depends(require_role(UserRole.ROLE_DATA_OPS, UserRole.ROLE_ANALYST)),
    db: Session = Depends(get_db),
) -> DetectionRunAll:
    """Every ACTIVE rule, then every ACTIVE model version (TRD A22). Rules run
    first so the models also store their view of every alerted entity."""
    p02 = run_p02_structuring(db, actor=user)
    ml = run_ml_scoring(db, actor=user)
    return DetectionRunAll(
        rules=[p02],
        ml=MlRunSummary(
            detection_run_id=ml.detection_run_id,
            models=[UnitRunOut(**r.__dict__) for r in ml.models],
            ml_rule_versions_evaluated=ml.ml_rule_versions_evaluated,
            alerts_created=ml.alerts_created,
            alerts_already_existing=ml.alerts_already_existing,
            seconds=ml.seconds,
            signal_notice=SIGNAL_NOTICE,
            synthetic_notice=SYNTHETIC_NOTICE,
        ),
    )
