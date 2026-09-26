"""免費次數（quota）— 每天歸零，下載與傳輸各自獨立。

規則（2026-09-26 小羅定案）：
- 每日 **5 次**（下載 5、傳輸 5，兩個獨立額度）
- 以「使用者當地時間 00:00」重置
- 開發期間 `FREE_LIMIT_ENABLED=false`（不擋，方便測試）

⚠️ 本模組是「預留接口」的一部分：真正的計數要接資料庫／Redis，
   現在用記憶體，呼叫端不用改。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..core.config import settings
from ..core.errors import QuotaExceeded

# kind: "download" | "transfer"
_KINDS = ("download", "transfer")

# key: (kind, subject, date_key) -> count     subject＝裝置ID 或 會員ID 或 IP
_counts: dict[tuple[str, str, str], int] = {}


def _date_key(tz_name: str = "Asia/Taipei", now: datetime | None = None) -> str:
    """依使用者時區算「當地日期」字串（YYYY-MM-DD）。

    正式版由 Cloudflare 提供時區；這裡先支援傳入，取不到就 fallback 台北。
    """
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone(timedelta(hours=8))
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    return now.astimezone(tz).strftime("%Y-%m-%d")


def daily_limit(kind: str) -> int:
    return settings.free_transfer_per_day if kind == "transfer" else settings.free_download_per_day


def used(kind: str, subject: str, *, tz_name: str = "Asia/Taipei") -> int:
    return _counts.get((kind, subject, _date_key(tz_name)), 0)


def remaining(kind: str, subject: str, *, tz_name: str = "Asia/Taipei") -> int:
    if not settings.free_limit_enabled:
        return 9999  # 開發期＝不限
    return max(0, daily_limit(kind) - used(kind, subject, tz_name=tz_name))


def consume(kind: str, subject: str, *, tz_name: str = "Asia/Taipei") -> dict:
    """用掉一次。用完丟 QuotaExceeded。回傳剩餘資訊給前端顯示。"""
    if kind not in _KINDS:
        raise ValueError(f"unknown quota kind: {kind}")

    dk = _date_key(tz_name)
    if settings.free_limit_enabled:
        cur = _counts.get((kind, subject, dk), 0)
        if cur >= daily_limit(kind):
            raise QuotaExceeded("今天的免費次數用完了，明天 00:00 重新開始")
        _counts[(kind, subject, dk)] = cur + 1

    return {
        "kind": kind,
        "limit": daily_limit(kind),
        "used": used(kind, subject, tz_name=tz_name),
        "remaining": remaining(kind, subject, tz_name=tz_name),
        "reset_at": f"{dk}T00:00:00（{tz_name} 隔日）",
        "unlimited": not settings.free_limit_enabled or _is_paid(subject),
    }


def _is_paid(subject: str) -> bool:
    """是否為付費會員（預留接口）。現在一律 False，等 billing 模組接上。"""
    return False


def status(subject: str, *, tz_name: str = "Asia/Taipei") -> dict:
    return {
        "download": {"limit": daily_limit("download"), "used": used("download", subject, tz_name=tz_name),
                     "remaining": remaining("download", subject, tz_name=tz_name)},
        "transfer": {"limit": daily_limit("transfer"), "used": used("transfer", subject, tz_name=tz_name),
                     "remaining": remaining("transfer", subject, tz_name=tz_name)},
        "unlimited": not settings.free_limit_enabled,
    }
