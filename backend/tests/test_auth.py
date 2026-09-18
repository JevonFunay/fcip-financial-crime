from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.user import User

TEST_EMAIL = "test-auth-user@example.com"
TEST_PASSWORD = "CorrectHorseBattery9!"


def _make_user(db, role: UserRole = UserRole.ROLE_ANALYST) -> User:
    user = User(
        email=TEST_EMAIL,
        hashed_password=hash_password(TEST_PASSWORD),
        full_name="Test User",
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_login_success_then_me(client, db):
    _make_user(db)

    response = client.post("/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]

    me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me_response.status_code == 200
    assert me_response.json()["email"] == TEST_EMAIL


def test_login_wrong_password_rejected(client, db):
    _make_user(db)

    response = client.post("/auth/login", json={"email": TEST_EMAIL, "password": "wrong-password"})
    assert response.status_code == 401


def test_login_unknown_email_rejected(client):
    response = client.post("/auth/login", json={"email": TEST_EMAIL, "password": "whatever"})
    assert response.status_code == 401


def test_login_locks_after_max_attempts(client, db):
    _make_user(db)

    for _ in range(5):
        response = client.post("/auth/login", json={"email": TEST_EMAIL, "password": "wrong-password"})
        assert response.status_code == 401

    locked_response = client.post("/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert locked_response.status_code == 423


def test_refresh_rotates_and_invalidates_old_token(client, db):
    _make_user(db)

    login_response = client.post("/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    old_refresh = login_response.json()["refresh_token"]

    refresh_response = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert refresh_response.status_code == 200
    assert refresh_response.json()["refresh_token"] != old_refresh

    reuse_response = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse_response.status_code == 401


def test_logout_revokes_refresh_token(client, db):
    _make_user(db)

    login_response = client.post("/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    refresh_token = login_response.json()["refresh_token"]

    logout_response = client.post("/auth/logout", json={"refresh_token": refresh_token})
    assert logout_response.status_code == 204

    reuse_response = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert reuse_response.status_code == 401


def test_me_requires_authentication(client):
    response = client.get("/auth/me")
    assert response.status_code == 401
