"""身分判定（全站唯一取得「這是誰」的地方）。

- 全站只透過這裡的 `current_subject()` 取得身份 → 呼叫端不用改。
- 未登入＝裝置 ID；已登入＝`user:<會員ID>`（會員實作在 `members.py`）。
- 開關：`feature.auth`（關掉＝一律當未登入裝置）。
"""
from __future__ import annotations

from ..core.errors import AppError
from . import flags


class AuthRequired(AppError):
    code = "AUTH_REQUIRED"
    http_status = 401


def enabled() -> bool:
    return flags.feature_enabled("feature.auth")


def current_subject(request=None) -> str:
    """回傳「這次請求的代表者」＝會員ID（已登入）或裝置ID（未登入）。

    ⚠️ 這是全站唯一取得身份的地方（quota、歷史記錄都用它）。
    """
    if not enabled():
        return _device_id(request)

    # 已登入：Authorization: Bearer <member token> 或 x-member-token
    member_id = _member_from_request(request)
    if member_id:
        return f"user:{member_id}"
    return _device_id(request)


def current_member_id(request=None) -> str | None:
    """目前登入的會員 ID（沒登入回 None）。

    給「註銷帳號」這種需要知道是誰的端點用。
    """
    if not enabled():
        return _member_from_request(request)
    return _member_from_request(request)


def _member_from_request(request=None) -> str | None:
    if request is None:
        return None
    hdr = getattr(request, "headers", {}) or {}
    token = hdr.get("x-member-token") or ""
    auth = hdr.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        return None
    from . import members

    return members.verify_token(token)


def _device_id(request=None) -> str:
    if request is not None:
        hdr = getattr(request, "headers", {}) or {}
        dev = hdr.get("x-device-id") or hdr.get("X-Device-Id")
        if dev:
            return f"dev:{dev}"
        client = getattr(request, "client", None)
        if client is not None:
            return f"ip:{client.host}"
    return "dev:anonymous"

