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
    if not _limit_on(kind) or _is_paid(subject):
        return 9999  # 不限（公測全開／該模組開關關掉／會員）
    return max(0, daily_limit(kind) - used(kind, subject, tz_name=tz_name))


def consume(kind: str, subject: str, *, tz_name: str = "Asia/Taipei") -> dict:
    """用掉一次。用完丟 QuotaExceeded。回傳剩餘資訊給前端顯示。"""
    if kind not in _KINDS:
        raise ValueError(f"unknown quota kind: {kind}")

    dk = _date_key(tz_name)
    if _limit_on(kind) and not _is_paid(subject):
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
        "unlimited": not _limit_on(kind) or _is_paid(subject),
    }


def _limit_on(kind: str) -> bool:
    """這個模組現在要不要限制次數？

    ⚠️ 小羅 2026-09-27：開關要**分開**（下載一個、傳輸一個），
       關掉＝該模組無限使用（公測期間想全開就是關掉限制）。
    """
    from . import flags

    if not settings.free_limit_enabled:
        return False
    key = "feature.free_limit_transfer" if kind == "transfer" else "feature.free_limit_download"
    try:
        return bool(flags.feature_enabled(key))
    except Exception:  # noqa: BLE001
        return True


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
    dk = _date_key(tz_name)
    parts = dk.split("-")
    hint = f"{parts[1]}-{parts[2]} 00:00"        # 例：09-27 00:00
    # ⚠️ 「不限次數」要**分模組**判斷（下載與傳輸的免費開關是分開的）
    paid = _is_paid(subject)
    dl_unlimited = not _limit_on("download") or paid
    tr_unlimited = not _limit_on("transfer") or paid
    return {
        "download": {
            "limit": daily_limit("download"),
            "used": used("download", subject, tz_name=tz_name),
            "remaining": remaining("download", subject, tz_name=tz_name),
            "reset_hint": hint,
            "unlimited": dl_unlimited,
        },
        "transfer": {
            "limit": daily_limit("transfer"),
            "used": used("transfer", subject, tz_name=tz_name),
            "remaining": remaining("transfer", subject, tz_name=tz_name),
            "reset_hint": hint,
            "unlimited": tr_unlimited,
        },
        "timezone": tz_name,
        # 舊欄位：兩個都無限才算全站無限（前台相容用）
        "unlimited": dl_unlimited and tr_unlimited,
    }


# ══════════════════════════════════════════════════════════════════
#  後台工具：手動恢復／查看免費次數
#
#  小羅 2026-09-27：「用了一次免費次數但沒有解析成功，正常不該記次數；
#                    如果記了，我是不是可以手動幫他恢復一次？」
#  → 解析失敗本來就不會扣（扣次數在解析成功之後），
#    但遇到例外狀況（逾時、平台風控）可以在後台幫他補回來。
# ══════════════════════════════════════════════════════════════════

def grant(kind: str, subject: str, n: int = 1, *, tz_name: str = "Asia/Taipei") -> dict:
    """把某個主體的今日用量「減掉 n 次」（＝還他 n 次免費額度）。

    不會減到負數；回傳還原後的狀態。
    """
    n = max(1, min(int(n), 50))
    dk = _date_key(tz_name)
    cur = used(kind, subject, tz_name=tz_name)
    newv = max(0, cur - n)
    db.execute(
        "INSERT INTO quotas(kind, subject, date_key, count, tz, updated_at)"
        " VALUES(?,?,?,?,?,?)"
        " ON CONFLICT(kind, subject, date_key) DO UPDATE SET count=excluded.count,"
        " updated_at=excluded.updated_at",
        (kind, subject, dk, newv, tz_name, time.time()),
    )
    return {"kind": kind, "subject": subject, "before": cur, "after": newv, "gave_back": cur - newv,
            "date": dk}


def today_usage(*, tz_name: str = "Asia/Taipei", limit: int = 200) -> list[dict]:
    """今天各主體用了幾次（後台用，看得出誰快用完）。"""
    dk = _date_key(tz_name)
    rows = db.query(
        "SELECT kind, subject, count, updated_at FROM quotas WHERE date_key=?"
        " ORDER BY count DESC LIMIT ?", (dk, limit))
    lim = {k: daily_limit(k) for k in _KINDS}
    return [{**dict(r), "limit": lim.get(r["kind"], 0)} for r in rows]


def reset_today(subject: str | None = None, *, tz_name: str = "Asia/Taipei") -> int:
    """把今天的用量歸零（不給 subject＝全部歸零）。回傳影響筆數。"""
    dk = _date_key(tz_name)
    if subject:
        db.execute("UPDATE quotas SET count=0, updated_at=? WHERE date_key=? AND subject=?",
                   (time.time(), dk, subject))
    else:
        db.execute("UPDATE quotas SET count=0, updated_at=? WHERE date_key=?", (time.time(), dk))
    return int(db.scalar("SELECT COUNT(*) FROM quotas WHERE date_key=?", (dk,)))
