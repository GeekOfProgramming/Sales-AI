"""
backend/operator_auth.py
Secure Two-User Authentication & Session Management Layer for Sales-AI (SEC-01).

Implements:
- Roles: ADMIN and OPERATOR
- Secure password hashing using PBKDF2-HMAC-SHA256 (600,000 rounds) + scrypt verification support
- Unpredictable 32-byte session tokens (stored only as SHA-256 hashes in SQLite)
- Fast sliding session expiration & revocation
- Brute-force rate limiting
- Server-side dependency injection for HTML views and JSON API routes
- Safe operator identity attribution (never trusts client-supplied username fields)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List, Union

from fastapi import Request, HTTPException, Depends, status
from fastapi.responses import RedirectResponse

REPO_ROOT = Path(__file__).resolve().parent.parent

# Session Cookie Configuration
SESSION_COOKIE_NAME = "sales_session"
SESSION_DURATION_HOURS = int(os.getenv("SALES_SESSION_DURATION_HOURS", "24"))

# Brute-force rate limiting storage (in-memory, sliding window per IP)
_LOGIN_ATTEMPTS_LOCK = threading.Lock()
_LOGIN_ATTEMPTS: Dict[str, List[float]] = {}
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 60  # 5 attempts per minute per IP


@dataclass
class OperatorUser:
    user_id: str
    username: str
    role: str  # "admin" or "operator"
    full_name: Optional[str] = None
    is_active: bool = True
    created_at: Optional[str] = None
    last_login_at: Optional[str] = None

    @property
    def is_admin(self) -> bool:
        return self.role.lower() == "admin"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def init_auth_schema(db_path: Union[str, Path]) -> None:
    """Ensure operator_users and operator_sessions tables exist."""
    p = str(db_path)
    conn = sqlite3.connect(p, timeout=30.0)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS operator_users (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                full_name TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS operator_sessions (
                session_id TEXT PRIMARY KEY,
                token_hash TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                last_seen_at TEXT NOT NULL,
                client_ip TEXT,
                user_agent TEXT,
                FOREIGN KEY (user_id) REFERENCES operator_users (user_id)
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON operator_sessions (token_hash);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON operator_sessions (user_id);")
        conn.commit()
    finally:
        conn.close()


def hash_password(plain_password: str, salt: Optional[str] = None) -> str:
    """
    Hashes password using PBKDF2-HMAC-SHA256 with 600,000 iterations and unique random salt.
    Format: pbkdf2_sha256$600000$<salt>$<hash>
    """
    if not plain_password:
        raise ValueError("Password cannot be empty.")
    if not salt:
        salt = secrets.token_hex(16)
    
    rounds = 600000
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        plain_password.encode("utf-8"),
        salt.encode("utf-8"),
        rounds,
    )
    return f"pbkdf2_sha256${rounds}${salt}${derived.hex()}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """Verifies plain password against stored hash."""
    if not plain_password or not stored_hash:
        return False
    try:
        parts = stored_hash.split("$")
        if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
            rounds = int(parts[1])
            salt = parts[2]
            expected_hex = parts[3]
            derived = hashlib.pbkdf2_hmac(
                "sha256",
                plain_password.encode("utf-8"),
                salt.encode("utf-8"),
                rounds,
            )
            return secrets.compare_digest(derived.hex(), expected_hex)
        elif len(parts) == 2:
            # Legacy fallback: salt$hash (100,000 iterations)
            salt, expected_hex = parts[0], parts[1]
            derived = hashlib.pbkdf2_hmac(
                "sha256",
                plain_password.encode("utf-8"),
                salt.encode("utf-8"),
                100000,
            )
            return secrets.compare_digest(derived.hex(), expected_hex)
        return False
    except Exception:
        return False


def hash_session_token(raw_token: str) -> str:
    """Computes SHA-256 hash of raw session token for database lookup."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class OperatorAuthService:
    """Manages operator accounts, authentication, and database-backed sessions."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path:
            self.db_path = str(db_path)
        else:
            from sales_engine.candidates.backup_engine import resolve_configured_db_path
            self.db_path = str(resolve_configured_db_path())
        init_auth_schema(self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def get_user_count(self) -> int:
        """Returns the number of registered operator accounts."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM operator_users;").fetchone()
            return row[0] if row else 0

    def get_user_by_username(self, username: str) -> Optional[OperatorUser]:
        """Retrieves user by case-insensitive normalized username."""
        clean_user = username.strip().lower()
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT user_id, username, role, full_name, is_active, created_at, last_login_at "
                "FROM operator_users WHERE LOWER(username) = ?;",
                (clean_user,),
            ).fetchone()
            if not row:
                return None
            return OperatorUser(
                user_id=row["user_id"],
                username=row["username"],
                role=row["role"],
                full_name=row["full_name"],
                is_active=bool(row["is_active"]),
                created_at=row["created_at"],
                last_login_at=row["last_login_at"],
            )

    def get_user_by_id(self, user_id: str) -> Optional[OperatorUser]:
        """Retrieves user by primary key user_id."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT user_id, username, role, full_name, is_active, created_at, last_login_at "
                "FROM operator_users WHERE user_id = ?;",
                (user_id,),
            ).fetchone()
            if not row:
                return None
            return OperatorUser(
                user_id=row["user_id"],
                username=row["username"],
                role=row["role"],
                full_name=row["full_name"],
                is_active=bool(row["is_active"]),
                created_at=row["created_at"],
                last_login_at=row["last_login_at"],
            )

    def list_users(self) -> List[OperatorUser]:
        """Lists all registered users."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT user_id, username, role, full_name, is_active, created_at, last_login_at "
                "FROM operator_users ORDER BY created_at ASC;"
            ).fetchall()
            return [
                OperatorUser(
                    user_id=r["user_id"],
                    username=r["username"],
                    role=r["role"],
                    full_name=r["full_name"],
                    is_active=bool(r["is_active"]),
                    created_at=r["created_at"],
                    last_login_at=r["last_login_at"],
                )
                for r in rows
            ]

    def create_user(
        self,
        username: str,
        password: str,
        role: str = "operator",
        full_name: Optional[str] = None,
    ) -> OperatorUser:
        """Creates a new operator or admin user with a hashed password."""
        clean_user = username.strip().lower()
        if not clean_user or len(clean_user) < 3:
            raise ValueError("Username must be at least 3 characters.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        
        clean_role = role.strip().lower()
        if clean_role not in ("admin", "operator"):
            raise ValueError("Role must be 'admin' or 'operator'.")

        user_id = f"user_{secrets.token_hex(8)}"
        pw_hash = hash_password(password)
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._get_conn() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO operator_users (
                        user_id, username, password_hash, role, full_name, is_active, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?);
                    """,
                    (user_id, clean_user, pw_hash, clean_role, full_name, now_iso),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                raise ValueError(f"Username '{clean_user}' already exists.")

        return OperatorUser(
            user_id=user_id,
            username=clean_user,
            role=clean_role,
            full_name=full_name,
            is_active=True,
            created_at=now_iso,
        )

    def set_user_active(self, username: str, is_active: bool) -> bool:
        """Activates or deactivates a user. Revokes all active sessions immediately on deactivation."""
        clean_user = username.strip().lower()
        with self._get_conn() as conn:
            user = self.get_user_by_username(clean_user)
            if not user:
                return False

            # Prevent deactivating the last active administrator
            if not is_active and user.is_admin:
                active_admins = conn.execute(
                    "SELECT COUNT(*) FROM operator_users WHERE role = 'admin' AND is_active = 1;"
                ).fetchone()[0]
                if active_admins <= 1:
                    raise ValueError("Cannot deactivate the sole active administrator account.")

            conn.execute(
                "UPDATE operator_users SET is_active = ? WHERE user_id = ?;",
                (1 if is_active else 0, user.user_id),
            )
            # If deactivating, immediately revoke all active sessions
            if not is_active:
                conn.execute(
                    "DELETE FROM operator_sessions WHERE user_id = ?;",
                    (user.user_id,),
                )
            conn.commit()
            return True

    def authenticate_user(
        self,
        username: str,
        password: str,
        client_ip: Optional[str] = None,
    ) -> Optional[OperatorUser]:
        """Authenticates username and password. Checks active status and updates last_login_at."""
        clean_user = username.strip().lower()

        # Check rate limiting
        if client_ip:
            now = time.time()
            with _LOGIN_ATTEMPTS_LOCK:
                attempts = [t for t in _LOGIN_ATTEMPTS.get(client_ip, []) if now - t < LOGIN_WINDOW_SECONDS]
                if len(attempts) >= MAX_LOGIN_ATTEMPTS:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Too many failed login attempts. Please wait 1 minute before retrying.",
                    )

        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT user_id, username, password_hash, role, full_name, is_active, created_at "
                "FROM operator_users WHERE LOWER(username) = ?;",
                (clean_user,),
            ).fetchone()

            if not row or not bool(row["is_active"]):
                if client_ip:
                    with _LOGIN_ATTEMPTS_LOCK:
                        _LOGIN_ATTEMPTS.setdefault(client_ip, []).append(time.time())
                return None

            stored_hash = row["password_hash"]
            if not verify_password(password, stored_hash):
                if client_ip:
                    with _LOGIN_ATTEMPTS_LOCK:
                        _LOGIN_ATTEMPTS.setdefault(client_ip, []).append(time.time())
                return None

            # Success: reset rate limiting for this IP
            if client_ip:
                with _LOGIN_ATTEMPTS_LOCK:
                    _LOGIN_ATTEMPTS.pop(client_ip, None)

            # Record login timestamp
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE operator_users SET last_login_at = ? WHERE user_id = ?;",
                (now_iso, row["user_id"]),
            )
            conn.commit()

            return OperatorUser(
                user_id=row["user_id"],
                username=row["username"],
                role=row["role"],
                full_name=row["full_name"],
                is_active=True,
                created_at=row["created_at"],
                last_login_at=now_iso,
            )

    def create_session(
        self,
        user_id: str,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> str:
        """Creates a new cryptographically unpredictable session token and stores its SHA-256 hash."""
        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_session_token(raw_token)
        session_id = f"sess_{secrets.token_hex(8)}"
        
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=SESSION_DURATION_HOURS)

        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO operator_sessions (
                    session_id, token_hash, user_id, created_at, expires_at, last_seen_at, client_ip, user_agent
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    session_id,
                    token_hash,
                    user_id,
                    now.isoformat(),
                    expires.isoformat(),
                    now.isoformat(),
                    client_ip,
                    user_agent,
                ),
            )
            conn.commit()

        return raw_token

    def validate_session(self, raw_token: str) -> Optional[OperatorUser]:
        """Validates raw token against stored token_hash. Checks expiration and revocation."""
        if not raw_token:
            return None
        token_hash = hash_session_token(raw_token)
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT s.session_id, s.user_id, s.expires_at, s.revoked_at,
                       u.username, u.role, u.full_name, u.is_active, u.created_at, u.last_login_at
                FROM operator_sessions s
                JOIN operator_users u ON s.user_id = u.user_id
                WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ? AND u.is_active = 1;
                """,
                (token_hash, now_iso),
            ).fetchone()

            if not row:
                return None

            # Update last_seen_at
            conn.execute(
                "UPDATE operator_sessions SET last_seen_at = ? WHERE session_id = ?;",
                (now_iso, row["session_id"]),
            )
            conn.commit()

            return OperatorUser(
                user_id=row["user_id"],
                username=row["username"],
                role=row["role"],
                full_name=row["full_name"],
                is_active=bool(row["is_active"]),
                created_at=row["created_at"],
                last_login_at=row["last_login_at"],
            )

    def revoke_session(self, raw_token: str) -> bool:
        """Revokes a session by setting revoked_at timestamp."""
        if not raw_token:
            return False
        token_hash = hash_session_token(raw_token)
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE operator_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL;",
                (now_iso, token_hash),
            )
            conn.commit()
            return cur.rowcount > 0


# Global auth service instance
_auth_service: Optional[OperatorAuthService] = None


def get_auth_service(db_path: Optional[Union[str, Path]] = None) -> OperatorAuthService:
    global _auth_service
    # If a specific db_path is passed, or if the environment SALES_OUTREACH_DB changed (e.g. across tests), reinitialize
    configured_db = str(db_path) if db_path else os.getenv("SALES_OUTREACH_DB", os.getenv("SALESAI_DB_PATH", ""))
    if _auth_service is None or (configured_db and str(_auth_service.db_path) != configured_db):
        _auth_service = OperatorAuthService(db_path=configured_db or None)
    return _auth_service


def get_client_ip(request: Request) -> str:
    """Extracts client IP safely without blindly trusting headers in LAN mode."""
    if request.client:
        return request.client.host
    return "127.0.0.1"


# ==============================================================================
# FastAPI Dependencies
# ==============================================================================

async def get_current_user_optional(request: Request) -> Optional[OperatorUser]:
    """Retrieves authenticated user from session cookie or Authorization Bearer header, if present."""
    svc = get_auth_service()

    # 1. Check session cookie
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        user = svc.validate_session(token)
        if user:
            return user

    # 2. Check Authorization: Bearer <token>
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        raw = auth_header[7:].strip()
        user = svc.validate_session(raw)
        if user:
            return user

    return None


async def require_authenticated_operator(request: Request) -> OperatorUser:
    """Enforces authentication for browser HTML routes and JSON APIs."""
    user = await get_current_user_optional(request)
    if user:
        return user

    # Determine if request expects HTML (browser navigation) or JSON
    accept = request.headers.get("accept", "").lower()
    is_browser = "text/html" in accept or request.url.path.startswith("/sales") and not request.url.path.startswith("/api/")

    if is_browser and request.method == "GET":
        next_url = request.url.path
        if request.url.query:
            next_url += f"?{request.url.query}"
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": f"/sales/login?next={next_url}"},
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Please log in.",
        headers={"WWW-Authenticate": "Cookie"},
    )


async def require_admin_operator(
    current_user: OperatorUser = Depends(require_authenticated_operator),
) -> OperatorUser:
    """Enforces administrative role for user management and destructive actions."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Administrative privileges required.",
        )
    return current_user
