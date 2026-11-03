from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.detection import DetectionRunSummary
from app.schemas.ml import DetectionRunAll, MlRunSummary, UnitRunOut
from app.services.detection.p02_structuring import run_p02_structuring
from app.services.detection.registry import RULE_PATTERN_CODES, run_all_rule_patterns, run_pattern_by_code
from app.services.ml_scoring import SIGNAL_NOTICE, SYNTHETIC_NOTICE, run_ml_scoring

router = APIRouter()


@router.post("/p02/run", response_model=DetectionRunSummary)
def run_p02(
    user: User = Depends(require_role(UserRole.ROLE_DATA_OPS, UserRole.ROLE_ANALYST)),
    db: Session = Depends(get_db),
) -> DetectionRunSummary:
    return run_p02_structuring(db, actor=user)


@router.post("/patterns/{pattern_code}/run", response_model=DetectionRunSummary)
def run_one_pattern(
    pattern_code: str,
    user: User = Depends(require_role(UserRole.ROLE_DATA_OPS, UserRole.ROLE_ANALYST)),
    db: Session = Depends(get_db),
) -> DetectionRunSummary:
    """Run every ACTIVE rule version of one pattern (P02 and P01, P03-P10)."""
    if pattern_code not in RULE_PATTERN_CODES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No rule template for pattern '{pattern_code}'")
    return run_pattern_by_code(db, pattern_code, actor=user)


@router.post("/run", response_model=DetectionRunAll)
def run_all(
    user: User = Depends(require_role(UserRole.ROLE_DATA_OPS, UserRole.ROLE_ANALYST)),
    db: Session = Depends(get_db),
) -> DetectionRunAll:
    """Every ACTIVE rule, then every ACTIVE model version (TRD A22). Rules run
    first so the models also store their view of every alerted entity."""
    summaries = run_all_rule_patterns(db, actor=user)
    ml = run_ml_scoring(db, actor=user)
    return DetectionRunAll(
        rules=summaries,
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
