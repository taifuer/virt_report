"""运行指标访问控制。密钥只来自环境变量。"""
from __future__ import annotations

import hashlib
import hmac
import time
from http.cookies import SimpleCookie
from typing import Mapping

COOKIE_NAME = "virt_metrics_session"


def issue_session(access_key: str, ttl_hours: int, now: int | None = None) -> str:
    expires = (int(time.time()) if now is None else now) + max(1, ttl_hours) * 3600
    payload = str(expires)
    signature = hmac.new(access_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_session(token: str, access_key: str, now: int | None = None) -> bool:
    try:
        expires_text, signature = token.split(".", 1)
        expires = int(expires_text)
    except (AttributeError, TypeError, ValueError):
        return False
    current = int(time.time()) if now is None else now
    expected = hmac.new(access_key.encode(), expires_text.encode(), hashlib.sha256).hexdigest()
    return expires >= current and hmac.compare_digest(signature, expected)


def is_authorized(headers: Mapping[str, str], access_key: str | None,
                  now: int | None = None) -> bool:
    if not access_key:
        return False
    authorization = headers.get("Authorization", "")
    if authorization.startswith("Bearer ") and hmac.compare_digest(
            authorization[7:].strip(), access_key):
        return True
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("Cookie", ""))
    except Exception:
        return False
    morsel = cookie.get(COOKIE_NAME)
    return bool(morsel and verify_session(morsel.value, access_key, now))
