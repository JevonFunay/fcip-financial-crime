from datetime import datetime, timedelta, timezone

from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.session import Session as SessionModel
from app.models.user import User
from app.routers.auth import REFRESH_COOKIE, REFRESH_COOKIE_PATH

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


def _login(client):
    return client.post("/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})


def _refresh_cookie_header(response) -> str:
    headers = [h for h in response.headers.get_list("set-cookie") if h.startswith(f"{REFRESH_COOKIE}=")]
    assert len(headers) == 1, headers
    return headers[0]


# --- login ---------------------------------------------------------------------------------


def test_login_returns_access_token_in_body_and_refresh_token_in_cookie(client, db):
    _make_user(db)

    response = _login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert "refresh_token" not in body

    cookie_value = client.cookies.get(REFRESH_COOKIE)
    assert cookie_value
    assert cookie_value not in response.text


def test_refresh_cookie_has_httponly_secure_samesite_strict_and_auth_path(client, db):
    _make_user(db)

    header = _refresh_cookie_header(_login(client)).lower()

    assert "httponly" in header
    assert "secure" in header
    assert "samesite=strict" in header
    assert f"path={REFRESH_COOKIE_PATH}".lower() in header
    assert "max-age=" in header


def test_refresh_cookie_max_age_matches_absolute_session_expiry(client, db):
    _make_user(db)

    header = _refresh_cookie_header(_login(client))
    max_age = int(next(part.split("=")[1] for part in header.split("; ") if part.startswith("Max-Age=")))

    assert 8 * 3600 - 5 <= max_age <= 8 * 3600


def test_login_then_me(client, db):
    _make_user(db)
    access_token = _login(client).json()["access_token"]

    me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})

    assert me_response.status_code == 200
    assert me_response.json()["email"] == TEST_EMAIL


def test_login_wrong_password_rejected_and_sets_no_cookie(client, db):
    _make_user(db)

    response = client.post("/auth/login", json={"email": TEST_EMAIL, "password": "wrong-password"})

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert client.cookies.get(REFRESH_COOKIE) is None


def test_login_unknown_email_rejected(client):
    response = client.post("/auth/login", json={"email": TEST_EMAIL, "password": "whatever"})
    assert response.status_code == 401


def test_login_locks_after_max_attempts(client, db):
    _make_user(db)

    for _ in range(5):
        response = client.post("/auth/login", json={"email": TEST_EMAIL, "password": "wrong-password"})
        assert response.status_code == 401

    locked_response = _login(client)
    assert locked_response.status_code == 423


# --- refresh -------------------------------------------------------------------------------


def test_refresh_uses_cookie_and_rotates_it(client, db):
    _make_user(db)
    _login(client)
    old_cookie = client.cookies.get(REFRESH_COOKIE)

    response = client.post("/auth/refresh")

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert "refresh_token" not in response.json()
    new_cookie = client.cookies.get(REFRESH_COOKIE)
    assert new_cookie and new_cookie != old_cookie
    assert "httponly" in _refresh_cookie_header(response).lower()


def test_rotated_refresh_token_cannot_be_reused(client, db):
    _make_user(db)
    _login(client)
    old_cookie = client.cookies.get(REFRESH_COOKIE)
    client.post("/auth/refresh")

    reuse = client.post("/auth/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={old_cookie}"})

    assert reuse.status_code == 401


def test_refresh_without_cookie_is_rejected(client, db):
    _make_user(db)

    response = client.post("/auth/refresh")

    assert response.status_code == 401


def test_refresh_ignores_token_sent_in_body(client, db):
    """The refresh token is only accepted from the cookie, never from a JSON body."""
    _make_user(db)
    _login(client)
    cookie = client.cookies.get(REFRESH_COOKIE)
    client.cookies.clear()

    response = client.post("/auth/refresh", json={"refresh_token": cookie})

    assert response.status_code == 401


def test_refresh_rejects_garbage_cookie_and_clears_it(client, db):
    _make_user(db)

    response = client.post("/auth/refresh", headers={"Cookie": f"{REFRESH_COOKIE}=garbage"})

    assert response.status_code == 401
    header = _refresh_cookie_header(response).lower()
    assert "max-age=0" in header


def test_refresh_after_idle_timeout_is_rejected(client, db):
    _make_user(db)
    _login(client)
    session = db.query(SessionModel).one()
    session.last_used_at = datetime.now(timezone.utc) - timedelta(minutes=31)
    db.commit()

    response = client.post("/auth/refresh")

    assert response.status_code == 401
    db.refresh(session)
    assert session.revoked_at is not None


def test_refresh_after_absolute_timeout_is_rejected(client, db):
    _make_user(db)
    _login(client)
    session = db.query(SessionModel).one()
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    assert client.post("/auth/refresh").status_code == 401


def test_refresh_keeps_absolute_expiry_of_original_login(client, db):
    _make_user(db)
    _login(client)
    original = db.query(SessionModel).one().expires_at

    client.post("/auth/refresh")

    sessions = db.query(SessionModel).order_by(SessionModel.created_at).all()
    assert len(sessions) == 2
    assert sessions[0].revoked_at is not None
    assert sessions[1].revoked_at is None
    assert sessions[1].expires_at == original


# --- logout --------------------------------------------------------------------------------


def test_logout_revokes_session_and_clears_cookie(client, db):
    _make_user(db)
    _login(client)
    cookie = client.cookies.get(REFRESH_COOKIE)

    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert "max-age=0" in _refresh_cookie_header(response).lower()
    assert client.cookies.get(REFRESH_COOKIE) is None
    assert db.query(SessionModel).one().revoked_at is not None

    reuse = client.post("/auth/refresh", headers={"Cookie": f"{REFRESH_COOKIE}={cookie}"})
    assert reuse.status_code == 401


def test_logout_without_cookie_is_still_a_clean_204(client):
    response = client.post("/auth/logout")

    assert response.status_code == 204


# --- me ------------------------------------------------------------------------------------


def test_me_requires_authentication(client):
    response = client.get("/auth/me")
    assert response.status_code == 401
