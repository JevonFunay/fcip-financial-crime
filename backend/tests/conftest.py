import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import Settings, get_settings

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _point_app_at_test_database() -> None:
    # Must run before app.database is imported, so the app engine binds to the test DB.
    url = make_url(Settings().database_url)
    if not url.database.endswith("_test"):
        url = url.set(database=f"{url.database}_test")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()


_point_app_at_test_database()

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.core.security import create_access_token  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.alert import Alert  # noqa: E402
from app.models.customer import Customer  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.user import User  # noqa: E402
from app.scripts.seed import seed_master_data  # noqa: E402
from app.services.detection.active_rules import active_rule_versions  # noqa: E402
from app.services.detection.p02_structuring import PATTERN_CODE, P02Parameters, run_p02_structuring  # noqa: E402
from app.services.ingestion import ingest_transactions_csv  # noqa: E402

SAMPLE_FILE = BACKEND_DIR / "sample_data" / "transactions_sample.csv"

# Reference data that a migration seeds and the app needs in order to work: the
# ACTIVE P02 rule (migration 0006). The per-test TRUNCATE wipes it with
# everything else, so it is copied aside once, right after the migrations run,
# and put back after every clean. Parents first. The audit event the seed
# wrote is deliberately not restored, so a test counting audit events sees
# only its own.
SEEDED_REFERENCE_TABLES = ("rule", "rule_version")
_SEED_SNAPSHOT_SCHEMA = "test_seed_snapshot"


def _alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> None:
    assert engine.url.database.endswith("_test"), "refusing to run tests against a non-test database"

    # Rebuilt from the migrations on every run, so the seeded reference data is
    # exactly what the migrations produce and an edited migration can never
    # leave a stale test schema behind.
    admin_engine = create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{engine.url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{engine.url.database}"'))
    admin_engine.dispose()
    engine.dispose()

    command.upgrade(_alembic_config(), "head")

    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {_SEED_SNAPSHOT_SCHEMA}"))
        for table in SEEDED_REFERENCE_TABLES:
            conn.execute(text(f'CREATE TABLE {_SEED_SNAPSHOT_SCHEMA}."{table}" AS TABLE public."{table}"'))


@pytest.fixture()
def alembic_config() -> Config:
    return _alembic_config()


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    assert engine.url.database.endswith("_test"), "refusing to truncate a non-test database"
    table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
        for table in SEEDED_REFERENCE_TABLES:
            conn.execute(text(f'INSERT INTO public."{table}" SELECT * FROM {_SEED_SNAPSHOT_SCHEMA}."{table}"'))


@pytest.fixture()
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client() -> TestClient:
    # https so the client's cookie jar sends back the Secure refresh cookie, like a real browser would.
    return TestClient(app, base_url="https://testserver")


@pytest.fixture()
def auth_headers(db: Session) -> Callable[[UserRole], dict[str, str]]:
    def make(role: UserRole) -> dict[str, str]:
        user = User(
            email=f"{role.value.lower()}-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="not-a-real-hash",
            full_name=f"Test {role.value}",
            role=role,
        )
        db.add(user)
        db.commit()
        token = create_access_token(user_id=user.id, role=role.value)
        return {"Authorization": f"Bearer {token}"}

    return make


@pytest.fixture()
def p02_params(db: Session) -> P02Parameters:
    """The parameters of the ACTIVE P02 rule version in the database, i.e. the
    migration-seeded RUL-0001 v1. They used to be Python constants."""
    [(_, version)] = active_rule_versions(db, PATTERN_CODE)
    return P02Parameters.from_rule_version(version)


@pytest.fixture()
def sample_alerts(db: Session) -> dict[str, Alert]:
    """Seeds master data, ingests the sample CSV and runs P02: yields the 2 resulting alerts keyed by customer_ref."""
    seed_master_data(db)
    ingest_transactions_csv(db, file_name="transactions_sample.csv", content=SAMPLE_FILE.read_text())
    run_p02_structuring(db)
    rows = db.execute(select(Alert, Customer.customer_ref).join(Customer, Alert.customer_id == Customer.id)).all()
    alerts = {customer_ref: alert for alert, customer_ref in rows}
    assert set(alerts) == {"CUST-001", "CUST-006"}
    return alerts
