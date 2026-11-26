# -*- coding: utf-8 -*-
"""Authentication for Web UI (PBKDF2 password + HttpOnly HMAC session cookie) and REST API (Bearer / X-API-Key)."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Dict, Optional

from fastapi import Cookie, Header, HTTPException, Query, Response, status

import init
from app.utils.bot_settings import get_setting, set_setting
from app.web.config import get_web_config

SESSION_COOKIE_NAME = "tg115_session"
SESSION_TTL_SECONDS = 30 * 86400
PBKDF2_ITERATIONS = 200_000

WEB_USERNAME_KEY = "web_username"
WEB_PASSWORD_HASH_KEY = "web_password_hash"
WEB_SESSION_SECRET_KEY = "web_session_secret"
WEB_API_KEY_KEY = "web_api_key"

_PLACEHOLDER_PASSWORDS = {"", "your_web_password", "change_me_password", "changeme"}
_PLACEHOLDER_API_KEYS = {"", "your_api_key", "your_web_api_key", "change_me_api_key"}


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt_bytes = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, PBKDF2_ITERATIONS)
    salt_b64 = base64.urlsafe_b64encode(salt_bytes).decode("ascii")
    hash_b64 = base64.urlsafe_b64encode(dk).decode("ascii")
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_b64}${hash_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not password or not stored_hash:
        return False
    try:
        scheme, iters_str, salt_b64, hash_b64 = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iters_str)
        salt_bytes = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def _get_or_create_session_secret() -> str:
    secret = get_setting(WEB_SESSION_SECRET_KEY)
    if not secret:
        secret = secrets.token_hex(32)
        set_setting(WEB_SESSION_SECRET_KEY, secret)
    return str(secret)


def get_web_username() -> str:
    stored = get_setting(WEB_USERNAME_KEY)
    if stored:
        return str(stored)
    return get_web_config()["username"]


def ensure_web_credentials() -> Dict[str, Optional[str]]:
    """
    Ensure username, PBKDF2 password hash, session secret, and API Key exist in SQLite.
    Returns metadata including any one-time auto-generated password for startup logging.
    """
    cfg = get_web_config()
    generated_password: Optional[str] = None

    username = get_setting(WEB_USERNAME_KEY)
    if not username:
        username = cfg["username"] or "admin"
        set_setting(WEB_USERNAME_KEY, username)

    pwd_hash = get_setting(WEB_PASSWORD_HASH_KEY)
    if not pwd_hash:
        raw_pwd = cfg["password"]
        if raw_pwd and raw_pwd.lower() not in _PLACEHOLDER_PASSWORDS:
            pwd_hash = hash_password(raw_pwd)
        else:
            generated_password = secrets.token_urlsafe(10)
            pwd_hash = hash_password(generated_password)
            if init.logger:
                init.logger.warn(
                    f"⚠️ 未在 config.yaml 配置 web.password，已为 Web 账号「{username}」自动生成初始密码: {generated_password}（登录后请在设置页修改）"
                )
        set_setting(WEB_PASSWORD_HASH_KEY, pwd_hash)

    _get_or_create_session_secret()

    api_key = get_setting(WEB_API_KEY_KEY)
    if not api_key:
        raw_key = cfg["api_key"]
        if raw_key and raw_key.lower() not in _PLACEHOLDER_API_KEYS:
            api_key = raw_key
        else:
            api_key = f"t115_{secrets.token_hex(20)}"
        set_setting(WEB_API_KEY_KEY, api_key)

    return {
        "username": str(username),
        "api_key": str(api_key),
        "generated_password": generated_password,
    }


def get_api_key() -> str:
    ensure_web_credentials()
    return str(get_setting(WEB_API_KEY_KEY) or "")


def rotate_api_key() -> str:
    ensure_web_credentials()
    new_key = f"t115_{secrets.token_hex(20)}"
    set_setting(WEB_API_KEY_KEY, new_key)
    return new_key


def update_web_password(new_password: str, new_username: Optional[str] = None) -> None:
    if not new_password or len(new_password) < 6:
        raise ValueError("新密码长度不能少于 6 位")
    ensure_web_credentials()
    if new_username is not None and new_username.strip():
        set_setting(WEB_USERNAME_KEY, new_username.strip())
    set_setting(WEB_PASSWORD_HASH_KEY, hash_password(new_password))
    # Rotate session secret to invalidate stale sessions if desired, or keep current user re-issued
    set_setting(WEB_SESSION_SECRET_KEY, secrets.token_hex(32))


def authenticate_user(username: str, password: str) -> bool:
    ensure_web_credentials()
    expected_user = get_web_username()
    stored_hash = get_setting(WEB_PASSWORD_HASH_KEY) or ""
    if not hmac.compare_digest(str(username or "").strip(), expected_user):
        return False
    return verify_password(password, str(stored_hash))


def create_session_token(username: str) -> str:
    now = int(time.time())
    payload = {"u": username, "iat": now, "exp": now + SESSION_TTL_SECONDS}
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    secret = _get_or_create_session_secret().encode("utf-8")
    sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def verify_session_token(token: Optional[str]) -> Optional[Dict]:
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        secret = _get_or_create_session_secret().encode("utf-8")
        expected_sig = _b64url_encode(hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig_b64, expected_sig):
            return None
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        if not payload.get("u"):
            return None
        return payload
    except Exception:
        return None


def set_session_cookie(response: Response, username: str) -> str:
    token = create_session_token(username)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return token


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def _verify_credentials(
    session_cookie: Optional[str],
    x_api_key: Optional[str],
    authorization: Optional[str],
    query_api_key: Optional[str] = None,
) -> Dict:
    ensure_web_credentials()
    expected_api_key = get_api_key()

    # 1. X-API-Key header
    if x_api_key and expected_api_key and hmac.compare_digest(x_api_key.strip(), expected_api_key):
        return {"username": get_web_username(), "auth_type": "api_key"}

    # 2. Authorization: Bearer <api_key>
    if authorization:
        parts = authorization.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            bearer = parts[1].strip()
            if expected_api_key and hmac.compare_digest(bearer, expected_api_key):
                return {"username": get_web_username(), "auth_type": "api_key"}
            # Also accept a valid session token as Bearer if needed
            session_payload = verify_session_token(bearer)
            if session_payload:
                return {"username": session_payload["u"], "auth_type": "session"}

    # 3. Query param api_key (SSE EventSource fallback for external clients)
    if query_api_key and expected_api_key and hmac.compare_digest(query_api_key.strip(), expected_api_key):
        return {"username": get_web_username(), "auth_type": "api_key"}

    # 4. HttpOnly Session Cookie
    session_payload = verify_session_token(session_cookie)
    if session_payload:
        return {"username": session_payload["u"], "auth_type": "session"}

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未登录或 API Key 无效",
    )


def require_auth(
    tg115_session: Optional[str] = Cookie(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> Dict:
    return _verify_credentials(tg115_session, x_api_key, authorization)


def require_auth_sse(
    tg115_session: Optional[str] = Cookie(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    api_key: Optional[str] = Query(default=None),
) -> Dict:
    return _verify_credentials(tg115_session, x_api_key, authorization, query_api_key=api_key)
