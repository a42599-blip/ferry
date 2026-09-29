"""錯誤碼集中管理。

所有可預期的失敗都必須是 PlatformError 的子類，上層才能一致處理。
（規格書第 21-2 章）
"""
from __future__ import annotations


class AppError(Exception):
    """本專案所有錯誤的根。"""

    code = "APP_ERROR"
    http_status = 400

    def __init__(self, message: str = "", *, detail: str | None = None,
                 code: str | None = None):
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        self.detail = detail
        if code:                       # 允許個別錯誤給更精確的碼（前端才能翻譯）
            self.code = code

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


class AdRequired(AppError):
    """該看廣告了（不是用完）。

    小羅 2026-09-29：「只要他願意看廣告，就永遠再給他次數，
    **永遠不要告訴他今天次數用完了請等明天** —— 他看得越多我越賺錢。」
    → 廣告開關有開的等級，次數到頂時一律回這個（前端據此跳廣告），只有真的
      「廣告關掉」或「不屬於要看廣告的等級」才會回 QUOTA_EXCEEDED。
    """

    code = "AD_REQUIRED"
    http_status = 428


class NotFound(AppError):
    code = "NOT_FOUND"
    http_status = 404


# ── 平台端錯誤（會記錄、但不一定是我們的錯）────────

class PlatformError(AppError):
    """平台解析失敗的根。各平台模組失敗一律丟這個（或其子類）。

    ⚠️ 狀態碼**不可用 5xx**（2026-09-27 實際踩到）：
       本站前面有 Cloudflare，它會攔截 origin 的 5xx，換成自己的
       純文字錯誤頁（`error code: 502`）→ 使用者看不到我們的說明，
       前端也 parsing 不到 JSON。
       改用 422（語意也對：平台回得出東西，只是我們處理不了）。
    """

    code = "PLATFORM_ERROR"
    http_status = 422

    def __init__(self, message: str = "", *, detail: str | None = None, platform: str = "",
                 code: str | None = None):
        super().__init__(message, detail=detail, code=code)
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
