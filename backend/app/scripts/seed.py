"""Creates one dev/test user per role. Idempotent — skips emails that already exist.

Run via: docker compose exec backend python -m app.scripts.seed
"""

from app.core.security import hash_password
from app.database import SessionLocal
from app.models.enums import UserRole
from app.models.user import User

DEV_PASSWORD = "DevPassword123!"

SEED_USERS = [
    ("dataops@fcip.internal", "DataOps User", UserRole.ROLE_DATA_OPS),
    ("analyst@fcip.internal", "Analyst User", UserRole.ROLE_ANALYST),
    ("triage@fcip.internal", "Triage User", UserRole.ROLE_TRIAGE),
    ("investigator@fcip.internal", "Investigator User", UserRole.ROLE_INVESTIGATOR),
]


def run() -> None:
    db = SessionLocal()
    try:
        for email, full_name, role in SEED_USERS:
            if db.query(User).filter(User.email == email).one_or_none() is not None:
                print(f"skip (already exists): {email}")
                continue
            db.add(
                User(
                    email=email,
                    hashed_password=hash_password(DEV_PASSWORD),
                    full_name=full_name,
                    role=role,
                )
            )
            print(f"created: {email} ({role.value}) password={DEV_PASSWORD}")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    run()
