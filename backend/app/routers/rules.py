"""Rule Management API (FR-301, FR-302, FR-303; TRD §7.3, §9.5).

Read endpoints (FR-301, FR-302):
  GET /rules                          — list all rules, any authenticated role
  GET /rules/{rule_ref}               — full detail + all versions
  GET /rules/{rule_ref}/versions/{v}  — one version detail

Simulation endpoint (FR-303):
  POST /rules/{rule_ref}/versions/{v}/simulate

All state transitions (submit, approve, reject, suspend, retire — FR-304/305)
and the maker-checker flow require ROLE_MLRO and ROLE_ADMIN, which are not
built yet.  The endpoints are deliberately not included here so they cannot
be called prematurely.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.models.enums import RuleState, UserRole
from app.models.rule import Rule, RuleVersion, SimulationResult
from app.models.user import User
from app.schemas.rule import (
    RuleDetail,
    RuleListResponse,
    RuleSummary,
    RuleVersionDetail,
    SimulateRequest,
    SimulationResultOut,
    SimulationSampleAlert,
)
from app.services.audit import OBJECT_RULE_VERSION, record_transition

router = APIRouter()

ALL_ROLES = tuple(UserRole)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_rule_or_404(db: Session, rule_ref: str) -> Rule:
    rule = db.scalar(select(Rule).where(Rule.rule_ref == rule_ref.upper()))
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Rule '{rule_ref}' not found")
    return rule


def _get_version_or_404(db: Session, rule: Rule, v: int) -> RuleVersion:
    version = db.scalar(
        select(RuleVersion).where(
            RuleVersion.rule_id == rule.id,
            RuleVersion.version == v,
        )
    )
    if version is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Rule '{rule.rule_ref}' version {v} not found",
        )
    return version


def _version_detail(v: RuleVersion) -> RuleVersionDetail:
    return RuleVersionDetail(
        id=v.id,
        version=v.version,
        parent_version=v.parent_version,
        state=v.state,
        template_version=v.template_version,
        parameters=v.parameters,
        window_type=v.window_type,
        window_length=v.window_length,
        entity_scope=v.entity_scope,
        severity=v.severity,
        reason_code=v.reason_code,
        description=v.description,
        changed_by=v.changed_by,
        changed_at=v.changed_at,
        change_summary=v.change_summary,
    )


# ---------------------------------------------------------------------------
# Read endpoints (FR-301, FR-302)
# ---------------------------------------------------------------------------


@router.get("", response_model=RuleListResponse, dependencies=[Depends(require_role(*ALL_ROLES))])
def list_rules(
    pattern_code: str | None = Query(None, description="Filter by pattern code, e.g. P02_STRUCTURING"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> RuleListResponse:
    """FR-301 AC1: list all rules, optionally filtered by pattern.

    Ordered by rule_ref so the response is stable and predictable.
    """
    base = select(Rule)
    if pattern_code:
        base = base.where(Rule.pattern_code == pattern_code.upper())
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rules = db.scalars(base.order_by(Rule.rule_ref).limit(limit).offset(offset)).all()
    return RuleListResponse(
        items=[RuleSummary(id=r.id, rule_ref=r.rule_ref, pattern_code=r.pattern_code, created_at=r.created_at) for r in rules],
        total=total,
    )


@router.get("/{rule_ref}", response_model=RuleDetail, dependencies=[Depends(require_role(*ALL_ROLES))])
def get_rule(rule_ref: str, db: Session = Depends(get_db)) -> RuleDetail:
    """FR-301 AC3: full rule detail including every version, newest first.

    The version's description is shown verbatim (BR-301.2) so an investigator
    reading an alert always sees exactly why the rule exists, as written.
    """
    rule = _get_rule_or_404(db, rule_ref)
    versions = db.scalars(
        select(RuleVersion)
        .where(RuleVersion.rule_id == rule.id)
        .order_by(RuleVersion.version.desc())
    ).all()
    return RuleDetail(
        id=rule.id,
        rule_ref=rule.rule_ref,
        pattern_code=rule.pattern_code,
        correlation_id=rule.correlation_id,
        created_at=rule.created_at,
        versions=[_version_detail(v) for v in versions],
    )


@router.get(
    "/{rule_ref}/versions/{v}",
    response_model=RuleVersionDetail,
    dependencies=[Depends(require_role(*ALL_ROLES))],
)
def get_rule_version(rule_ref: str, v: int, db: Session = Depends(get_db)) -> RuleVersionDetail:
    """FR-302 AC2: get one specific version by its (rule_ref, version) natural key.

    The version's content is immutable once saved; only state can change.
    """
    rule = _get_rule_or_404(db, rule_ref)
    version = _get_version_or_404(db, rule, v)
    return _version_detail(version)


# ---------------------------------------------------------------------------
# Simulation endpoint (FR-303)
# ---------------------------------------------------------------------------


@router.post(
    "/{rule_ref}/versions/{v}/simulate",
    response_model=SimulationResultOut,
    status_code=status.HTTP_201_CREATED,
)
def simulate_rule_version(
    rule_ref: str,
    v: int,
    body: SimulateRequest,
    user: User = Depends(require_role(UserRole.ROLE_DATA_OPS, UserRole.ROLE_ANALYST)),
    db: Session = Depends(get_db),
) -> SimulationResultOut:
    """FR-303 / TRD §9.5: dry-run the rule version on historical data.

    Runs the pattern's evaluate() with these parameters — the same function a
    live detection run uses — but writes nothing to the alert table.  The
    result is stored in simulation_result (insert-only) and returned.

    Only ROLE_DATA_OPS and ROLE_ANALYST can trigger a simulation; the
    maker-checker approval flow (FR-304, not built yet) will gate ACTIVE use.

    Supported patterns: P02_STRUCTURING.  Other patterns will get their own
    evaluate() once their detectors are built; the endpoint shape is the same.
    """
    rule = _get_rule_or_404(db, rule_ref)
    version = _get_version_or_404(db, rule, v)

    # Only DRAFT and IN_SIMULATION versions can be simulated.  An ACTIVE,
    # SUSPENDED or RETIRED version should be run through the normal detection
    # path, not re-simulated, to avoid confusion.
    if version.state not in (RuleState.DRAFT, RuleState.IN_SIMULATION):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Version {v} is in state '{version.state.value}'; only DRAFT or IN_SIMULATION versions can be simulated",
        )

    # Import pattern templates lazily so adding a new pattern doesn't touch this
    # file — each pattern registers its own evaluate() callable.
    from app.services.detection.p02_structuring import (  # noqa: PLC0415
        P02Parameters,
        evaluate as p02_evaluate,
    )

    _SUPPORTED_PATTERNS = {"P02_STRUCTURING": (P02Parameters.from_rule_version, p02_evaluate)}

    if rule.pattern_code not in _SUPPORTED_PATTERNS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Pattern '{rule.pattern_code}' does not have a simulation-capable template yet",
        )

    params_factory, evaluate_fn = _SUPPORTED_PATTERNS[rule.pattern_code]
    try:
        params = params_factory(version)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    # Determine the period: default to all data in the DB.
    if body.period_start is not None and body.period_end is not None:
        if body.period_start >= body.period_end:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "period_start must be before period_end",
            )
        period: tuple[datetime, datetime] | None = (body.period_start, body.period_end)
    else:
        period = None

    evaluation = evaluate_fn(db, params, period=period)

    # Build the sample list: at most 10 candidates for the response.
    sample: list[SimulationSampleAlert] = [
        SimulationSampleAlert(
            customer_ref=c.customer_ref,
            window_start=c.cluster.window_start.astimezone(timezone.utc),
            window_end=c.cluster.window_end.astimezone(timezone.utc),
            txn_count=len(c.cluster.transactions),
            aggregate_amount=str(sum(t.amount for t in c.cluster.transactions)),
        )
        for c in evaluation.candidates[:10]
    ]

    # Daily distribution: how many would-be alerts per date.
    daily: dict[str, int] = {}
    for c in evaluation.candidates:
        day = c.cluster.window_start.astimezone(timezone.utc).date().isoformat()
        daily[day] = daily.get(day, 0) + 1

    # Determine the actual evaluated period (None → whole DB).
    from sqlalchemy import func as sqlfunc  # noqa: PLC0415
    from app.models.transaction import Transaction  # noqa: PLC0415

    if period is not None:
        actual_start, actual_end = period
    else:
        row = db.execute(
            select(sqlfunc.min(Transaction.transaction_date), sqlfunc.max(Transaction.transaction_date))
        ).one()
        actual_start = row[0] or datetime.now(timezone.utc)
        actual_end = row[1] or datetime.now(timezone.utc)

    alerted_entities = len(set(c.customer_id for c in evaluation.candidates))
    result = SimulationResult(
        rule_version_id=version.id,
        period_start=actual_start,
        period_end=actual_end,
        transactions_scanned=evaluation.transactions_scanned,
        would_be_alert_count=len(evaluation.candidates),
        unique_entity_count=alerted_entities,
        daily_distribution=daily,
        overlap_by_rule=[],  # cross-rule overlap analysis is FR-303 stage 2
        sample_alerts=[s.model_dump(mode="json") for s in sample],
        run_by=user.id,
    )
    db.add(result)

    # Advance the version's state to IN_SIMULATION if still DRAFT, so the rule
    # audit trail reflects that a simulation was run (FR-303).
    if version.state == RuleState.DRAFT:
        prev_state = version.state.value
        version.state = RuleState.IN_SIMULATION
        record_transition(
            db,
            correlation_id=rule.correlation_id,
            actor=user,
            object_type=OBJECT_RULE_VERSION,
            object_id=version.id,
            from_state=prev_state,
            to_state=RuleState.IN_SIMULATION.value,
            reason=f"Simulation triggered by {user.email}",
        )

    db.commit()
    db.refresh(result)

    return SimulationResultOut(
        id=result.id,
        rule_ref=rule.rule_ref,
        version=version.version,
        period_start=result.period_start,
        period_end=result.period_end,
        transactions_scanned=result.transactions_scanned,
        would_be_alert_count=result.would_be_alert_count,
        unique_entity_count=result.unique_entity_count,
        daily_distribution=result.daily_distribution,
        sample_alerts=sample,
        run_at=result.run_at,
    )
