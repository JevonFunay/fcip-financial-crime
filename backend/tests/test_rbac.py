import pytest
from fastapi import HTTPException

from app.core.rbac import require_role
from app.models.enums import UserRole
from app.models.user import User


def test_require_role_allows_matching_role():
    dependency = require_role(UserRole.ROLE_TRIAGE, UserRole.ROLE_INVESTIGATOR)
    user = User(role=UserRole.ROLE_TRIAGE)

    assert dependency(user) is user


def test_require_role_denies_non_matching_role():
    dependency = require_role(UserRole.ROLE_TRIAGE)
    user = User(role=UserRole.ROLE_ANALYST)

    with pytest.raises(HTTPException) as exc_info:
        dependency(user)
    assert exc_info.value.status_code == 403
