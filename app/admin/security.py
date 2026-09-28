"""後台安全：登入權杖（HMAC）＋ 兩步驟驗證（TOTP，RFC 6238）。

（規格書 10-4：後台需登入 ＋ 兩步驟驗證）

設計：
- 金鑰存在資料庫（`settings.admin_secret`），第一次自動產生 → 重啟不會失效。
- 密碼來自環境變數 `ADMIN_PASSWORD`（不寫死在程式）。
- TOTP（Google Authenticator）**預設關閉**：小羅若要用，在後台「系統」頁按啟用，
  複製 otpauth 連結到 App 掃描即可 → 不用 AI 幫他做任何設定。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time

from ..core import db
from ..core.config import settings

_TOKEN_TTL = settings.admin_token_ttl


def _secret() -> bytes:
    s = db.get_setting("admin_secret")
    if not s:
        s = secrets.token_hex(32)
        db.set_setting("admin_secret", s)
    return s.encode()


# ── 登入權杖 ───────────────────────────────────────────
def issue_token(user: str, *, ttl: int | None = None) -> str:
    payload = {
        "u": user,
        "iat": int(time.time()),
        "exp": int(time.time()) + (ttl or _TOKEN_TTL),
        "n": secrets.token_hex(4),
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def verify_token(token: str) -> dict | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    want = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, want):
        return None
    try:
        pad = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad))
    except Exception:  # noqa: BLE001
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    return payload


# ── 密碼 ──────────────────────────────────────────
# 小羅 2026-09-29：「你幫我改成帳號密碼都用 admin」；
#                 「前後台你也都沒有給我設計改帳密的方法或按鈕和邏輯呀」
# → 帳密改成「存在資料庫」（可用後台修改），環境變數只當「第一次的預設值」。
def _hash_password(pw: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000)
    return f"{salt}${h.hex()}"


def _verify_password(pw: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, want = stored.split("$", 1)
    return hmac.compare_digest(_hash_password(pw, salt), stored)


def current_user() -> str:
    """目前的管理員帳號（後台改過就優先用 DB 的）。"""
    return db.get_setting("admin_user") or settings.admin_user


def check_password(user: str, password: str) -> bool:
    """驗帳密：① 後台改過（存 DB）優先　② 還沒改過 → 用環境變數。"""
    stored_pw = db.get_setting("admin_password")
    if stored_pw:
        return (hmac.compare_digest(user or "", current_user())
                and _verify_password(password or "", stored_pw))
    ok_user = hmac.compare_digest(user or "", settings.admin_user)
    ok_pass = hmac.compare_digest(password or "", settings.admin_password)
    return ok_user and ok_pass


def set_account(user: str, password: str) -> None:
    """改後台帳密（存 DB）——改完會換掉 token 金鑰，讓舊登入全部失效。"""
    user = (user or "").strip()
    if not user:
        raise ValueError("帳號不能空白")
    if len(password or "") < 4:
        raise ValueError("密碼至少 4 個字")
    db.set_setting("admin_user", user)
    db.set_setting("admin_password", _hash_password(password))
    db.set_setting("admin_secret", secrets.token_hex(32))   # 強制重新登入


# ── TOTP（兩步驟驗證）──────────────────────────────────
def totp_enabled() -> bool:
    return bool(db.get_setting("admin_totp_secret"))


def ensure_totp_secret() -> str:
    s = db.get_setting("admin_totp_secret")
    if not s:
        s = base64.b32encode(os.urandom(20)).decode().rstrip("=")
        db.set_setting("admin_totp_secret", s)
    return s


def disable_totp() -> None:
    db.set_setting("admin_totp_secret", "")


def _hotp(key: bytes, counter: int) -> str:
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1000000:06d}"


def totp_now(secret: str | None = None, *, step: int = 30) -> str:
    secret = secret or ensure_totp_secret()
    pad = "=" * (-len(secret) % 8)
    key = base64.b32decode(secret.upper() + pad)
    return _hotp(key, int(time.time()) // step)


def totp_verify(code: str, *, window: int = 1) -> bool:
    if not totp_enabled():
        return True                      # 未啟用 → 不驗
    code = (code or "").strip()
    if not code.isdigit():
        return False
    for drift in range(-window, window + 1):
        if hmac.compare_digest(code, totp_now(step=30) ) and drift == 0:
            return True
        secret = db.get_setting("admin_totp_secret")
        pad = "=" * (-len(secret) % 8)
        key = base64.b32decode(secret.upper() + pad)
        if hmac.compare_digest(code, _hotp(key, int(time.time()) // 30 + drift)):
            return True
    return False


def otpauth_url(account: str = "admin") -> str:
    secret = ensure_totp_secret()
    label = f"ferry:{account}"
    return (
        f"otpauth://totp/{label}?secret={secret}"
        f"&issuer=ferry&period=30&digits=6"
    )
