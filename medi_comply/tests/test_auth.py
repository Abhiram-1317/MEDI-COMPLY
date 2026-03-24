import time
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException, status

from medi_comply.api.auth import (
    API_KEY_PREFIX,
    AuthManager,
    CurrentUser,
    LoginRequest,
    ROLE_PERMISSIONS,
    UserRole,
    get_auth_manager,
    require_permission,
    require_role,
)


@pytest.fixture
def auth():
    return AuthManager()


class TestUserManagement:
    def test_default_users_created(self, auth: AuthManager):
        assert len(auth.list_users()) == 5

    def test_create_user(self, auth: AuthManager):
        user = auth.create_user("new_user", "Password1!", UserRole.CODER)
        assert user.username == "new_user"
        assert auth.get_user("new_user") is not None

    def test_create_duplicate_user(self, auth: AuthManager):
        auth.create_user("dupe", "Password1!", UserRole.CODER)
        with pytest.raises(ValueError):
            auth.create_user("dupe", "Password1!", UserRole.CODER)

    def test_get_user(self, auth: AuthManager):
        user = auth.get_user("admin")
        assert user is not None
        assert user.username == "admin"

    def test_get_user_by_id(self, auth: AuthManager):
        user = auth.get_user("admin")
        found = auth.get_user_by_id(user.user_id) if user else None
        assert found is not None
        assert found.user_id == user.user_id

    def test_delete_user(self, auth: AuthManager):
        auth.create_user("temp", "Password1!", UserRole.CODER)
        assert auth.delete_user("temp") is True
        deleted = auth.get_user("temp")
        assert deleted is not None
        assert deleted.is_active is False

    def test_list_users(self, auth: AuthManager):
        users = auth.list_users()
        assert users
        assert all("password_hash" not in u for u in users)

    def test_change_password(self, auth: AuthManager):
        auth.create_user("changer", "Password1!", UserRole.CODER)
        assert auth.change_password("changer", "Password1!", "NewPass2!") is True
        user = auth.get_user("changer")
        assert user is not None
        assert auth._verify_password("NewPass2!", user.password_hash)


class TestPasswordHashing:
    def test_hash_password(self, auth: AuthManager):
        hashed = auth._hash_password("Password1!")
        assert "Password1!" not in hashed

    def test_verify_correct_password(self, auth: AuthManager):
        hashed = auth._hash_password("Password1!")
        assert auth._verify_password("Password1!", hashed)

    def test_verify_wrong_password(self, auth: AuthManager):
        hashed = auth._hash_password("Password1!")
        assert not auth._verify_password("WrongPass", hashed)

    def test_hash_contains_salt(self, auth: AuthManager):
        hashed = auth._hash_password("Password1!")
        assert ":" in hashed


class TestLogin:
    def test_login_success(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        assert res.success is True
        assert res.token is not None

    def test_login_wrong_password(self, auth: AuthManager):
        res = auth.login("admin", "badpass")
        assert res.success is False

    def test_login_unknown_user(self, auth: AuthManager):
        res = auth.login("nope", "whatever")
        assert res.success is False

    def test_login_returns_access_token(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        assert res.token.access_token

    def test_login_returns_refresh_token(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        assert res.token.refresh_token

    def test_login_returns_role(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        assert res.role == UserRole.ADMIN.value

    def test_login_updates_last_login(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        user = auth.get_user("admin")
        assert user.last_login is not None
        assert res.success

    def test_login_inactive_user(self, auth: AuthManager):
        auth.delete_user("admin")
        res = auth.login("admin", "admin123!")
        assert res.success is False


class TestAccountLockout:
    def test_lockout_after_max_attempts(self, auth: AuthManager):
        for _ in range(auth._max_failed_attempts):
            auth.login("admin", "badpass")
        res = auth.login("admin", "badpass")
        assert res.success is False
        user = auth.get_user("admin")
        assert user.is_locked

    def test_lockout_message(self, auth: AuthManager):
        for _ in range(auth._max_failed_attempts + 1):
            res = auth.login("admin", "badpass")
        assert res.message == "Account locked"

    def test_lockout_expires(self, auth: AuthManager):
        for _ in range(auth._max_failed_attempts):
            auth.login("admin", "badpass")
        user = auth.get_user("admin")
        user.locked_until = time.time() - 1
        user.is_locked = True
        res = auth.login("admin", "admin123!")
        assert res.success is True

    def test_successful_login_resets_attempts(self, auth: AuthManager):
        auth.login("admin", "badpass")
        auth.login("admin", "badpass")
        res = auth.login("admin", "admin123!")
        user = auth.get_user("admin")
        assert user.failed_attempts == 0
        assert res.success

    def test_locked_account_correct_password(self, auth: AuthManager):
        for _ in range(auth._max_failed_attempts):
            auth.login("admin", "badpass")
        user = auth.get_user("admin")
        user.locked_until = time.time() + 60
        res = auth.login("admin", "admin123!")
        assert res.success is False
        assert res.message == "Account locked"


class TestTokens:
    def test_access_token_verifies(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        payload = auth.verify_token(res.token.access_token)
        assert payload is not None

    def test_expired_token_rejected(self, auth: AuthManager):
        auth._token_expiry_minutes = 0
        res = auth.login("admin", "admin123!")
        payload = auth.verify_token(res.token.access_token)
        assert payload is None

    def test_tampered_token_rejected(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        tampered = res.token.access_token[:-1] + ("A" if res.token.access_token[-1] != "A" else "B")
        assert auth.verify_token(tampered) is None

    def test_revoked_token_rejected(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        payload = auth.verify_token(res.token.access_token)
        auth._revoked_tokens.add(payload.jti)
        assert auth.verify_token(res.token.access_token) is None

    def test_token_contains_user_info(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        payload = auth.verify_token(res.token.access_token)
        assert payload.username == "admin"
        assert payload.role == UserRole.ADMIN.value

    def test_token_has_expiry(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        payload = auth.verify_token(res.token.access_token)
        assert payload.exp > time.time()

    def test_refresh_token_works(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        refreshed = auth.refresh_access_token(res.token.refresh_token)
        assert refreshed is not None
        assert refreshed.access_token != res.token.access_token

    def test_invalid_refresh_token(self, auth: AuthManager):
        assert auth.refresh_access_token("not-a-token") is None


class TestSessions:
    def test_session_created_on_login(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        assert auth.get_active_sessions(user_id=res.token.user_id)

    def test_session_timeout(self, auth: AuthManager):
        auth._session_timeout_minutes = 0
        res = auth.login("admin", "admin123!")
        assert auth.verify_token(res.token.access_token) is None

    def test_logout_removes_session(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        assert auth.logout(res.token.session_id)
        assert not auth.get_active_sessions(user_id=res.token.user_id)

    def test_logout_all_sessions(self, auth: AuthManager):
        res1 = auth.login("admin", "admin123!")
        res2 = auth.login("admin", "admin123!")
        count = auth.logout_all_sessions(res1.token.user_id)
        assert count >= 2
        assert not auth.get_active_sessions(user_id=res1.token.user_id)

    def test_get_active_sessions(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        sessions = auth.get_active_sessions(user_id=res.token.user_id)
        assert len(sessions) >= 1

    def test_cleanup_expired(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        sid = res.token.session_id
        session = auth._sessions[sid]
        session.last_activity = time.time() - 99999
        session.expires_at = time.time() - 1
        cleaned = auth.cleanup_expired_sessions()
        assert cleaned >= 1
        assert sid not in auth._sessions


class TestAPIKeys:
    def test_create_api_key(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        api_key_resp = auth.create_api_key(res.token.user_id, "Key1", UserRole.CODER)
        assert api_key_resp.api_key.startswith(API_KEY_PREFIX)

    def test_verify_valid_key(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        api_key_resp = auth.create_api_key(res.token.user_id, "Key1", UserRole.CODER)
        current = auth.verify_api_key(api_key_resp.api_key)
        assert isinstance(current, CurrentUser)

    def test_verify_invalid_key(self, auth: AuthManager):
        assert auth.verify_api_key("invalid") is None

    def test_verify_expired_key(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        api_key_resp = auth.create_api_key(res.token.user_id, "Key1", UserRole.CODER, expires_in_days=1)
        entry = next(v for v in auth._api_keys.values() if v["key_id"] == api_key_resp.key_id)
        entry["expires_at"] = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
        assert auth.verify_api_key(api_key_resp.api_key) is None

    def test_revoke_api_key(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        api_key_resp = auth.create_api_key(res.token.user_id, "Key1", UserRole.CODER)
        assert auth.revoke_api_key(api_key_resp.key_id, res.token.user_id)
        assert auth.verify_api_key(api_key_resp.api_key) is None

    def test_list_api_keys(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        auth.create_api_key(res.token.user_id, "Key1", UserRole.CODER)
        keys = auth.list_api_keys(res.token.user_id)
        assert keys and "api_key" not in keys[0]


class TestPermissions:
    def test_coder_can_code(self, auth: AuthManager):
        assert auth.has_permission(UserRole.CODER.value, "coding:process")

    def test_coder_cannot_update_kb(self, auth: AuthManager):
        assert not auth.has_permission(UserRole.CODER.value, "knowledge:update")

    def test_admin_has_all(self, auth: AuthManager):
        assert auth.has_permission(UserRole.ADMIN.value, "user:create")
        assert auth.has_permission(UserRole.ADMIN.value, "apikey:create")

    def test_auditor_read_only(self, auth: AuthManager):
        assert auth.has_permission(UserRole.AUDITOR.value, "audit:search")
        assert not auth.has_permission(UserRole.AUDITOR.value, "coding:process")

    def test_reviewer_can_review(self, auth: AuthManager):
        assert auth.has_permission(UserRole.REVIEWER.value, "escalation:review")

    def test_system_has_wildcard(self, auth: AuthManager):
        assert auth.has_permission(UserRole.SYSTEM.value, "anything:at:all")

    def test_check_permission_raises(self, auth: AuthManager):
        user = CurrentUser(user_id="U1", username="u", role=UserRole.CODER, session_id="s")
        with pytest.raises(HTTPException):
            auth.check_permission(user, "knowledge:update")

    def test_role_permissions_complete(self):
        assert all(len(perms) >= 3 for perms in ROLE_PERMISSIONS.values())


class TestAuditLog:
    def test_login_logged(self, auth: AuthManager):
        auth.login("admin", "admin123!")
        assert any(e.event_type == "login_success" for e in auth.get_audit_log())

    def test_failed_login_logged(self, auth: AuthManager):
        auth.login("admin", "wrong")
        assert any(e.event_type == "login_failed" for e in auth.get_audit_log())

    def test_logout_logged(self, auth: AuthManager):
        res = auth.login("admin", "admin123!")
        auth.logout(res.token.session_id)
        assert any(e.event_type == "logout" for e in auth.get_audit_log())

    def test_audit_log_filtered(self, auth: AuthManager):
        auth.login("admin", "admin123!")
        filtered = auth.get_audit_log(event_type="login_success")
        assert all(e.event_type == "login_success" for e in filtered)


class TestFastAPIDependencies:
    def test_require_role_allows(self):
        dep = require_role("ADMIN")
        current = CurrentUser(user_id="1", username="u", role=UserRole.ADMIN, session_id="s")
        assert dep(current) == current

    def test_require_role_denies(self):
        dep = require_role("ADMIN")
        current = CurrentUser(user_id="1", username="u", role=UserRole.CODER, session_id="s")
        with pytest.raises(HTTPException) as exc:
            dep(current)
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    def test_require_permission_allows(self):
        dep = require_permission("knowledge:search")
        current = CurrentUser(user_id="1", username="u", role=UserRole.ADMIN, session_id="s")
        assert dep(current) == current

    def test_require_permission_denies(self):
        dep = require_permission("knowledge:update")
        current = CurrentUser(user_id="1", username="u", role=UserRole.CODER, session_id="s")
        with pytest.raises(HTTPException) as exc:
            dep(current)
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN

    def test_get_auth_manager(self):
        assert isinstance(get_auth_manager(), AuthManager)
