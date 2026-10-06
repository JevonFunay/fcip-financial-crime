from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.models.enums import UserRole
from app.models.ml import UNIT_AML, UNIT_FRAUD, ModelVersion
from app.schemas.ml import ModelVersionOut, ScoreListResponse
from app.services.ml_queries import list_scores
from app.services.ml_scoring import SIGNAL_NOTICE, SYNTHETIC_NOTICE

router = APIRouter()

ALL_ROLES = tuple(UserRole)


@router.get("/models", response_model=list[ModelVersionOut], dependencies=[Depends(require_role(*ALL_ROLES))])
def list_models(db: Session = Depends(get_db)) -> list[ModelVersionOut]:
    out = []
    for version in db.scalars(select(ModelVersion).order_by(ModelVersion.unit, ModelVersion.created_at)):
        card = version.model_card
        gbm = card.get("validation", {}).get("gbm", {})
        out.append(ModelVersionOut(
            id=version.id, version=version.version, unit=version.unit, state=version.state,
            artefact_type=version.artefact_type, definition_hash=version.definition_hash,
            feature_set_version=version.feature_set_version, threshold=version.threshold,
            trained_on={k: card.get("training_data", {}).get(k) for k in ("generator_version", "profile", "seed")},
            validation={k: gbm.get(k) for k in ("average_precision", "precision", "recall", "f1")},
            created_at=version.created_at, synthetic_notice=SYNTHETIC_NOTICE,
        ))
    return out


@router.get("/scores", response_model=ScoreListResponse, dependencies=[Depends(require_role(*ALL_ROLES))])
def get_scores(
    unit: str | None = Query(None, pattern=f"^({UNIT_AML}|{UNIT_FRAUD})$"),
    flagged: bool = Query(True, description="only units at or above their model version's threshold"),
    search: str | None = Query(None, min_length=1, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> ScoreListResponse:
    items, total = list_scores(db, unit=unit, flagged_only=flagged, search=search, limit=limit, offset=offset)
    return ScoreListResponse(items=items, total=total, limit=limit, offset=offset,
                             signal_notice=SIGNAL_NOTICE, synthetic_notice=SYNTHETIC_NOTICE)
