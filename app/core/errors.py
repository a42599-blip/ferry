"""錯誤碼集中管理。

所有可預期的失敗都必須是 PlatformError 的子類，上層才能一致處理。
（規格書第 21-2 章）
"""
from __future__ import annotations


class AppError(Exception):
    """本專案所有錯誤的根。"""

    code = "APP_ERROR"
    http_status = 400

    def __init__(self, message: str = "", *, detail: str | None = None):
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        self.detail = detail

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


# ── 使用者端錯誤（4xx）─────────────────────────────

class BadRequest(AppError):
    code = "BAD_REQUEST"
    http_status = 400


class UnsupportedUrl(AppError):
    """沒有任何平台認得這個網址。"""

    code = "UNSUPPORTED_URL"
    http_status = 400


class PlatformDisabled(AppError):
    """該平台被後台開關關閉。"""

    code = "PLATFORM_DISABLED"
    http_status = 403


class QuotaExceeded(AppError):
    """免費次數用完。"""

    code = "QUOTA_EXCEEDED"
    http_status = 429


class NotFound(AppError):
    code = "NOT_FOUND"
    http_status = 404


# ── 平台端錯誤（會記錄、但不一定是我們的錯）────────

class PlatformError(AppError):
    """平台解析失敗的根。各平台模組失敗一律丟這個（或其子類）。"""

    code = "PLATFORM_ERROR"
    http_status = 502

    def __init__(self, message: str = "", *, detail: str | None = None, platform: str = ""):
        super().__init__(message, detail=detail)
        self.platform = platform

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["platform"] = self.platform
        return d


class PlatformChanged(PlatformError):
    """平台改版（頁面結構／API 變了）→ 需要修該平台模組。"""

    code = "PLATFORM_CHANGED"


class PlatformBlocked(PlatformError):
    """被平台擋（403／驗證碼／地區限制）。"""

    code = "PLATFORM_BLOCKED"


class PlatformTimeout(PlatformError):
    """平台回應逾時。"""

    code = "PLATFORM_TIMEOUT"


class PlatformRateLimited(PlatformError):
    """被平台限流。"""

    code = "PLATFORM_RATE_LIMITED"
