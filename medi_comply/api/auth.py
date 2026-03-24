from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_EXPIRY_MINUTES = 60
DEFAULT_SESSION_TIMEOUT_MINUTES = 15
DEFAULT_REFRESH_TOKEN_EXPIRY_DAYS = 7
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 30
API_KEY_PREFIX = "mc_"
TOKEN_ALGORITHM = "HS256"

SECRET_KEY = "medi-comply-secret-key-change-in-production-" + secrets.token_hex(16)
REFRESH_SECRET_KEY = "medi-comply-refresh-secret-" + secrets.token_hex(16)


class UserRole(str, Enum):
    CODER = "CODER"
    REVIEWER = "REVIEWER"
    ADMIN = "ADMIN"
    AUDITOR = "AUDITOR"
    SYSTEM = "SYSTEM"
class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"
    API_KEY = "api_key"
class AuthEventType(str, Enum):
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    TOKEN_REFRESH = "token_refresh"
    LOGOUT = "logout"
    ACCESS_DENIED = "access_denied"
    ACCOUNT_LOCKED = "account_locked"
    ACCOUNT_UNLOCKED = "account_unlocked"
    PASSWORD_CHANGED = "password_changed"
    API_KEY_CREATED = "api_key_created"
    API_KEY_REVOKED = "api_key_revoked"
    SESSION_EXPIRED = "session_expired"
class UserAccount(BaseModel):
    user_id: str = Field(default_factory=lambda: f"USR-{uuid.uuid4().hex[:8].upper()}")
    username: str
    password_hash: str = ""
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: UserRole = UserRole.CODER
    is_active: bool = True
    is_locked: bool = False
    failed_attempts: int = 0
    locked_until: Optional[float] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    last_login: Optional[str] = None
    api_keys: List[str] = Field(default_factory=list)
    mfa_enabled: bool = False
    mfa_secret: Optional[str] = None
class TokenPayload(BaseModel):
    sub: str
    username: str
    role: str
    token_type: str = "access"
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    iat: float = Field(default_factory=time.time)
    exp: float = 0.0
    jti: str = Field(default_factory=lambda: uuid.uuid4().hex)
class AuthToken(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "Bearer"
    expires_in: int
    role: str
    username: str
    user_id: str
    session_id: str
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)

    model_config = ConfigDict(json_schema_extra={"example": {"username": "coder_jane", "password": "secure_password_123"}})


class LoginResponse(BaseModel):
    success: bool
    token: Optional[AuthToken] = None
    message: str = ""
    user_id: Optional[str] = None
    role: Optional[str] = None
    requires_mfa: bool = False
class TokenRefreshRequest(BaseModel):
    refresh_token: str
class APIKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    role: UserRole = UserRole.CODER
    expires_in_days: int = Field(default=365, ge=1, le=3650)


class APIKeyResponse(BaseModel):
    api_key: str
    key_id: str
    name: str
    role: str
    created_at: str
    expires_at: str
    message: str = "Store this API key securely. It will not be shown again."


class ActiveSession(BaseModel):
    session_id: str
    user_id: str
    username: str
    role: str
    created_at: float
    last_activity: float
    expires_at: float
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    is_active: bool = True


class AuthAuditEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    event_type: str
    user_id: Optional[str] = None
    username: Optional[str] = None
    ip_address: Optional[str] = None
    details: str = ""
    success: bool = True


class CurrentUser(BaseModel):
    """Represents the authenticated user in request context."""

    user_id: str
    username: str
    role: UserRole
    session_id: str
    token_type: str = "access"

ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    "CODER": {"coding:process", "coding:validate", "auth:check_required", "audit:read_own", "knowledge:search"},
    "REVIEWER": {"coding:process", "coding:validate", "claims:adjudicate", "claims:validate", "auth:submit", "auth:check_required", "audit:read_all", "audit:explain", "compliance:dashboard", "knowledge:search", "escalation:review", "escalation:resolve"},
    "ADMIN": {"coding:process", "coding:validate", "claims:adjudicate", "claims:batch", "auth:submit", "auth:check_required", "audit:read_all", "audit:explain", "audit:search", "compliance:dashboard", "compliance:report", "knowledge:search", "knowledge:update", "user:create", "user:update", "user:delete", "user:list", "apikey:create", "apikey:revoke", "escalation:review", "escalation:resolve", "system:config"},
    "AUDITOR": {"audit:read_all", "audit:explain", "audit:search", "compliance:dashboard", "compliance:report", "knowledge:search", "coding:validate"},
    "SYSTEM": {"*", "system:config", "system:audit"},
}

class AuthManager:
    """Central authentication and authorization manager."""

    def __init__(self) -> None:
        self.logger = logger
        self._users: Dict[str, UserAccount] = {}
        self._sessions: Dict[str, ActiveSession] = {}
        self._revoked_tokens: Set[str] = set()
        self._api_keys: Dict[str, Dict[str, Any]] = {}
        self._audit_log: List[AuthAuditEntry] = []
        self._secret_key = SECRET_KEY
        self._refresh_secret = REFRESH_SECRET_KEY
        self._token_expiry_minutes = DEFAULT_TOKEN_EXPIRY_MINUTES
        self._session_timeout_minutes = DEFAULT_SESSION_TIMEOUT_MINUTES
        self._max_failed_attempts = MAX_FAILED_ATTEMPTS
        self._lockout_duration_minutes = LOCKOUT_DURATION_MINUTES
        self._seed_default_users()

    def _seed_default_users(self) -> None:
        """Create demo users for quick start."""
        defaults = [("admin", "admin123!", UserRole.ADMIN, "System Administrator"), ("coder_jane", "coder123!", UserRole.CODER, "Jane Doe - Medical Coder"), ("coder_john", "coder123!", UserRole.CODER, "John Smith - Medical Coder"), ("reviewer_sarah", "review123!", UserRole.REVIEWER, "Sarah Johnson - Reviewer"), ("auditor_mike", "audit123!", UserRole.AUDITOR, "Mike Wilson - Compliance Auditor")]
        for username, pw, role, full_name in defaults:
            if username not in self._users:
                self.create_user(username, pw, role, full_name=full_name)

    def _generate_salt(self) -> str:
        """Generate random salt."""
        return secrets.token_hex(16)

    def _hash_password(self, password: str, salt: Optional[str] = None) -> str:
        """Hash password using SHA-256 with salt."""
        salt = salt or self._generate_salt()
        digest = hashlib.sha256((salt + password).encode()).hexdigest()
        return f"{salt}:{digest}"

    def _verify_password(self, password: str, password_hash: str) -> bool:
        """Timing-safe password verification."""
        try:
            salt, stored = password_hash.split(":", 1)
        except ValueError:
            return False
        calc = hashlib.sha256((salt + password).encode()).hexdigest()
        return hmac.compare_digest(calc, stored)

    def _validate_username(self, username: str) -> bool:
        """Basic username validation for account operations."""
        if not isinstance(username, str):
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9_]{3,50}", username))

    def _enforce_password_policy(self, password: str) -> None:
        """Ensure passwords include letters, digits, and sufficient length."""
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            raise ValueError("Password must include letters and digits")

    def create_user(
        self,
        username: str,
        password: str,
        role: UserRole,
        full_name: Optional[str] = None,
        email: Optional[str] = None,
    ) -> UserAccount:
        """Create a new user account."""
        if not self._validate_username(username):
            raise ValueError("Username must be 3-50 chars, alphanumeric or underscore")
        if username in self._users:
            raise ValueError("Username already exists")
        self._enforce_password_policy(password)
        password_hash = self._hash_password(password)
        user = UserAccount(
            username=username,
            password_hash=password_hash,
            role=role,
            full_name=full_name,
            email=email,
        )
        self._users[username] = user
        self._log_audit(AuthEventType.LOGIN_SUCCESS.value, user_id=user.user_id, username=username, details="user_created")
        return user

    def get_user(self, username: str) -> Optional[UserAccount]:
        """Lookup user by username."""
        return self._users.get(username)

    def get_user_by_id(self, user_id: str) -> Optional[UserAccount]:
        """Lookup user by user_id."""
        for user in self._users.values():
            if user.user_id == user_id:
                return user
        return None

    def update_user(self, username: str, updates: Dict[str, Any]) -> Optional[UserAccount]:
        """Update mutable fields of a user."""
        user = self._users.get(username)
        if not user:
            return None
        if "role" in updates:
            user.role = UserRole(updates["role"])
        if "is_active" in updates:
            user.is_active = bool(updates["is_active"])
        if "email" in updates:
            user.email = updates["email"]
        if "full_name" in updates:
            user.full_name = updates["full_name"]
        self._log_audit("user_updated", user_id=user.user_id, username=user.username, details=json.dumps(updates))
        return user

    def delete_user(self, username: str) -> bool:
        """Soft-delete a user (mark inactive)."""
        user = self._users.get(username)
        if not user:
            return False
        user.is_active = False
        self.logout_all_sessions(user.user_id)
        self._log_audit("user_deleted", user_id=user.user_id, username=username)
        return True

    def list_users(self) -> List[Dict[str, Any]]:
        """List users without password hashes."""
        return [
            {
                "user_id": user.user_id,
                "username": user.username,
                "role": user.role.value,
                "is_active": user.is_active,
                "last_login": user.last_login,
                "created_at": user.created_at,
            }
            for user in self._users.values()
        ]

    def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        """Change password after verifying old password."""
        user = self._users.get(username)
        if not user:
            return False
        if not self._verify_password(old_password, user.password_hash):
            return False
        try:
            self._enforce_password_policy(new_password)
        except ValueError:
            return False
        user.password_hash = self._hash_password(new_password)
        self._log_audit(AuthEventType.PASSWORD_CHANGED.value, user_id=user.user_id, username=username)
        return True

    def reset_failed_attempts(self, username: str) -> bool:
        """Reset failed login attempts for a user."""
        user = self._users.get(username)
        if not user:
            return False
        user.failed_attempts = 0
        user.is_locked = False
        user.locked_until = None
        self._log_audit(AuthEventType.ACCOUNT_UNLOCKED.value, user_id=user.user_id, username=username)
        return True

    def unlock_user(self, username: str) -> bool:
        """Unlock user account regardless of lock timer."""
        user = self._users.get(username)
        if not user:
            return False
        user.is_locked = False
        user.locked_until = None
        user.failed_attempts = 0
        self._log_audit(AuthEventType.ACCOUNT_UNLOCKED.value, user_id=user.user_id, username=username)
        return True

    def reactivate_user(self, username: str) -> bool:
        """Reactivate a previously deactivated account."""
        user = self._users.get(username)
        if not user:
            return False
        user.is_active = True
        self._log_audit("user_reactivated", user_id=user.user_id, username=username)
        return True

    def enable_mfa(self, username: str, secret: Optional[str] = None) -> bool:
        """Enable MFA for a user with a provided or generated secret."""
        user = self._users.get(username)
        if not user:
            return False
        user.mfa_enabled = True
        user.mfa_secret = secret or secrets.token_hex(8)
        self._log_audit("mfa_enabled", user_id=user.user_id, username=username)
        return True

    def disable_mfa(self, username: str) -> bool:
        """Disable MFA for a user."""
        user = self._users.get(username)
        if not user:
            return False
        user.mfa_enabled = False
        user.mfa_secret = None
        self._log_audit("mfa_disabled", user_id=user.user_id, username=username)
        return True

    def verify_mfa_token(self, username: str, token: str) -> bool:
        """Placeholder MFA verification using HMAC of secret and minute bucket."""
        user = self._users.get(username)
        if not user or not user.mfa_enabled or not user.mfa_secret:
            return False
        bucket = int(time.time() // 60)
        expected = hmac.new(user.mfa_secret.encode(), str(bucket).encode(), hashlib.sha256).hexdigest()[:6]
        return hmac.compare_digest(expected, token)

    def get_user_security_status(self, username: str) -> Dict[str, Any]:
        """Return security posture for a user (lock, MFA, failed attempts)."""
        user = self._users.get(username)
        if not user:
            return {}
        return {
            "is_locked": user.is_locked,
            "locked_until": user.locked_until,
            "failed_attempts": user.failed_attempts,
            "mfa_enabled": user.mfa_enabled,
            "last_login": user.last_login,
        }

    def list_locked_users(self) -> List[Dict[str, Any]]:
        """Return a list of currently locked accounts."""
        locked: List[Dict[str, Any]] = []
        for user in self._users.values():
            if user.is_locked:
                locked.append(
                    {
                        "username": user.username,
                        "user_id": user.user_id,
                        "locked_until": user.locked_until,
                        "failed_attempts": user.failed_attempts,
                    }
                )
        return locked

    def account_health_report(self) -> Dict[str, Any]:
        """Summarize account states for admin dashboards."""
        total = len(self._users)
        active = self.active_user_count()
        locked = len(self.list_locked_users())
        return {
            "total_users": total,
            "active_users": active,
            "locked_users": locked,
            "active_sessions": self.active_session_count(),
            "active_api_keys": self.active_api_key_count(),
        }

    def _create_session(
        self,
        user: UserAccount,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> ActiveSession:
        """Create a new session for the user."""
        now = time.time()
        session_id = uuid.uuid4().hex[:12]
        expires_at = now + self._session_timeout_minutes * 60
        session = ActiveSession(
            session_id=session_id,
            user_id=user.user_id,
            username=user.username,
            role=user.role.value,
            created_at=now,
            last_activity=now,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._sessions[session_id] = session
        return session

    def _check_session_timeout(self, session_id: str) -> bool:
        """Return True if session active; deactivate if expired."""
        session = self._sessions.get(session_id)
        if not session or not session.is_active:
            return False
        now = time.time()
        timeout_window = self._session_timeout_minutes * 60
        if self._session_timeout_minutes <= 0 or now >= session.expires_at or now - session.last_activity >= timeout_window:
            session.is_active = False
            self._log_audit(AuthEventType.SESSION_EXPIRED.value, user_id=session.user_id, username=session.username)
            return False
        return True

    def get_active_sessions(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return active sessions, optionally filtered by user."""
        return [
            {
                "session_id": session.session_id,
                "username": session.username,
                "role": session.role,
                "created_at": session.created_at,
                "last_activity": session.last_activity,
            }
            for session in self._sessions.values()
            if session.is_active and (not user_id or session.user_id == user_id)
        ]

    def cleanup_expired_sessions(self) -> int:
        """Remove expired sessions."""
        removed = 0
        for session_id, session in list(self._sessions.items()):
            if not self._check_session_timeout(session_id):
                removed += 1
                self._sessions.pop(session_id, None)
        return removed

    def logout(self, session_id: str) -> bool:
        """Invalidate a session and revoke its token IDs."""
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.is_active = False
        self._sessions.pop(session_id, None)
        self._log_audit(AuthEventType.LOGOUT.value, user_id=session.user_id, username=session.username)
        return True

    def logout_all_sessions(self, user_id: str) -> int:
        """Revoke all sessions for a user."""
        count = 0
        for sid, session in list(self._sessions.items()):
            if session.user_id == user_id:
                session.is_active = False
                self._sessions.pop(sid, None)
                count += 1
        return count

    def _base64url_encode(self, data: bytes) -> str:
        """Base64 URL-safe encoding without padding."""
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    def _base64url_decode(self, data: str) -> bytes:
        """Base64 URL-safe decoding with padding restored."""
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + padding)

    def _sign(self, payload_b64: str, secret: str) -> str:
        """HMAC-SHA256 signature of payload."""
        sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
        return self._base64url_encode(sig)

    def _encode_token(self, payload: Dict[str, Any], secret: str) -> str:
        """Encode payload into a JWT-like token."""
        header = {"alg": TOKEN_ALGORITHM, "typ": "JWT"}
        header_b64 = self._base64url_encode(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = self._base64url_encode(json.dumps(payload, separators=(",", ":")).encode())
        signature = self._sign(f"{header_b64}.{payload_b64}", secret)
        return f"{header_b64}.{payload_b64}.{signature}"

    def _decode_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Decode token payload without verifying signature."""
        try:
            _header, payload_b64, _sig = token.split(".")
            payload_json = self._base64url_decode(payload_b64)
            return json.loads(payload_json)
        except Exception:
            return None

    def _generate_access_token(self, user: UserAccount, session_id: str) -> str:
        """Generate signed access token."""
        exp = time.time() + self._token_expiry_minutes * 60
        payload = TokenPayload(
            sub=user.user_id,
            username=user.username,
            role=user.role.value,
            token_type=TokenType.ACCESS.value,
            session_id=session_id,
            exp=exp,
        ).model_dump()
        return self._encode_token(payload, self._secret_key)

    def _generate_refresh_token(self, user: UserAccount, session_id: str) -> str:
        """Generate signed refresh token."""
        exp = time.time() + DEFAULT_REFRESH_TOKEN_EXPIRY_DAYS * 24 * 60 * 60
        payload = TokenPayload(
            sub=user.user_id,
            username=user.username,
            role=user.role.value,
            token_type=TokenType.REFRESH.value,
            session_id=session_id,
            exp=exp,
        ).model_dump()
        return self._encode_token(payload, self._refresh_secret)

    def _reconstruct_payload(self, data: Dict[str, Any]) -> Optional[TokenPayload]:
        """Create TokenPayload from dict, returning None on failure."""
        try:
            return TokenPayload(**data)
        except Exception:
            return None

    def login(
        self,
        username: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> LoginResponse:
        """Authenticate user and issue tokens."""
        if not self._validate_username(username):
            return LoginResponse(success=False, message="Invalid credentials")
        user = self._users.get(username)
        if not user:
            return LoginResponse(success=False, message="Invalid credentials")
        if not user.is_active:
            return LoginResponse(success=False, message="Account inactive")
        if user.is_locked:
            if user.locked_until and time.time() < user.locked_until:
                self._log_audit(AuthEventType.ACCOUNT_LOCKED.value, user_id=user.user_id, username=username, details="locked")
                return LoginResponse(success=False, message="Account locked")
            user.is_locked = False
            user.failed_attempts = 0
            user.locked_until = None
            self._log_audit(AuthEventType.ACCOUNT_UNLOCKED.value, user_id=user.user_id, username=username)
        if not self._verify_password(password, user.password_hash):
            user.failed_attempts += 1
            if user.failed_attempts >= self._max_failed_attempts:
                user.is_locked = True
                user.locked_until = time.time() + self._lockout_duration_minutes * 60
                self._log_audit(AuthEventType.ACCOUNT_LOCKED.value, user_id=user.user_id, username=username, success=False)
            else:
                self._log_audit(AuthEventType.LOGIN_FAILED.value, user_id=user.user_id, username=username, success=False)
            return LoginResponse(success=False, message="Invalid credentials")
        user.failed_attempts = 0
        session = self._create_session(user, ip_address=ip_address, user_agent=user_agent)
        access = self._generate_access_token(user, session.session_id)
        refresh = self._generate_refresh_token(user, session.session_id)
        user.last_login = datetime.utcnow().isoformat() + "Z"
        token = AuthToken(
            access_token=access,
            refresh_token=refresh,
            expires_in=self._token_expiry_minutes * 60,
            role=user.role.value,
            username=user.username,
            user_id=user.user_id,
            session_id=session.session_id,
        )
        self._log_audit(AuthEventType.LOGIN_SUCCESS.value, user_id=user.user_id, username=user.username, ip_address=ip_address)
        return LoginResponse(success=True, token=token, message="Login successful", user_id=user.user_id, role=user.role.value)

    def verify_token(self, token: str, token_type: str = TokenType.ACCESS.value) -> Optional[TokenPayload]:
        """Verify signature, expiry, revocation, and session validity."""
        try:
            header_b64, payload_b64, signature = token.split(".")
        except ValueError:
            return None
        signed = f"{header_b64}.{payload_b64}"
        secret = self._secret_key if token_type == TokenType.ACCESS.value else self._refresh_secret
        expected = self._sign(signed, secret)
        if not hmac.compare_digest(signature, expected):
            return None
        payload_dict = self._decode_token(token)
        if not payload_dict:
            return None
        payload = self._reconstruct_payload(payload_dict)
        if not payload:
            return None
        if payload.token_type != token_type:
            return None
        if payload.exp <= time.time():
            return None
        if payload.jti in self._revoked_tokens:
            return None
        session = self._sessions.get(payload.session_id)
        if not session or not session.is_active:
            return None
        if not self._check_session_timeout(payload.session_id):
            self._revoked_tokens.add(payload.jti)
            return None
        session.last_activity = time.time()
        session.expires_at = session.last_activity + self._session_timeout_minutes * 60
        return payload

    def refresh_access_token(self, refresh_token: str) -> Optional[AuthToken]:
        """Use refresh token to mint a new access token."""
        payload = self.verify_token(refresh_token, token_type=TokenType.REFRESH.value)
        if not payload:
            return None
        user = self.get_user_by_id(payload.sub)
        if not user:
            return None
        access = self._generate_access_token(user, payload.session_id)
        token = AuthToken(
            access_token=access,
            refresh_token=refresh_token,
            expires_in=self._token_expiry_minutes * 60,
            role=user.role.value,
            username=user.username,
            user_id=user.user_id,
            session_id=payload.session_id,
        )
        self._log_audit(AuthEventType.TOKEN_REFRESH.value, user_id=user.user_id, username=user.username)
        return token

    def _hash_api_key(self, api_key: str) -> str:
        """Hash API key for storage."""
        return hashlib.sha256(api_key.encode()).hexdigest()

    def create_api_key(self, user_id: str, name: str, role: UserRole, expires_in_days: int = 365) -> APIKeyResponse:
        """Create and store an API key for a user."""
        raw_key = API_KEY_PREFIX + secrets.token_hex(32)
        key_hash = self._hash_api_key(raw_key)
        key_id = uuid.uuid4().hex[:12]
        created_at = datetime.utcnow()
        expires_at = created_at + timedelta(days=expires_in_days)
        self._api_keys[key_hash] = {
            "user_id": user_id,
            "role": role.value,
            "name": name,
            "created_at": created_at.isoformat() + "Z",
            "expires_at": expires_at.isoformat() + "Z",
            "key_id": key_id,
        }
        user = self.get_user_by_id(user_id)
        if user:
            user.api_keys.append(key_hash)
        self._log_audit(AuthEventType.API_KEY_CREATED.value, user_id=user_id, details=name)
        return APIKeyResponse(
            api_key=raw_key,
            key_id=key_id,
            name=name,
            role=role.value,
            created_at=created_at.isoformat() + "Z",
            expires_at=expires_at.isoformat() + "Z",
        )

    def verify_api_key(self, api_key: str) -> Optional[CurrentUser]:
        """Verify API key and return CurrentUser."""
        key_hash = self._hash_api_key(api_key)
        entry = self._api_keys.get(key_hash)
        if not entry:
            return None
        if datetime.fromisoformat(entry["expires_at"].replace("Z", "")) < datetime.utcnow():
            return None
        user = self.get_user_by_id(entry["user_id"])
        if not user:
            return None
        return CurrentUser(
            user_id=user.user_id,
            username=user.username,
            role=UserRole(user.role),
            session_id="api-key",
            token_type=TokenType.API_KEY.value,
        )

    def revoke_api_key(self, key_id: str, user_id: str) -> bool:
        """Revoke an API key by key_id for a user."""
        found = False
        for key_hash, entry in list(self._api_keys.items()):
            if entry.get("key_id") == key_id and entry.get("user_id") == user_id:
                self._api_keys.pop(key_hash, None)
                found = True
        if found:
            self._log_audit(AuthEventType.API_KEY_REVOKED.value, user_id=user_id, details=key_id)
        return found

    def list_api_keys(self, user_id: str) -> List[Dict[str, str]]:
        """List API key metadata for a user."""
        return [
            {
                "key_id": entry.get("key_id"),
                "name": entry.get("name"),
                "role": entry.get("role"),
                "created_at": entry.get("created_at"),
                "expires_at": entry.get("expires_at"),
            }
            for entry in self._api_keys.values()
            if entry.get("user_id") == user_id
        ]

    def has_permission(self, role: str, permission: str) -> bool:
        """Check if a role grants a permission."""
        if role == UserRole.SYSTEM.value:
            return True
        perms = ROLE_PERMISSIONS.get(role, set())
        return "*" in perms or permission in perms

    def get_role_permissions(self, role: str) -> Set[str]:
        """Return permissions for a role."""
        return set(ROLE_PERMISSIONS.get(role, set()))

    def check_permission(self, user: CurrentUser, permission: str) -> None:
        """Raise HTTPException if permission denied."""
        if not self.has_permission(user.role.value, permission):
            self._log_audit(
                AuthEventType.ACCESS_DENIED.value,
                user_id=user.user_id,
                username=user.username,
                details=f"Permission denied: {permission}",
                success=False,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    def _log_audit(
        self,
        event_type: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        ip_address: Optional[str] = None,
        details: str = "",
        success: bool = True,
    ) -> None:
        """Append audit entry, trimming to last 10k entries."""
        entry = AuthAuditEntry(
            event_type=event_type,
            user_id=user_id,
            username=username,
            ip_address=ip_address,
            details=details,
            success=success,
        )
        self._audit_log.append(entry)
        if len(self._audit_log) > 10000:
            self._audit_log = self._audit_log[-10000:]

    def get_audit_log(self, user_id: Optional[str] = None, event_type: Optional[str] = None, limit: int = 100) -> List[AuthAuditEntry]:
        """Return filtered audit entries."""
        results: List[AuthAuditEntry] = []
        for entry in reversed(self._audit_log):
            if user_id and entry.user_id != user_id:
                continue
            if event_type and entry.event_type != event_type:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return list(reversed(results))

_auth_manager = AuthManager()
bearer_scheme = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    api_key: Optional[str] = Security(api_key_header),
) -> CurrentUser:
    """Resolve current user from bearer token or API key."""
    if credentials and credentials.credentials:
        payload = _auth_manager.verify_token(credentials.credentials, token_type=TokenType.ACCESS.value)
        if not payload:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
        return CurrentUser(
            user_id=payload.sub,
            username=payload.username,
            role=UserRole(payload.role),
            session_id=payload.session_id,
            token_type=payload.token_type,
        )
    if api_key:
        user = _auth_manager.verify_api_key(api_key)
        if user:
            return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


def require_role(*roles: str) -> Callable:
    """Dependency that enforces user role membership."""

    def role_checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role == UserRole.SYSTEM:
            return current_user
        if current_user.role.value not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role.value}' does not have access. Required: {roles}",
            )
        return current_user

    return role_checker


def require_permission(permission: str) -> Callable:
    """Dependency that enforces a specific permission."""

    def permission_checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not _auth_manager.has_permission(current_user.role.value, permission):
            _auth_manager._log_audit(
                AuthEventType.ACCESS_DENIED.value,
                user_id=current_user.user_id,
                username=current_user.username,
                details=f"Permission denied: {permission}",
                success=False,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' not granted to role '{current_user.role.value}'",
            )
        return current_user

    return permission_checker


def get_auth_manager() -> AuthManager:
    """Expose the module-level AuthManager."""

    return _auth_manager

auth_router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


@auth_router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, req: Request):
    ip = req.client.host if req.client else None
    ua = req.headers.get("user-agent")
    return _auth_manager.login(request.username, request.password, ip, ua)


@auth_router.post("/logout")
async def logout(current_user: CurrentUser = Depends(get_current_user)):
    _auth_manager.logout(current_user.session_id)
    return {"message": "Logged out successfully"}


@auth_router.post("/refresh")
async def refresh_token(request: TokenRefreshRequest):
    result = _auth_manager.refresh_access_token(request.refresh_token)
    if not result:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")
    return result


@auth_router.get("/me")
async def get_current_user_info(current_user: CurrentUser = Depends(get_current_user)):
    user = _auth_manager.get_user_by_id(current_user.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return {"user_id": user.user_id, "username": user.username, "role": user.role.value, "full_name": user.full_name, "email": user.email, "last_login": user.last_login, "mfa_enabled": user.mfa_enabled}


@auth_router.post("/api-keys", response_model=APIKeyResponse)
async def create_api_key(
    request: APIKeyCreateRequest,
    current_user: CurrentUser = Depends(require_role("ADMIN")),
):
    return _auth_manager.create_api_key(current_user.user_id, request.name, request.role, request.expires_in_days)


@auth_router.get("/api-keys")
async def list_api_keys(current_user: CurrentUser = Depends(get_current_user)):
    return _auth_manager.list_api_keys(current_user.user_id)


@auth_router.delete("/api-keys/{key_id}")
async def revoke_api_key(key_id: str, current_user: CurrentUser = Depends(get_current_user)):
    if _auth_manager.revoke_api_key(key_id, current_user.user_id):
        return {"message": "API key revoked"}
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")


@auth_router.get("/sessions")
async def list_sessions(current_user: CurrentUser = Depends(require_role("ADMIN"))):
    return _auth_manager.get_active_sessions()


@auth_router.get("/audit-log")
async def get_auth_audit_log(
    limit: int = 100,
    current_user: CurrentUser = Depends(require_role("ADMIN", "AUDITOR")),
):
    return _auth_manager.get_audit_log(limit=limit)


@auth_router.post("/users")
async def create_user(
    username: str,
    password: str,
    role: UserRole,
    full_name: Optional[str] = None,
    current_user: CurrentUser = Depends(require_role("ADMIN")),
):
    try:
        user = _auth_manager.create_user(username, password, role, full_name)
        return {"user_id": user.user_id, "username": user.username, "role": user.role.value}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@auth_router.get("/users")
async def list_users(current_user: CurrentUser = Depends(require_role("ADMIN"))):
    return _auth_manager.list_users()

if __name__ == "__main__":
    manager = AuthManager()

    print("=== MEDI-COMPLY Auth System Demo ===\n")

    print("Default Users:")
    for user in manager.list_users():
        print(f"  {user['username']} ({user['role']}) - {user.get('full_name', 'N/A')}")

    print("\n--- Login Tests ---")
    result = manager.login("coder_jane", "coder123!")
    print(f"Login coder_jane: success={result.success}")
    if result.token:
        print(f"  Token: {result.token.access_token[:30]}...")
        print(f"  Role: {result.token.role}")
        print(f"  Expires in: {result.token.expires_in}s")
        payload = manager.verify_token(result.token.access_token)
        print(f"  Token valid: {payload is not None}")
        if payload:
            print(f"  User: {payload.username}, Role: {payload.role}")

    result2 = manager.login("coder_jane", "wrong_password")
    print(f"\nLogin with wrong password: success={result2.success}")
    print(f"  Message: {result2.message}")

    print("\n--- Permission Tests ---")
    for role in ["CODER", "REVIEWER", "ADMIN", "AUDITOR"]:
        perms = manager.get_role_permissions(role)
        print(f"  {role}: {len(perms)} permissions")
        can_update_kb = manager.has_permission(role, "knowledge:update")
        print(f"    Can update knowledge base: {can_update_kb}")

    print("\n--- API Key Test ---")
    admin_login = manager.login("admin", "admin123!")
    if admin_login.success and admin_login.token:
        key_response = manager.create_api_key(admin_login.token.user_id, "Test Key", UserRole.CODER)
        print(f"API Key created: {key_response.api_key[:20]}...")
        print(f"Key ID: {key_response.key_id}")
        user_from_key = manager.verify_api_key(key_response.api_key)
        print(f"API Key valid: {user_from_key is not None}")
        if user_from_key:
            print(f"  User: {user_from_key.username}, Role: {user_from_key.role}")

    print("\n--- Account Lockout Test ---")
    for i in range(6):
        res = manager.login("coder_john", "wrong_password")
        print(f"  Attempt {i+1}: success={res.success}, message={res.message}")

    print("\n--- Recent Audit Log ---")
    for entry in manager.get_audit_log(limit=10):
        print(f"  [{entry.timestamp[:19]}] {entry.event_type}: {entry.username or 'N/A'} - {entry.details[:60]}")

    print(f"\n--- Active Sessions: {len(manager.get_active_sessions())} ---")
    for session in manager.get_active_sessions():
        print(f"  {session['username']} ({session['role']}) - session {session['session_id'][:8]}...")
