"""Isolated security & configuration contract tests for ARCH-01C (`backend/auth.py`).

Verifies fail-closed authentication configuration, JWT verification hardening,
operator authentication independence, and import-time isolation without
touching operational databases, real credentials, or external providers.
"""

from __future__ import annotations

import ast
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import backend.auth as auth_mod
import backend.operator_auth as operator_auth_mod
from tests.support.isolated_subprocess import run_isolated_subprocess

SYNTH_SECRET_A = "synth-jwt-signing-key-9f4e2a7c1b8d6035ab12cd34ef5678901234abcd56"
SYNTH_SECRET_B = "synth-jwt-other-key-7d3b9e1a4c6f8021fe65dc43ba2109876543efab1234"
SYNTH_PASSWORD = "Synth-Admin-Pass-2026!#89"

_AUTH_ENV_KEYS = (
    "PYBIM_SECRET_KEY",
    "PYBIM_TOKEN_EXPIRE_MINUTES",
    "PYBIM_ADMIN_USER",
    "PYBIM_ADMIN_PASSWORD",
    "PYBIM_ALLOW_LEGACY_ADMIN",
)


@pytest.fixture
def isolated_auth_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate DB paths and clear all PYBIM_* auth env variables for each test."""
    db_file = tmp_path / "isolated_auth_contract.db"
    monkeypatch.setenv("SALES_OUTREACH_DB", str(db_file))
    monkeypatch.setenv("SALESAI_DB_PATH", str(db_file))
    monkeypatch.setenv("SALESAI_CANDIDATES_DB", str(db_file))
    for key in _AUTH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    orig_service = operator_auth_mod._auth_service
    operator_auth_mod._auth_service = None
    try:
        yield db_file
    finally:
        operator_auth_mod._auth_service = orig_service


def test_import_isolation_no_ephemeral_secret_and_no_db_init(tmp_path: Path) -> None:
    """Importing `backend.auth` in a scrubbed subprocess creates no DB and no ephemeral secret."""
    db_path = tmp_path / "should_not_exist.db"
    probe = (
        "import json, os, sys; "
        "sys.path.insert(0, os.getcwd()); "
        "import backend.auth as m; "
        "print(json.dumps({"
        "'secret_key': m.SECRET_KEY, "
        "'admin_password': m.ADMIN_PASSWORD, "
        "'admin_username': m.ADMIN_USERNAME, "
        "'legacy_enabled': m.is_legacy_admin_enabled()"
        "}))"
    )
    proc = run_isolated_subprocess(
        [sys.executable, "-c", probe],
        isolated_db_path=db_path,
        trusted_dir=tmp_path,
        extra_env={**{k: "" for k in _AUTH_ENV_KEYS}, "PYBIM_TOKEN_EXPIRE_MINUTES": "not_an_int"},
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout.strip())
    assert payload["secret_key"] is None
    assert payload["admin_password"] is None
    assert payload["admin_username"] is None
    assert payload["legacy_enabled"] is False
    assert not db_path.exists()

    # Source static guard: no ephemeral token_hex fallback on SECRET_KEY
    auth_src = (REPO_ROOT / "backend" / "auth.py").read_text(encoding="utf-8")
    tree = ast.parse(auth_src, filename="backend/auth.py")
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "SECRET_KEY":
            assert isinstance(node.value, ast.Constant) and node.value.value is None
    assert "c106cf816f90bce7060f16a700359e7346a4d4f48f932d109ba189df1a5b514f" in auth_mod._DISALLOWED_LEGACY_PASSWORD_SHA256


def test_legacy_admin_disabled_and_invalid_password_configurations_fail_closed(
    isolated_auth_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing, empty, weak, disallowed, or disabled legacy admin configurations all fail closed."""
    # 1. Completely unconfigured -> fails closed
    assert auth_mod.is_legacy_admin_enabled() is False
    assert auth_mod.authenticate_admin("admin", SYNTH_PASSWORD) is False
    assert auth_mod.authenticate_admin("admin", "changeme123") is False

    # 2. Password configured, but PYBIM_ALLOW_LEGACY_ADMIN unset -> disabled by default
    monkeypatch.setenv("PYBIM_ADMIN_USER", "ops_legacy")
    monkeypatch.setenv("PYBIM_ADMIN_PASSWORD", SYNTH_PASSWORD)
    assert auth_mod.authenticate_admin("ops_legacy", SYNTH_PASSWORD) is False

    # 3. Explicitly disabled or malformed PYBIM_ALLOW_LEGACY_ADMIN values cannot be bypassed
    for flag_val in ("", "   ", "0", "false", "FALSE", "no", "off", "maybe", "invalid"):
        monkeypatch.setenv("PYBIM_ALLOW_LEGACY_ADMIN", flag_val)
        assert auth_mod.is_legacy_admin_enabled() is False
        assert auth_mod.authenticate_admin("ops_legacy", SYNTH_PASSWORD) is False

    # 4. Explicitly enabled, but missing / empty / short / known placeholder password -> fail closed
    monkeypatch.setenv("PYBIM_ALLOW_LEGACY_ADMIN", "true")
    monkeypatch.delenv("PYBIM_ADMIN_PASSWORD", raising=False)
    assert auth_mod.authenticate_admin("ops_legacy", SYNTH_PASSWORD) is False

    for bad_pw in ("", "        ", "short7", "password", "password123", "admin123", "changeme", "changeme123", "12345678"):
        monkeypatch.setenv("PYBIM_ADMIN_PASSWORD", bad_pw)
        assert auth_mod.authenticate_admin("ops_legacy", bad_pw) is False

    # 5. Explicitly empty username fails closed even with valid password
    monkeypatch.setenv("PYBIM_ADMIN_PASSWORD", SYNTH_PASSWORD)
    monkeypatch.setenv("PYBIM_ADMIN_USER", "   ")
    assert auth_mod.authenticate_admin("", SYNTH_PASSWORD) is False
    assert auth_mod.authenticate_admin("admin", SYNTH_PASSWORD) is False


def test_legacy_admin_explicitly_enabled_with_valid_synthetic_credentials(
    isolated_auth_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicitly enabled legacy admin with strong synthetic credentials succeeds and rejects wrong credentials."""
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("PYBIM_ALLOW_LEGACY_ADMIN", truthy)
        monkeypatch.setenv("PYBIM_ADMIN_USER", "legacy_admin_user")
        monkeypatch.setenv("PYBIM_ADMIN_PASSWORD", SYNTH_PASSWORD)
        assert auth_mod.is_legacy_admin_enabled() is True
        assert auth_mod.authenticate_admin("legacy_admin_user", SYNTH_PASSWORD) is True
        assert auth_mod.authenticate_admin("legacy_admin_user", "Wrong-Pass-2026!") is False
        assert auth_mod.authenticate_admin("other_user", SYNTH_PASSWORD) is False

    # When PYBIM_ADMIN_USER is unset, standard username "admin" remains supported with explicit strong password
    monkeypatch.delenv("PYBIM_ADMIN_USER", raising=False)
    assert auth_mod.authenticate_admin("admin", SYNTH_PASSWORD) is True


def test_jwt_signing_key_and_expiration_configuration_fail_closed(
    isolated_auth_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing, weak, placeholder signing keys and invalid expiration configs fail closed without secret leakage."""
    # Missing signing key -> 503 AuthConfigurationError
    with pytest.raises(auth_mod.AuthConfigurationError) as exc_missing:
        auth_mod.create_access_token({"sub": "admin", "role": "admin"})
    assert exc_missing.value.status_code == 503

    # Unsafe signing keys rejected without leaking candidate value in error detail
    bad_secrets = (
        "",
        "   ",
        "too-short-secret",
        "a" * 40,
        " " + SYNTH_SECRET_A,
        "default-secret-key-do-not-use-in-prod-1234",
        "pybim-secret-fallback-0123456789abcdef99",
        "changeme-0123456789abcdef0123456789abcdef",
    )
    for bad_secret in bad_secrets:
        monkeypatch.setenv("PYBIM_SECRET_KEY", bad_secret)
        with pytest.raises(auth_mod.AuthConfigurationError) as exc_bad:
            auth_mod.create_access_token({"sub": "admin", "role": "admin"})
        assert exc_bad.value.status_code == 503
        if bad_secret.strip():
            assert bad_secret.strip() not in exc_bad.value.detail

    # Valid secret key with invalid PYBIM_TOKEN_EXPIRE_MINUTES -> fail closed
    monkeypatch.setenv("PYBIM_SECRET_KEY", SYNTH_SECRET_A)
    for bad_exp in ("", "abc", "0", "-15", "999999"):
        monkeypatch.setenv("PYBIM_TOKEN_EXPIRE_MINUTES", bad_exp)
        with pytest.raises(auth_mod.AuthConfigurationError) as exc_exp:
            auth_mod.create_access_token({"sub": "admin", "role": "admin"})
        assert exc_exp.value.status_code == 503
        if bad_exp:
            assert bad_exp not in exc_exp.value.detail

    # Valid PYBIM_TOKEN_EXPIRE_MINUTES honored
    monkeypatch.setenv("PYBIM_TOKEN_EXPIRE_MINUTES", "45")
    token = auth_mod.create_access_token({"sub": "admin", "role": "admin"})
    decoded = jwt.decode(token, SYNTH_SECRET_A, algorithms=["HS256"])
    delta_seconds = decoded["exp"] - decoded["iat"]
    assert 44 * 60 <= delta_seconds <= 46 * 60


def test_jwt_verification_security_boundaries(
    isolated_auth_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify valid JWT acceptance and rejection of invalid, expired, malformed, unsigned, or fallback tokens."""
    monkeypatch.setenv("PYBIM_SECRET_KEY", SYNTH_SECRET_A)

    # 1. Valid admin token
    valid_token = auth_mod.create_access_token({"sub": "ops_admin", "role": "admin"})
    payload = auth_mod.verify_admin_token(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=valid_token)
    )
    assert payload["sub"] == "ops_admin"
    assert payload["role"] == "admin"

    # 2. Invalid signature (signed with different key)
    wrong_sig_token = jwt.encode(
        {"sub": "ops_admin", "role": "admin", "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        SYNTH_SECRET_B,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc_sig:
        auth_mod.verify_admin_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials=wrong_sig_token))
    assert exc_sig.value.status_code == 401
    assert SYNTH_SECRET_A not in exc_sig.value.detail
    assert SYNTH_SECRET_B not in exc_sig.value.detail

    # 3. Token signed with an insecure fallback secret must not authenticate
    fallback_secret = "default-secret-key-fallback-1234567890"
    fallback_token = jwt.encode(
        {"sub": "ops_admin", "role": "admin", "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        fallback_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc_fb:
        auth_mod.verify_admin_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials=fallback_token))
    assert exc_fb.value.status_code == 401

    # Also fails closed with 503 if server has no valid signing key configured
    monkeypatch.delenv("PYBIM_SECRET_KEY", raising=False)
    with pytest.raises(auth_mod.AuthConfigurationError) as exc_unconf:
        auth_mod.verify_admin_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials=fallback_token))
    assert exc_unconf.value.status_code == 503
    monkeypatch.setenv("PYBIM_SECRET_KEY", SYNTH_SECRET_A)

    # 4. Expired token
    expired_token = auth_mod.create_access_token(
        {"sub": "ops_admin", "role": "admin"},
        expires_delta=timedelta(seconds=-10),
    )
    with pytest.raises(HTTPException) as exc_exp:
        auth_mod.verify_admin_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials=expired_token))
    assert exc_exp.value.status_code == 401
    assert "expired" in exc_exp.value.detail.lower()

    # 5. Token missing `exp` claim rejected
    no_exp_token = jwt.encode({"sub": "ops_admin", "role": "admin"}, SYNTH_SECRET_A, algorithm="HS256")
    with pytest.raises(HTTPException) as exc_no_exp:
        auth_mod.verify_admin_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials=no_exp_token))
    assert exc_no_exp.value.status_code == 401

    # 6. Malformed tokens & wrong auth scheme
    for bad_token in ("not-a-jwt", "a.b", ""):
        with pytest.raises(HTTPException) as exc_mal:
            auth_mod.verify_admin_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials=bad_token))
        assert exc_mal.value.status_code == 401

    with pytest.raises(HTTPException) as exc_scheme:
        auth_mod.verify_admin_token(HTTPAuthorizationCredentials(scheme="Basic", credentials=valid_token))
    assert exc_scheme.value.status_code == 401

    # 7. Unsupported algorithms: HS512 and alg=none
    hs512_token = jwt.encode(
        {"sub": "ops_admin", "role": "admin", "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        SYNTH_SECRET_A,
        algorithm="HS512",
    )
    with pytest.raises(HTTPException) as exc_alg:
        auth_mod.verify_admin_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials=hs512_token))
    assert exc_alg.value.status_code == 401

    alg_none_header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode("ascii")
    alg_none_payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "ops_admin", "role": "admin", "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp())}).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    alg_none_token = f"{alg_none_header}.{alg_none_payload}."
    with pytest.raises(HTTPException) as exc_none:
        auth_mod.verify_admin_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials=alg_none_token))
    assert exc_none.value.status_code == 401

    # 8. Valid signature but non-admin role or empty subject -> 403 Forbidden
    op_token = auth_mod.create_access_token({"sub": "bob_operator", "role": "operator"})
    with pytest.raises(HTTPException) as exc_role:
        auth_mod.verify_admin_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials=op_token))
    assert exc_role.value.status_code == 403

    empty_sub_token = auth_mod.create_access_token({"sub": "   ", "role": "admin"})
    with pytest.raises(HTTPException) as exc_sub:
        auth_mod.verify_admin_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials=empty_sub_token))
    assert exc_sub.value.status_code == 403


def test_operator_auth_preserved_independently_and_db_errors_fail_closed(
    isolated_auth_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator accounts work without PYBIM_* vars; DB admin works with disabled legacy; DB errors fail closed."""
    svc = operator_auth_mod.get_auth_service(db_path=isolated_auth_env)
    svc.create_user("alice_admin", "Alice-Synth-Pass-2026!", role="admin", full_name="Alice Admin")
    svc.create_user("bob_operator", "Bob-Synth-Pass-2026!", role="operator", full_name="Bob Operator")

    # 1. Operator session flow works with zero PYBIM_* env vars configured
    bob = svc.authenticate_user("bob_operator", "Bob-Synth-Pass-2026!")
    assert bob is not None and bob.is_admin is False
    sess_token = svc.create_session(bob.user_id)
    validated = svc.validate_session(sess_token)
    assert validated is not None and validated.username == "bob_operator"
    assert svc.revoke_session(sess_token) is True
    assert svc.validate_session(sess_token) is None

    # 2. DB-backed admin succeeds in authenticate_admin even with legacy admin disabled
    assert auth_mod.is_legacy_admin_enabled() is False
    assert auth_mod.authenticate_admin("alice_admin", "Alice-Synth-Pass-2026!") is True
    # Non-admin operator is rejected for admin auth
    assert auth_mod.authenticate_admin("bob_operator", "Bob-Synth-Pass-2026!") is False

    # 3. DB-backed usernames are authoritative: wrong password, disabled account,
    # or non-admin role must NEVER fall through to explicitly enabled legacy credentials.
    monkeypatch.setenv("PYBIM_ALLOW_LEGACY_ADMIN", "true")
    monkeypatch.setenv("PYBIM_ADMIN_PASSWORD", SYNTH_PASSWORD)

    # 3a. Active DB admin with wrong password matching PYBIM_ADMIN_PASSWORD -> rejected
    monkeypatch.setenv("PYBIM_ADMIN_USER", "alice_admin")
    assert auth_mod.authenticate_admin("alice_admin", SYNTH_PASSWORD) is False
    assert auth_mod.authenticate_admin("ALICE_ADMIN", SYNTH_PASSWORD) is False

    # 3b. Non-admin DB operator whose username collides with PYBIM_ADMIN_USER -> rejected
    monkeypatch.setenv("PYBIM_ADMIN_USER", "bob_operator")
    assert auth_mod.authenticate_admin("bob_operator", SYNTH_PASSWORD) is False
    assert auth_mod.authenticate_admin("bob_operator", "Bob-Synth-Pass-2026!") is False

    # 3c. Deactivated DB admin whose username collides with PYBIM_ADMIN_USER -> rejected
    svc.create_user("carol_admin", "Carol-Synth-Pass-2026!", role="admin")
    assert svc.set_user_active("carol_admin", is_active=False) is True
    monkeypatch.setenv("PYBIM_ADMIN_USER", "carol_admin")
    assert auth_mod.authenticate_admin("carol_admin", SYNTH_PASSWORD) is False
    assert auth_mod.authenticate_admin("carol_admin", "Carol-Synth-Pass-2026!") is False

    # 3d. Distinct non-colliding legacy admin username succeeds only when explicitly enabled
    monkeypatch.setenv("PYBIM_ADMIN_USER", "breakglass_legacy")
    assert auth_mod.authenticate_admin("breakglass_legacy", SYNTH_PASSWORD) is True

    # 4. Disabling legacy admin stops new legacy logins while stateless JWT verification
    # remains governed by PYBIM_SECRET_KEY and token expiration.
    monkeypatch.setenv("PYBIM_SECRET_KEY", SYNTH_SECRET_A)
    issued_token = auth_mod.create_access_token({"sub": "breakglass_legacy", "role": "admin"})
    monkeypatch.setenv("PYBIM_ALLOW_LEGACY_ADMIN", "false")
    assert auth_mod.authenticate_admin("breakglass_legacy", SYNTH_PASSWORD) is False
    verified = auth_mod.verify_admin_token(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=issued_token)
    )
    assert verified["sub"] == "breakglass_legacy"
    assert verified["role"] == "admin"

    # 5. Unexpected auth service error fails closed (never falls through to legacy env credentials)
    monkeypatch.setenv("PYBIM_ALLOW_LEGACY_ADMIN", "true")
    monkeypatch.setenv("PYBIM_ADMIN_USER", "fallback_admin")
    monkeypatch.setenv("PYBIM_ADMIN_PASSWORD", SYNTH_PASSWORD)
    with patch("backend.operator_auth.get_auth_service", side_effect=RuntimeError("simulated db unavailable")):
        assert auth_mod.authenticate_admin("fallback_admin", SYNTH_PASSWORD) is False
