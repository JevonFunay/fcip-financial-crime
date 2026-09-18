from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.rbac import get_current_user
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    verify_password,
)
from app.database import get_db
from app.models.session import Session as SessionModel
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut

router = APIRouter()
settings = get_settings()

REFRESH_COOKIE = "fcip_refresh_token"
# Scoped to /auth so the browser never attaches the refresh token to ordinary API calls.
REFRESH_COOKIE_PATH = "/auth"


def _set_refresh_cookie(response: Response, token: str, expires_at: datetime) -> None:
    max_age = max(int((expires_at - datetime.now(timezone.utc)).total_seconds()), 0)
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=max_age,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def _refresh_rejected(detail: str) -> HTTPException:
    # Headers set on the injected Response are dropped when an exception is
    # raised, so the cookie-clearing header has to travel with the exception.
    scratch = Response()
    _clear_refresh_cookie(scratch)
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"set-cookie": scratch.headers["set-cookie"]})


def _issue_session(
    db: Session, user: User, response: Response, *, absolute_expires_at: datetime | None = None
) -> TokenResponse:
    now = datetime.now(timezone.utc)
    expires_at = absolute_expires_at or now + timedelta(hours=settings.refresh_token_expire_hours)
    refresh_token = generate_refresh_token()
    db.add(
        SessionModel(
            user_id=user.id,
            refresh_token_hash=hash_refresh_token(refresh_token),
            expires_at=expires_at,
            last_used_at=now,
        )
    )
    _set_refresh_cookie(response, refresh_token, expires_at)
    return TokenResponse(access_token=create_access_token(user_id=user.id, role=user.role.value))


def _session_from_cookie(request: Request, db: Session) -> SessionModel | None:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        return None
    return db.query(SessionModel).filter(SessionModel.refresh_token_hash == hash_refresh_token(raw)).one_or_none()


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.email == payload.email).one_or_none()
    now = datetime.now(timezone.utc)

    if user is not None and user.locked_until is not None and user.locked_until > now:
        raise HTTPException(status.HTTP_423_LOCKED, "Account locked, try again later")

    if user is None or not user.is_active or not verify_password(payload.password, user.hashed_password):
        # TRD §12.2 lockout: 5 consecutive failed attempts -> 15 minute lock.
        if user is not None and user.is_active:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.login_lockout_max_attempts:
                user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_login_count = 0
            db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    user.failed_login_count = 0
    user.locked_until = None
    tokens = _issue_session(db, user, response)
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> TokenResponse:
    session = _session_from_cookie(request, db)
    now = datetime.now(timezone.utc)

    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise _refresh_rejected("Invalid or expired refresh token")

    idle_cutoff = session.last_used_at + timedelta(minutes=settings.session_idle_timeout_minutes)
    if now > idle_cutoff:
        session.revoked_at = now
        db.commit()
        raise _refresh_rejected("Session expired due to inactivity")

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise _refresh_rejected("Invalid or expired refresh token")

    # Rotate on use: revoke this token and issue a new one, carrying forward
    # the original absolute expiry so refreshing can't extend the 8h cap.
    session.revoked_at = now
    tokens = _issue_session(db, user, response, absolute_expires_at=session.expires_at)
    db.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, db: Session = Depends(get_db)) -> Response:
    session = _session_from_cookie(request, db)
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response)
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
