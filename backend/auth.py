"""Authentication & JWT Security Layer for Sales-AI / legacy pyBIM admin endpoints.

Security model:
- Database-backed operator accounts (`backend.operator_auth`) are the primary
  authentication source and remain independent of legacy `PYBIM_*` settings.
- Legacy environment-driven admin credentials are disabled by default and
  require explicit opt-in (`PYBIM_ALLOW_LEGACY_ADMIN=true`) plus an explicit,
  non-default `PYBIM_ADMIN_PASSWORD`.
- JWT signing and verification fail closed unless `PYBIM_SECRET_KEY` is
  explicitly configured with a non-trivial secret (no ephemeral or hardcoded
  fallback keys).
- Module import never requires credentials or initializes operational databases.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Security configuration contracts
ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 Hours
MAX_ACCESS_TOKEN_EXPIRE_MINUTES = 10080  # 7 Days
MIN_SECRET_KEY_LENGTH = 32
MIN_SECRET_KEY_UNIQUE_CHARS = 8
MIN_ADMIN_PASSWORD_LENGTH = 8

# Non-secret module-level defaults (secrets and env credentials are never
# defaulted to hardcoded values or ephemeral random keys at import time).
SECRET_KEY: Optional[str] = None
ACCESS_TOKEN_EXPIRE_MINUTES = DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES
ADMIN_USERNAME: Optional[str] = None
ADMIN_PASSWORD: Optional[str] = None

_TRUTHY_FLAG_VALUES = frozenset({"1", "true", "yes", "on"})

# SHA-256 digests of disallowed historical default and common placeholder
# passwords (stored as hashes so plaintext defaults are absent from source).
_DISALLOWED_LEGACY_PASSWORD_SHA256 = frozenset(
    {
        "c106cf816f90bce7060f16a700359e7346a4d4f48f932d109ba189df1a5b514f",
        "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
        "ef92b778bafe771e89245b89ecbc08a44a4e166c06659911881f383d4473e94f",
        "240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9",
        "057ba03d6c44104863dc7361fe4578965d1887360f90a0895882e58a6248fc86",
        "494a715f7e9b4071aca61bac42ca858a309524e5864f0920030862a4ae7589be",
        "ef797c8118f02dfb649607dd5d3f8c7623048c9c063d532cc95c5ed7a898a64f",
    }
)

_INSECURE_SECRET_MARKERS = (
    "changeme",
    "default",
    "insecure",
    "placeholder",
    "replace-me",
    "replace_me",
    "your-secret",
    "your_secret",
    "super-secret",
    "super_secret",
    "dev-secret",
    "dev_secret",
    "pybim-secret",
    "pybim_secret",
    "secret-key",
    "secret_key",
)

security_bearer = HTTPBearer(auto_error=False)


class AuthConfigurationError(HTTPException):
    """Fail-closed error raised when admin JWT configuration is missing or unsafe."""

    def __init__(self, detail: str = "Admin JWT authentication is not securely configured.") -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _constant_time_equals(a: str, b: str) -> bool:
    """Compare two strings in constant time without leaking length differences."""
    return secrets.compare_digest(
        hashlib.sha256(a.encode("utf-8")).digest(),
        hashlib.sha256(b.encode("utf-8")).digest(),
    )


def _parse_legacy_admin_flag(raw: Optional[str]) -> bool:
    """Return True only when PYBIM_ALLOW_LEGACY_ADMIN is explicitly truthy; fail closed otherwise."""
    return raw is not None and raw.strip().lower() in _TRUTHY_FLAG_VALUES


def is_legacy_admin_enabled() -> bool:
    """Return True only if legacy env-based admin authentication is explicitly enabled."""
    return _parse_legacy_admin_flag(os.getenv("PYBIM_ALLOW_LEGACY_ADMIN"))


def _is_safe_secret_key(raw: Optional[str]) -> bool:
    """Validate that PYBIM_SECRET_KEY is explicitly configured and passes baseline safety checks."""
    if not isinstance(raw, str) or not raw:
        return False
    if raw != raw.strip():
        return False
    if len(raw) < MIN_SECRET_KEY_LENGTH:
        return False
    if len(set(raw)) < MIN_SECRET_KEY_UNIQUE_CHARS:
        return False
    lowered = raw.lower()
    if any(marker in lowered for marker in _INSECURE_SECRET_MARKERS):
        return False
    return True


def get_jwt_secret_key() -> str:
    """Return the validated PYBIM_SECRET_KEY or fail closed with AuthConfigurationError."""
    raw = os.getenv("PYBIM_SECRET_KEY")
    if not _is_safe_secret_key(raw):
        raise AuthConfigurationError("Admin JWT authentication is not securely configured.")
    assert raw is not None
    return raw


def _get_token_expire_minutes() -> int:
    """Return validated token lifetime in minutes or fail closed on invalid configuration."""
    raw = os.getenv("PYBIM_TOKEN_EXPIRE_MINUTES")
    if raw is None:
        return DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES
    cleaned = raw.strip()
    if not cleaned:
        raise AuthConfigurationError("Invalid admin JWT expiration configuration.")
    try:
        minutes = int(cleaned)
    except ValueError as exc:
        raise AuthConfigurationError("Invalid admin JWT expiration configuration.") from exc
    if minutes < 1 or minutes > MAX_ACCESS_TOKEN_EXPIRE_MINUTES:
        raise AuthConfigurationError("Invalid admin JWT expiration configuration.")
    return minutes


def _get_legacy_admin_credentials() -> Optional[Tuple[str, str]]:
    """Return explicit legacy env admin credentials only when explicitly enabled and valid."""
    if not is_legacy_admin_enabled():
        return None

    raw_user = os.getenv("PYBIM_ADMIN_USER")
    if raw_user is None:
        env_user = "admin"
    else:
        env_user = raw_user.strip()
        if not env_user:
            return None

    raw_pass = os.getenv("PYBIM_ADMIN_PASSWORD")
    if not isinstance(raw_pass, str) or not raw_pass or not raw_pass.strip():
        return None
    if len(raw_pass) < MIN_ADMIN_PASSWORD_LENGTH:
        return None
    if _sha256_hex(raw_pass) in _DISALLOWED_LEGACY_PASSWORD_SHA256:
        return None

    return (env_user, raw_pass)


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with 100,000 iterations."""
    if not salt:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000,
    )
    return f"{salt}${key.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain password against stored salt$hash."""
    try:
        salt, stored_hash = hashed_password.split("$", 1)
        key = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt.encode("utf-8"),
            100000,
        )
        return secrets.compare_digest(key.hex(), stored_hash)
    except Exception:
        return False


def authenticate_admin(username: str, password: str) -> bool:
    """Check username and verify password for administrative access.

    Order of evaluation (fail-closed):
    1. Database-backed operator admin accounts (`backend.operator_auth`).
       Unexpected database/service errors fail closed instead of falling open.
    2. Explicitly enabled and explicitly configured legacy environment credentials
       (`PYBIM_ALLOW_LEGACY_ADMIN=true` + safe `PYBIM_ADMIN_PASSWORD`).
    """
    if not isinstance(username, str) or not isinstance(password, str):
        return False
    if not username or not password:
        return False

    # 1. Check database-backed operator users (SEC-01).
    # If an account with this username exists in operator_users (active, disabled,
    # admin, or non-admin), it is authoritative and must never fall through to
    # legacy environment credentials.
    try:
        from backend.operator_auth import get_auth_service

        svc = get_auth_service()
        if svc.get_user_count() > 0:
            existing_user = svc.get_user_by_username(username)
            if existing_user is not None:
                user = svc.authenticate_user(username, password)
                return bool(user and user.is_admin)
    except HTTPException:
        raise
    except Exception:
        # Fail closed on unexpected auth service or database errors.
        return False

    # 2. Check explicitly enabled and securely configured legacy environment credentials
    legacy_creds = _get_legacy_admin_credentials()
    if legacy_creds is None:
        return False

    env_user, env_pass = legacy_creds
    user_match = _constant_time_equals(username, env_user)
    pass_match = _constant_time_equals(password, env_pass)
    return user_match and pass_match


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Generate a signed HS256 JWT access token using validated PYBIM_SECRET_KEY."""
    secret_key = get_jwt_secret_key()
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta is not None:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=_get_token_expire_minutes())

    to_encode.update(
        {
            "exp": expire,
            "iat": now,
        }
    )
    return jwt.encode(to_encode, secret_key, algorithm=ALGORITHM)


def verify_admin_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
) -> Dict[str, Any]:
    """FastAPI Dependency: verifies Bearer JWT token and ensures admin role."""
    if (
        not credentials
        or not credentials.credentials
        or credentials.scheme.lower() != "bearer"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please provide a valid Bearer JWT token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    secret_key = get_jwt_secret_key()
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["exp"]},
        )
        username = payload.get("sub")
        role = payload.get("role")
        if not isinstance(username, str) or not username.strip() or role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Administrative privileges required.",
            )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
