from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
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
from app.schemas.auth import LoginRequest, LogoutRequest, RefreshRequest, TokenResponse, UserOut

router = APIRouter()
settings = get_settings()


def _issue_session(db: Session, user: User, *, absolute_expires_at: datetime | None = None) -> TokenResponse:
    now = datetime.now(timezone.utc)
    refresh_token = generate_refresh_token()
    db.add(
        SessionModel(
            user_id=user.id,
            refresh_token_hash=hash_refresh_token(refresh_token),
            expires_at=absolute_expires_at or now + timedelta(hours=settings.refresh_token_expire_hours),
            last_used_at=now,
        )
    )
    access_token = create_access_token(user_id=user.id, role=user.role.value)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
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
    tokens = _issue_session(db, user)
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    token_hash = hash_refresh_token(payload.refresh_token)
    session = db.query(SessionModel).filter(SessionModel.refresh_token_hash == token_hash).one_or_none()
    now = datetime.now(timezone.utc)

    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")

    idle_cutoff = session.last_used_at + timedelta(minutes=settings.session_idle_timeout_minutes)
    if now > idle_cutoff:
        session.revoked_at = now
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired due to inactivity")

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")

    # Rotate on use: revoke this token and issue a new one, carrying forward
    # the original absolute expiry so refreshing can't extend the 8h cap.
    session.revoked_at = now
    tokens = _issue_session(db, user, absolute_expires_at=session.expires_at)
    db.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> None:
    token_hash = hash_refresh_token(payload.refresh_token)
    session = db.query(SessionModel).filter(SessionModel.refresh_token_hash == token_hash).one_or_none()
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
