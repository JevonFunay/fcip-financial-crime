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
from app.services.detection.p02_structuring import run_p02_structuring  # noqa: E402
from app.services.ingestion import ingest_transactions_csv  # noqa: E402

SAMPLE_FILE = BACKEND_DIR / "sample_data" / "transactions_sample.csv"


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> None:
    assert engine.url.database.endswith("_test"), "refusing to run tests against a non-test database"

    admin_engine = create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": engine.url.database})
        if exists.scalar() is None:
            conn.execute(text(f'CREATE DATABASE "{engine.url.database}"'))
    admin_engine.dispose()

    alembic_config = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(alembic_config, "head")


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    assert engine.url.database.endswith("_test"), "refusing to truncate a non-test database"
    table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


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
    return TestClient(app)


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
def sample_alerts(db: Session) -> dict[str, Alert]:
    """Seeds master data, ingests the sample CSV and runs P02: yields the 2 resulting alerts keyed by customer_ref."""
    seed_master_data(db)
    ingest_transactions_csv(db, file_name="transactions_sample.csv", content=SAMPLE_FILE.read_text())
    run_p02_structuring(db)
    rows = db.execute(select(Alert, Customer.customer_ref).join(Customer, Alert.customer_id == Customer.id)).all()
    alerts = {customer_ref: alert for alert, customer_ref in rows}
    assert set(alerts) == {"CUST-001", "CUST-006"}
    return alerts
