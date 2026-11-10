"""Detection entry points use the same rule runner and respect model lifecycle.

The orchestration checks replace only the expensive detection calls; ingestion
and mapping still pass through the real API/database. Stored-score checks use
persisted historical scores without fitting a model or changing those scores.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.ml.lab.features import FEATURE_GROUPS
from app.models.alert import Alert
from app.models.enums import RuleState, UserRole
from app.models.ml import MlScore, ModelVersion
from app.models.rule import Rule, RuleVersion
from app.scripts import run_detection
from app.services import ml_scoring
from app.services.detection import registry

from lab_helpers import make_dataset
from pattern_world import World


def _summary(pattern, count):
    return SimpleNamespace(pattern_code=pattern, detection_run_id=uuid.uuid4(),
                           rules_evaluated=[], alerts_created=count,
                           alerts_already_existing=0, alerts=[])


def _no_model_run():
    return SimpleNamespace(detection_run_id=uuid.uuid4(), models=[],
                           ml_rule_versions_evaluated=[], alerts_created=0,
                           alerts_already_existing=0, seconds=0.0)


@pytest.mark.parametrize("rules_only", [False, True])
def test_cli_uses_shared_all_pattern_runner_before_models(monkeypatch, capsys, rules_only):
    events = []
    db = SimpleNamespace(close=lambda: events.append("closed"))
    monkeypatch.setattr(run_detection, "SessionLocal", lambda: db)

    def rules(session, **kwargs):
        assert session is db
        events.append("all_rules")
        return [_summary("P02_STRUCTURING", 2), _summary("P09_SHARED_DEVICE", 4)]

    def models(session, **kwargs):
        assert session is db
        events.append("models")
        return _no_model_run()

    monkeypatch.setattr(run_detection, "run_all_rule_patterns", rules)
    monkeypatch.setattr(run_detection, "run_ml_scoring", models)
    run_detection.run(rules_only=rules_only)
    assert events == (["all_rules", "closed"] if rules_only else ["all_rules", "models", "closed"])
    output = capsys.readouterr().out
    assert "P02_STRUCTURING" in output and "P09_SHARED_DEVICE" in output


def test_lab_monitoring_uses_all_pattern_summaries_not_only_p02(client, auth_headers, monkeypatch):
    analyst = auth_headers(UserRole.ROLE_ANALYST)
    data_ops = auth_headers(UserRole.ROLE_DATA_OPS)
    uploaded = client.post("/lab/datasets", headers=analyst,
                           files={"file": ("study.csv", make_dataset(1500, seed=804), "text/csv")})
    assert uploaded.status_code == 201, uploaded.text
    dataset = uploaded.json()
    suggestion = dataset["suggestion"]
    mapped = client.post(f"/lab/datasets/{dataset['id']}/mapping", headers=analyst,
                         json={key: suggestion[key] for key in
                               ("fields", "label_column", "positive_values", "category_column", "date_format")})
    assert mapped.status_code == 200, mapped.text
    trained = client.post(f"/lab/datasets/{dataset['id']}/runs", headers=analyst,
                          json={"feature_groups": [g["key"] for g in FEATURE_GROUPS],
                                "models": ["logistic_regression"], "train_share": 0.6})
    assert trained.status_code == 201, trained.text
    candidate = trained.json()["results"]["models"][0]
    saved = client.post(f"/lab/runs/{trained.json()['id']}/save", headers=analyst,
                        json={"model_key": candidate["key"], "threshold_index": candidate["default_index"]})
    assert saved.status_code == 200, saved.text

    events = []

    def rules(db, *, actor):
        events.append("all_rules")
        assert actor.role is UserRole.ROLE_DATA_OPS
        return [_summary("P02_STRUCTURING", 2), _summary("P09_SHARED_DEVICE", 4)]

    def models(db, *, actor):
        events.append("models")
        return _no_model_run()

    monkeypatch.setattr(registry, "run_all_rule_patterns", rules)
    monkeypatch.setattr(ml_scoring, "run_ml_scoring", models)
    response = client.post(f"/lab/datasets/{dataset['id']}/monitor", headers=data_ops,
                           files={"file": ("monitor.csv", make_dataset(500, seed=805, defects=False), "text/csv")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert events == ["all_rules", "models"]
    assert body["rule_alerts_created"] == 6
    assert body["accepted"] + body["quarantined"] == 500


@pytest.mark.parametrize("state", [RuleState.ACTIVE, RuleState.SUSPENDED, RuleState.RETIRED])
def test_only_active_model_versions_can_turn_historical_flags_into_new_alerts(db, state):
    world = World(db)
    customer = world.customer("HISTORICAL-SCORE")
    account = world.account(customer)
    instant = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    transaction = world.tx(account, instant, 1000)
    world.done()
    version = ModelVersion(
        artefact_type="LAB_LOGISTIC_JSON", version=f"historical-{state.value.lower()}",
        definition_hash="a" * 64, state=state, unit="FRAUD", feature_set_version="behavioral_v1",
        threshold=0.5, model_text="unused: no fit or predictor load in this alert-only test",
        model_card={"feature_set": {"definitions": {"AMOUNT": "Observed amount"}}},
        correlation_id=uuid.uuid4(),
    )
    db.add(version)
    db.flush()
    score = MlScore(model_version_id=version.id, unit="FRAUD", customer_id=customer.id,
                    transaction_id=transaction.id, as_of=instant, score=0.9, threshold=0.5,
                    flagged=True, contributions=[{"feature": "AMOUNT", "value": 1000,
                                                 "contribution": 0.1}], detection_run_id=uuid.uuid4())
    db.add(score)
    rule = db.scalar(select(RuleVersion).join(Rule).where(Rule.pattern_code == "ML_SCORE"))
    rule.state = RuleState.ACTIVE
    db.commit()

    _, created, existing = ml_scoring.raise_ml_alerts(db, uuid.uuid4(), None)
    db.flush()
    expected = int(state is RuleState.ACTIVE)
    assert created == expected and existing == 0
    assert db.scalar(select(func.count()).select_from(Alert).where(Alert.pattern_code == "ML_SCORE")) == expected
    assert db.scalar(select(func.count()).select_from(MlScore)) == 1  # history remains immutable

    _, created_again, existing_again = ml_scoring.raise_ml_alerts(db, uuid.uuid4(), None)
    assert created_again == 0 and existing_again == expected
