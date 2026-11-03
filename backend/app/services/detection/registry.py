"""Every pattern template the platform can run or simulate, by pattern code."""

from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.detection import DetectionRunSummary
from app.services.detection import (
    p01_large_txn, p03_rapid_movement, p04_high_velocity, p05_dormant_reactivation, p06_round_amounts,
    p07_sudden_spike, p08_high_risk_geo, p09_shared_device, p10_unusual_merchant,
)
from app.services.detection.common import PatternTemplate, RowCache, run_pattern
from app.services.detection.p02_structuring import PATTERN_CODE as P02_CODE, run_p02_structuring

# P02 is the first template and has its own module and run; the others share common.run_pattern.
TEMPLATES: dict[str, PatternTemplate] = {
    module.TEMPLATE.pattern_code: module.TEMPLATE
    for module in (
        p01_large_txn, p03_rapid_movement, p04_high_velocity, p05_dormant_reactivation, p06_round_amounts,
        p07_sudden_spike, p08_high_risk_geo, p09_shared_device, p10_unusual_merchant,
    )
}
RULE_PATTERN_CODES = (P02_CODE, *sorted(TEMPLATES))


def run_pattern_by_code(db: Session, pattern_code: str, *, actor: User | None = None) -> DetectionRunSummary:
    if pattern_code == P02_CODE:
        return run_p02_structuring(db, actor=actor)
    return run_pattern(db, TEMPLATES[pattern_code], actor=actor)


def run_all_rule_patterns(db: Session, *, actor: User | None = None) -> list[DetectionRunSummary]:
    """P02, then every other template. The transactions are read once and shared."""
    cache = RowCache(db)
    summaries = [run_p02_structuring(db, actor=actor)]
    summaries += [run_pattern(db, TEMPLATES[code], actor=actor, cache=cache) for code in sorted(TEMPLATES)]
    return summaries
