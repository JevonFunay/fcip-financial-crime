"""User-authorised resets preserve governance and refuse unreviewed data edits."""

import json
import sys

import pytest
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.enums import EntityType
from app.scripts import reset_demo_datasets as reset


def test_reset_refuses_same_count_content_edit_then_preserves_governance(db):
    name = str(db.bind.url.database)
    customer = Customer(customer_ref="RESET-CONTRACT", full_name="Before", entity_type=EntityType.INDIVIDUAL)
    db.add(customer)
    db.commit()
    reviewed = reset.reset_database(name)
    customer.full_name = "Changed after review"
    db.commit()
    with pytest.raises(ValueError, match="changed after"):
        reset.reset_database(name, expected=reviewed)
    assert db.scalar(select(func.count()).select_from(Customer)) == 1
    db.rollback()  # Release the read transaction before the reset's table lock.
    current = reset.reset_database(name)
    result = reset.reset_database(name, expected=current)
    assert not any(result["after"]["dataset_rows"].values())
    assert result["before"]["preserved"] == result["after"]["preserved"]
    assert result["audit_event_appended"] is True
    assert db.scalar(select(func.count()).select_from(Customer)) == 0
    assert db.scalar(select(AuditLog).where(AuditLog.object_type == "DATASET_RESET")) is not None


def test_reset_report_collision_refuses_before_database_mutation(tmp_path, monkeypatch):
    plan = tmp_path / "reset.plan.json"
    plan.write_text(json.dumps({"databases": ["fcip_test"], "snapshots": {"fcip_test": {}}}))
    plan.with_name("reset.plan.executed.json").write_text("existing evidence")
    monkeypatch.setattr(sys, "argv", ["reset", "--database", "fcip_test", "--plan", str(plan), "--execute"])
    def forbidden(*args, **kwargs):
        pytest.fail("A colliding report must be refused before any database access")
    monkeypatch.setattr(reset, "reset_database", forbidden)
    with pytest.raises(SystemExit, match="overwrite a reset report"):
        reset.main()


def test_reset_refuses_unrelated_database():
    with pytest.raises(ValueError, match="Only named FCIP"):
        reset.reset_database("unrelated_production")
