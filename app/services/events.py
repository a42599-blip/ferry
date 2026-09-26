"""事件記錄與統計（後台數據的來源）。

（規格書第 10-3 章「事件模型」）

原則：
- **只記匿名事件**，不存任何檔案內容。
- 寫入要「不影響使用者」→ 失敗也不能讓請求掛掉。
- 統計一律走這裡，後台不自己寫 SQL。
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from ..core import db

#: 事件種類（規格書 10-3）
EVENTS = (
    "page_view",
    "resolve",
    "download",
    "transfer_pair",
    "transfer_done",
    "signup",
    "login",
    "pay",
)


def track(kind: str, *, device_id: Optional[str] = None, platform: Optional[str] = None,
          result: Optional[str] = None, latency_ms: Optional[int] = None,
          quality: Optional[str] = None, size: Optional[int] = None,
          mode: Optional[str] = None, error_code: Optional[str] = None,
          country: Optional[str] = None, referrer: Optional[str] = None,
          utm: Optional[str] = None, path: Optional[str] = None,
          os_name: Optional[str] = None, browser: Optional[str] = None,
          is_new: Optional[bool] = None, meta: Optional[dict] = None) -> None:
    """記一筆事件（永不拋錯）。"""
    try:
        db.execute(
            "INSERT INTO events(ts, kind, device_id, platform, result, latency_ms, quality,"
            " size, mode, error_code, country, referrer, utm, path, os, browser, is_new, meta)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), kind, device_id, platform, result, latency_ms, quality, size,
             mode, error_code, country, referrer, utm, path, os_name, browser,
             None if is_new is None else int(is_new),
             json.dumps(meta, ensure_ascii=False) if meta else None),
        )
    except Exception:  # noqa: BLE001 — 記錄失敗絕不影響使用者
        pass


def touch_device(device_id: str, *, country: Optional[str] = None, os_name: Optional[str] = None,
                 browser: Optional[str] = None, source: Optional[str] = None) -> bool:
    """更新裝置拜訪紀錄，回傳「是否為新訪客」。"""
    if not device_id:
        return False
    now = time.time()
    try:
        row = db.one("SELECT device_id FROM devices WHERE device_id = ?", (device_id,))
        if row is None:
            db.execute(
                "INSERT INTO devices(device_id, first_seen, last_seen, visits, country, os, browser, source)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (device_id, now, now, 1, country, os_name, browser, source),
            )
            return True
        db.execute(
            "UPDATE devices SET last_seen=?, visits=visits+1,"
            " country=COALESCE(?, country), os=COALESCE(?, os),"
            " browser=COALESCE(?, browser) WHERE device_id=?",
            (now, country, os_name, browser, device_id),
        )
        return False
    except Exception:  # noqa: BLE001
        return False


# ── 統計查詢（後台用）──────────────────────────────────

def _since(days: int) -> float:
    return time.time() - days * 86400


def overview(days: int = 30) -> dict:
    """總覽：流量／解析／下載／傳輸（規格書 10-2）。"""
    since = _since(days)
    pv = db.scalar("SELECT COUNT(*) FROM events WHERE kind='page_view' AND ts>=?", (since,))
    devices = db.scalar("SELECT COUNT(DISTINCT device_id) FROM events WHERE ts>=?", (since,))
    new_devices = db.scalar(
        "SELECT COUNT(*) FROM devices WHERE first_seen>=?", (since,)
    )
    total_devices = db.scalar("SELECT COUNT(*) FROM devices")
    resolve_ok = db.scalar(
        "SELECT COUNT(*) FROM events WHERE kind='resolve' AND result='ok' AND ts>=?", (since,))
    resolve_fail = db.scalar(
        "SELECT COUNT(*) FROM events WHERE kind='resolve' AND result='fail' AND ts>=?", (since,))
    downloads = db.scalar(
        "SELECT COUNT(*) FROM events WHERE kind='download' AND ts>=?", (since,))
    dl_bytes = db.scalar(
        "SELECT COALESCE(SUM(size),0) FROM events WHERE kind='download' AND ts>=?", (since,))
    transfers = db.scalar(
        "SELECT COUNT(*) FROM events WHERE kind='transfer_done' AND ts>=?", (since,))
    tr_bytes = db.scalar(
        "SELECT COALESCE(SUM(size),0) FROM events WHERE kind='transfer_done' AND ts>=?", (since,))
    revenue = db.scalar(
        "SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='paid' AND created_at>=?",
        (since,))
    errors = db.scalar(
        "SELECT COUNT(*) FROM events WHERE kind='resolve' AND result='fail' AND ts>=?", (since,))

    return {
        "days": days,
        "page_views": pv,
        "visitors": devices,
        "new_visitors": new_devices,
        "returning_visitors": max(0, devices - new_devices),
        "total_devices": total_devices,
        "resolve_ok": resolve_ok,
        "resolve_fail": resolve_fail,
        "resolve_total": resolve_ok + resolve_fail,
        "success_rate": round(resolve_ok / (resolve_ok + resolve_fail) * 100, 1)
        if (resolve_ok + resolve_fail) else None,
        "downloads": downloads,
        "download_bytes": dl_bytes,
        "transfers": transfers,
        "transfer_bytes": tr_bytes,
        "revenue": round(float(revenue), 2),
        "errors": errors,
        "download_only_ratio": None,
    }


def daily_series(kind: str = "page_view", days: int = 14) -> list[dict]:
    """每日趨勢（預設 14 天）。"""
    rows = db.query(
        "SELECT date(ts, 'unixepoch', '+8 hours') AS d, COUNT(*) AS c"
        " FROM events WHERE kind=? AND ts>=? GROUP BY d ORDER BY d",
        (kind, _since(days)),
    )
    return [{"date": r["d"], "count": r["c"]} for r in rows]


def by_platform(days: int = 7) -> list[dict]:
    """各平台解析次數與成功率（後台「功能與平台開關」頁用）。"""
    since = _since(days)
    rows = db.query(
        "SELECT platform,"
        " SUM(CASE WHEN result='ok' THEN 1 ELSE 0 END) AS ok,"
        " SUM(CASE WHEN result='fail' THEN 1 ELSE 0 END) AS fail,"
        " AVG(CASE WHEN result='ok' THEN latency_ms END) AS avg_ms,"
        " COUNT(*) AS total"
        " FROM events WHERE kind='resolve' AND platform IS NOT NULL AND ts>=?"
        " GROUP BY platform",
        (since,),
    )
    out = []
    for r in rows:
        total = r["total"] or 0
        ok = r["ok"] or 0
        out.append({
            "platform": r["platform"],
            "ok": ok,
            "fail": r["fail"] or 0,
            "total": total,
            "success_rate": round(ok / total * 100, 1) if total else None,
            "avg_ms": int(r["avg_ms"]) if r["avg_ms"] else None,
        })
    return sorted(out, key=lambda x: -x["total"])


def top_errors(days: int = 7, limit: int = 10) -> list[dict]:
    rows = db.query(
        "SELECT platform, error_code, COUNT(*) AS c FROM events"
        " WHERE kind='resolve' AND result='fail' AND ts>=?"
        " GROUP BY platform, error_code ORDER BY c DESC LIMIT ?",
        (_since(days), limit),
    )
    return [{"platform": r["platform"], "error_code": r["error_code"], "count": r["c"]} for r in rows]


def by_country(days: int = 30, limit: int = 20) -> list[dict]:
    rows = db.query(
        "SELECT country, COUNT(DISTINCT device_id) AS visitors FROM events"
        " WHERE country IS NOT NULL AND ts>=? GROUP BY country ORDER BY visitors DESC LIMIT ?",
        (_since(days), limit),
    )
    return [{"country": r["country"], "visitors": r["visitors"]} for r in rows]


def by_device(days: int = 30) -> dict:
    rows = db.query(
        "SELECT os, COUNT(*) AS c FROM events WHERE os IS NOT NULL AND ts>=?"
        " GROUP BY os ORDER BY c DESC",
        (_since(days),),
    )
    return {r["os"]: r["c"] for r in rows}


def by_source(days: int = 30, limit: int = 20) -> list[dict]:
    rows = db.query(
        "SELECT referrer, COUNT(*) AS c FROM events WHERE kind='page_view' AND ts>=?"
        " GROUP BY referrer ORDER BY c DESC LIMIT ?",
        (_since(days), limit),
    )
    return [{"referrer": r["referrer"] or "(直接進入)", "count": r["c"]} for r in rows]


def transfer_stats(days: int = 30) -> dict:
    pairs = db.scalar(
        "SELECT COUNT(*) FROM events WHERE kind='transfer_pair' AND ts>=?", (_since(days),))
    done = db.scalar(
        "SELECT COUNT(*) FROM events WHERE kind='transfer_done' AND ts>=?", (_since(days),))
    ok = db.scalar(
        "SELECT COUNT(*) FROM events WHERE kind='transfer_done' AND result='ok' AND ts>=?",
        (_since(days),))
    total_bytes = db.scalar(
        "SELECT COALESCE(SUM(size),0) FROM events WHERE kind='transfer_done' AND ts>=?",
        (_since(days),))
    avg_ms = db.scalar(
        "SELECT AVG(latency_ms) FROM events WHERE kind='transfer_done' AND ts>=?",
        (_since(days),), default=None)
    return {
        "pairs": pairs, "done": done, "ok": ok,
        "fail": max(0, done - ok),
        "success_rate": round(ok / done * 100, 1) if done else None,
        "total_bytes": total_bytes,
        "avg_ms": int(avg_ms) if avg_ms else None,
    }


def list_devices(days: int = 30, limit: int = 100) -> list[dict]:
    rows = db.query(
        "SELECT d.device_id, d.first_seen, d.last_seen, d.visits, d.country, d.os, d.browser,"
        " (SELECT COUNT(*) FROM events e WHERE e.device_id=d.device_id AND e.kind='resolve') AS resolves,"
        " (SELECT COUNT(*) FROM events e WHERE e.device_id=d.device_id AND e.kind='download') AS downloads"
        " FROM devices d WHERE d.last_seen>=? ORDER BY d.last_seen DESC LIMIT ?",
        (_since(days), limit),
    )
    return [dict(r) for r in rows]


def device_trace(device_id: str, limit: int = 200) -> list[dict]:
    """單一裝置完整軌跡（規格書：後台「會員與裝置」頁）。"""
    rows = db.query(
        "SELECT ts, kind, platform, result, latency_ms, quality, size, error_code, path"
        " FROM events WHERE device_id=? ORDER BY ts DESC LIMIT ?",
        (device_id, limit),
    )
    return [dict(r) for r in rows]


def count_events() -> int:
    return int(db.scalar("SELECT COUNT(*) FROM events"))


def cleanup(older_than_days: Optional[int] = None) -> int:
    """手動刪除事件（規格書 10-2-1：後台可依區間刪除）。回傳刪除筆數。"""
    if older_than_days is None:
        before = "SELECT COUNT(*) FROM events"
        n = int(db.scalar(before))
        db.execute("DELETE FROM events")
        return n
    cutoff = _since(older_than_days)
    n = int(db.scalar("SELECT COUNT(*) FROM events WHERE ts < ?", (cutoff,)))
    db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
    return n


def preview_cleanup(older_than_days: int) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM events WHERE ts < ?", (_since(older_than_days),)))


def export_rows(limit: int = 100000) -> list[dict]:
    rows = db.query("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


# ── 訂單（後台收益頁用；P6 之前為空）────────────────────
def orders(limit: int = 200) -> list[dict]:
    rows = db.query("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def add_order(order_id: str, *, member_id: Optional[str], plan: str, amount: float,
              currency: str = "USD", fee: float = 0.0, status: str = "pending",
              note: Optional[str] = None) -> None:
    db.execute(
        "INSERT OR REPLACE INTO orders(id, member_id, plan, amount, currency, fee, status,"
        " created_at, note) VALUES(?,?,?,?,?,?,?,?,?)",
        (order_id, member_id, plan, amount, currency, fee, status, time.time(), note),
    )


def mark_paid(order_id: str) -> None:
    db.execute("UPDATE orders SET status='paid', paid_at=? WHERE id=?", (time.time(), order_id))


def revenue_summary(days: int = 30) -> dict:
    since = _since(days)
    month = db.scalar(
        "SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='paid' AND created_at>=?",
        (since,))
    total = db.scalar("SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='paid'")
    fees = db.scalar("SELECT COALESCE(SUM(fee),0) FROM orders WHERE status='paid'")
    cnt = db.scalar("SELECT COUNT(*) FROM orders WHERE status='paid'")
    by_plan = db.query(
        "SELECT plan, COUNT(*) AS c, COALESCE(SUM(amount),0) AS amt FROM orders"
        " WHERE status='paid' GROUP BY plan")
    return {
        "month": round(float(month), 2),
        "total": round(float(total), 2),
        "fees": round(float(fees), 2),
        "orders": int(cnt),
        "by_plan": [dict(r) for r in by_plan],
    }


def notify_recipients() -> list[str]:
    from ..core.config import settings

    saved = db.get_setting("notify_emails")
    if isinstance(saved, list) and saved:
        return [str(x).strip() for x in saved if str(x).strip()]
    return [e.strip() for e in settings.notify_emails.split(",") if e.strip()]
