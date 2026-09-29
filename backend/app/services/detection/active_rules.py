"""Which rule versions a detection run evaluates (TRD C-06: "rule version aktif")."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import RuleState
from app.models.rule import Rule, RuleVersion


def active_rule_versions(db: Session, pattern_code: str) -> list[tuple[Rule, RuleVersion]]:
    """Every ACTIVE version of every rule for this pattern, in rule order.

    The database guarantees at most one ACTIVE version per rule, so this is one
    row per live rule. Empty when the pattern has no live rule: the run then
    evaluates nothing, and says so.
    """
    rows = db.execute(
        select(Rule, RuleVersion)
        .join(RuleVersion, RuleVersion.rule_id == Rule.id)
        .where(Rule.pattern_code == pattern_code, RuleVersion.state == RuleState.ACTIVE)
        .order_by(Rule.rule_ref)
    ).all()
    return [(rule, version) for rule, version in rows]
