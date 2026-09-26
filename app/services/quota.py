"""免費次數（quota）— 每天歸零，下載與傳輸各自獨立。

規則（2026-09-26 小羅定案）：
- 每日 **5 次**（下載 5、傳輸 5，兩個獨立額度）
- 以「使用者當地時間 00:00」重置
- 開發期間 `FREE_LIMIT_ENABLED=false`（不擋，方便測試）

⚠️ 本模組是「預留接口」的一部分：真正的計數要接資料庫／Redis，
   現在用記憶體，呼叫端不用改。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from ..core.config import settings
from ..core.errors import QuotaExceeded
from ..core import db

# kind: "download" | "transfer"
_KINDS = ("download", "transfer")


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
    try:
        return int(db.scalar(
            "SELECT count FROM quotas WHERE kind=? AND subject=? AND date_key=?",
            (kind, subject, _date_key(tz_name)),
            default=0,
        ))
    except Exception:  # noqa: BLE001 — DB 壞掉時不能讓使用者不能用
        return 0


def remaining(kind: str, subject: str, *, tz_name: str = "Asia/Taipei") -> int:
    if not settings.free_limit_enabled:
        return 9999  # 開發期＝不限
    return max(0, daily_limit(kind) - used(kind, subject, tz_name=tz_name))


def consume(kind: str, subject: str, *, tz_name: str = "Asia/Taipei") -> dict:
    """用掉一次。用完丟 QuotaExceeded。回傳剩餘資訊給前端顯示。"""
    if kind not in _KINDS:
        raise ValueError(f"unknown quota kind: {kind}")

    dk = _date_key(tz_name)
    if settings.free_limit_enabled and not _is_paid(subject):
        cur = used(kind, subject, tz_name=tz_name)
        if cur >= daily_limit(kind):
            raise QuotaExceeded("今天的免費次數用完了，明天 00:00 重新開始")
        try:
            db.execute(
                "INSERT INTO quotas(kind, subject, date_key, count, tz, updated_at)"
                " VALUES(?,?,?,1,?,?)"
                " ON CONFLICT(kind, subject, date_key)"
                " DO UPDATE SET count=count+1, updated_at=excluded.updated_at",
                (kind, subject, dk, tz_name, time.time()),
            )
        except Exception:  # noqa: BLE001
            pass

    return {
        "kind": kind,
        "limit": daily_limit(kind),
        "used": used(kind, subject, tz_name=tz_name),
        "remaining": remaining(kind, subject, tz_name=tz_name),
        "reset_at": f"{dk}T00:00:00（{tz_name} 隔日）",
        "unlimited": not settings.free_limit_enabled or _is_paid(subject),
    }


def _is_paid(subject: str) -> bool:
    """是否為付費會員（預留接口，接上 billing 後就生效）。"""
    try:
        from . import billing

        return billing.is_unlimited(subject)
    except Exception:  # noqa: BLE001
        return False


def reset_all() -> None:
    """後台用：清空所有次數計數。"""
    db.execute("DELETE FROM quotas")


def status(subject: str, *, tz_name: str = "Asia/Taipei") -> dict:
    return {
        "download": {"limit": daily_limit("download"), "used": used("download", subject, tz_name=tz_name),
                     "remaining": remaining("download", subject, tz_name=tz_name)},
        "transfer": {"limit": daily_limit("transfer"), "used": used("transfer", subject, tz_name=tz_name),
                     "remaining": remaining("transfer", subject, tz_name=tz_name)},
        "unlimited": not settings.free_limit_enabled,
    }
