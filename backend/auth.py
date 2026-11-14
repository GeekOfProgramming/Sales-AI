"""Authentication & JWT Security Layer for pyBIM-LLM.
Protects sensitive administrative endpoints (ingestion approvals, vector store manipulation)
against unverified access and Data Poisoning.
"""

import os
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# Security configuration
# In production, PYBIM_SECRET_KEY should be set explicitly. If absent, generate a random ephemeral key.
# For backward-compatible tests, honor PYBIM_SECRET_KEY or fallback to a secured runtime secret.
SECRET_KEY = os.getenv("PYBIM_SECRET_KEY") or secrets.token_hex(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("PYBIM_TOKEN_EXPIRE_MINUTES", "1440"))  # 24 Hours

# Configurable Admin Credentials (Environment Variable Driven)
ADMIN_USERNAME = os.getenv("PYBIM_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("PYBIM_ADMIN_PASSWORD", "pybim-admin2026")

security_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with 100,000 iterations."""
    if not salt:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    )
    return f"{salt}${key.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain password against stored salt$hash."""
    try:
        salt, stored_hash = hashed_password.split('$', 1)
        key = hashlib.pbkdf2_hmac(
            'sha256',
            plain_password.encode('utf-8'),
            salt.encode('utf-8'),
            100000
        )
        return secrets.compare_digest(key.hex(), stored_hash)
    except Exception:
        return False


def authenticate_admin(username: str, password: str) -> bool:
    """Check username and verify password for administrative access."""
    # 1. First check database-backed operator users (SEC-01)
    try:
        from backend.operator_auth import get_auth_service
        svc = get_auth_service()
        # Only if database has configured users
        if svc.get_user_count() > 0:
            user = svc.authenticate_user(username, password)
            if user and user.is_admin:
                return True
            # If database users exist, do not fall back to default password
            if not os.getenv("PYBIM_ALLOW_LEGACY_ADMIN") and not os.getenv("PYBIM_ADMIN_PASSWORD"):
                return False
    except Exception:
        pass

    # 2. Check environment or legacy test fallback
    env_user = os.getenv("PYBIM_ADMIN_USER", ADMIN_USERNAME)
    env_pass = os.getenv("PYBIM_ADMIN_PASSWORD", ADMIN_PASSWORD)
    if env_user and env_pass and username == env_user:
        return verify_password(password, hash_password(env_pass))

    return False


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Generate a signed JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    })
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_admin_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer)) -> Dict[str, Any]:
    """FastAPI Dependency: verifies Bearer JWT token and ensures admin role."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please provide a valid Bearer JWT token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: Optional[str] = payload.get("sub")
        role: Optional[str] = payload.get("role")
        if not username or role != "admin":
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
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
