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
from typing import Optional

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
          url: Optional[str] = None,
          os_name: Optional[str] = None, browser: Optional[str] = None,
          is_new: Optional[bool] = None, meta: Optional[dict] = None) -> None:
    """記一筆事件（永不拋錯）。

    ⚠️ 任何帶 device_id 的事件都會自動把該裝置寫進 devices 表，
       這樣「沒看首頁、直接解析」的使用者後台也看得到（真實聯動）。
    """
    if device_id:
        _ensure_device(device_id)
    try:
        db.execute(
            "INSERT INTO events(ts, kind, device_id, platform, result, latency_ms, quality,"
            " size, mode, error_code, country, referrer, utm, path, url, os, browser, is_new, meta)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), kind, device_id, platform, result, latency_ms, quality, size,
             mode, error_code, country, referrer, utm, path, (url or "")[:500] or None,
             os_name, browser,
             None if is_new is None else int(is_new),
             json.dumps(meta, ensure_ascii=False) if meta else None),
        )
    except Exception:  # noqa: BLE001 — 記錄失敗絕不影響使用者
        pass


def _ensure_device(device_id: str) -> None:
    """沒有這台裝置就建一筆（不改 visits）。"""
    try:
        db.execute(
            "INSERT INTO devices(device_id, first_seen, last_seen, visits)"
            " VALUES(?,?,?,0) ON CONFLICT(device_id) DO UPDATE SET last_seen=excluded.last_seen",
            (device_id, time.time(), time.time()),
        )
    except Exception:  # noqa: BLE001
        pass


def touch_meta(device_id: str, *, country: Optional[str] = None, os_name: Optional[str] = None,
               browser: Optional[str] = None, source: Optional[str] = None) -> None:
    """補上裝置的環境資訊（不改 visits）——任何請求都可以呼叫。"""
    if not device_id:
        return
    _ensure_device(device_id)
    try:
        db.execute(
            "UPDATE devices SET country=COALESCE(?, country), os=COALESCE(?, os),"
            " browser=COALESCE(?, browser), source=COALESCE(?, source), last_seen=?"
            " WHERE device_id=?",
            (country or None, os_name or None, browser or None, source or None,
             time.time(), device_id),
        )
    except Exception:  # noqa: BLE001
        pass


def link_member(device_id: str, member_id: str, email: str | None = None) -> None:
    """把裝置綁到會員（註冊／登入時呼叫）→ 後台就能顯示「會員」。"""
    if not device_id or not member_id:
        return
    _ensure_device(device_id)
    try:
        db.execute(
            "UPDATE devices SET member_id=?, member_email=COALESCE(?, member_email), last_seen=?"
            " WHERE device_id=?",
            (member_id, email, time.time(), device_id),
        )
    except Exception:  # noqa: BLE001
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

    # ── 更細的下載／解析數值（小羅 2026-09-27 要求）─────────
    dl_ok = db.scalar(
        "SELECT COUNT(*) FROM events WHERE kind='download' AND ts>=?"
        " AND (result IS NULL OR result='ok')", (since,))
    dl_fail = max(0, downloads - dl_ok)
    #: 解析平均耗時（毫秒）——後端在 resolve 事件記 elapsed_ms
    resolve_avg_ms = db.scalar(
        "SELECT AVG(latency_ms) FROM events WHERE kind='resolve' AND result='ok'"
        " AND latency_ms IS NOT NULL AND ts>=?", (since,))
    #: 下載平均檔案大小
    dl_avg_bytes = db.scalar(
        "SELECT AVG(size) FROM events WHERE kind='download' AND size>0 AND ts>=?", (since,))
    #: 今日（近 24 小時）即時數字
    day = _since(1)
    dl_today = db.scalar("SELECT COUNT(*) FROM events WHERE kind='download' AND ts>=?", (day,))
    dl_today_bytes = db.scalar(
        "SELECT COALESCE(SUM(size),0) FROM events WHERE kind='download' AND ts>=?", (day,))
    rs_today = db.scalar("SELECT COUNT(*) FROM events WHERE kind='resolve' AND ts>=?", (day,))
    rs_today_ok = db.scalar(
        "SELECT COUNT(*) FROM events WHERE kind='resolve' AND result='ok' AND ts>=?", (day,))
    #: 使用者最常選的畫質（下載事件有記 quality）
    q_rows = [dict(r) for r in db.query(
        "SELECT quality AS name, COUNT(*) AS n FROM events"
        " WHERE kind='download' AND ts>=? AND quality IS NOT NULL AND quality<>''"
        " GROUP BY quality ORDER BY n DESC LIMIT 8", (since,))]
    #: 逐時分佈（近 24 小時）——看得出哪個時段人最多
    hourly = [dict(r) for r in db.query(
        "SELECT CAST((ts - ?) / 3600 AS INTEGER) AS h, COUNT(*) AS n FROM events"
        " WHERE kind='page_view' AND ts>=? GROUP BY h ORDER BY h", (day, day))]

    # 訪客 vs 會員（以裝置為單位）
    member_devices = int(db.scalar(
        "SELECT COUNT(DISTINCT e.device_id) FROM events e"
        " JOIN members m ON m.device_id = e.device_id"
        " WHERE e.ts>=? AND e.device_id IS NOT NULL", (since,)))
    paid_devices = int(db.scalar(
        "SELECT COUNT(*) FROM members WHERE plan<>'free'"))

    return {
        "days": days,
        "audience": {
            "members": member_devices,
            "visitors": max(0, devices - member_devices),
            "paid_members": paid_devices,
        },
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
        # ── 下載 ──
        "download_ok": dl_ok,
        "download_fail": dl_fail,
        "download_rate": round(dl_ok / downloads * 100, 1) if downloads else None,
        "download_avg_bytes": int(dl_avg_bytes or 0),
        "download_today": dl_today,
        "download_today_bytes": dl_today_bytes,
        # ── 解析 ──
        "resolve_avg_ms": int(resolve_avg_ms or 0),
        "resolve_today": rs_today,
        "resolve_today_ok": rs_today_ok,
        # ── 其他 ──
        "top_qualities": q_rows,
        "hourly": hourly,
        "download_only_ratio": (round((downloads / (resolve_ok or 1)) * 100, 1)
                                if downloads and resolve_ok else None),
    }


def daily_series(kind: str = "page_view", days: int = 14) -> list[dict]:
    """每日趨勢。

    ⚠️ 欄位名固定用 `d`（日期）與 `c`（次數），與 `_bucket_rows()` 一致
       —— 之前用 date/count 造成前端圖表讀不到值（全顯示 undefined）。
    """
    return _bucket_rows(days, kind)


def by_platform(days: int = 7) -> list[dict]:
    """各平台的**解析**與**下載**表現（後台「平台表現」用）。

    ⚠️ 兩件事要分開看，不能混在一起：
        解析成功 ≠ 下載成功（可能解析到了，但下載時連結失效／被擋）
    """
    since = _since(days)
    rows = db.query(
        "SELECT platform, kind,"
        " SUM(CASE WHEN result='ok' THEN 1 ELSE 0 END) AS ok,"
        " SUM(CASE WHEN result='fail' THEN 1 ELSE 0 END) AS fail,"
        " AVG(CASE WHEN result='ok' THEN latency_ms END) AS avg_ms,"
        " COUNT(*) AS total"
        " FROM events"
        " WHERE kind IN ('resolve','download') AND platform IS NOT NULL"
        "   AND platform <> '' AND ts>=?"
        " GROUP BY platform, kind",
        (since,),
    )
    agg: dict[str, dict] = {}
    for r in rows:
        p = agg.setdefault(r["platform"], {
            "platform": r["platform"],
            "resolve_ok": 0, "resolve_fail": 0, "resolve_ms": None,
            "download_ok": 0, "download_fail": 0,
        })
        if r["kind"] == "resolve":
            p["resolve_ok"] = r["ok"] or 0
            p["resolve_fail"] = r["fail"] or 0
            p["resolve_ms"] = int(r["avg_ms"]) if r["avg_ms"] else None
        else:
            p["download_ok"] = r["ok"] or 0
            p["download_fail"] = r["fail"] or 0

    out = []
    for p in agg.values():
        rt = p["resolve_ok"] + p["resolve_fail"]
        dt = p["download_ok"] + p["download_fail"]
        p["resolve_total"] = rt
        p["download_total"] = dt
        p["resolve_rate"] = round(p["resolve_ok"] / rt * 100, 1) if rt else None
        p["download_rate"] = round(p["download_ok"] / dt * 100, 1) if dt else None
        p["avg_ms"] = p["resolve_ms"]
        p["total"] = rt + dt
        p["ok"] = p["resolve_ok"] + p["download_ok"]
        p["fail"] = p["resolve_fail"] + p["download_fail"]
        # 相容舊欄位（其他頁面還在用 success_rate）
        p["success_rate"] = p["resolve_rate"]
        out.append(p)
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


def list_devices(days: int = 30, limit: int = 200) -> list[dict]:
    """裝置清單（後台「會員與裝置」頁）。

    每列都告訴你：**他是會員還是訪客**、解析成功幾次失敗幾次、
    最近解析哪個平台／哪個網址、下載幾次、最後活動時間。
    """
    rows = db.query(
        "SELECT d.device_id, d.first_seen, d.last_seen, d.visits, d.country, d.os, d.browser, d.source,"
        "  (SELECT COUNT(*) FROM events e WHERE e.device_id=d.device_id AND e.kind='resolve') AS resolves,"
        "  (SELECT COUNT(*) FROM events e WHERE e.device_id=d.device_id AND e.kind='resolve' AND e.result='ok') AS resolve_ok,"
        "  (SELECT COUNT(*) FROM events e WHERE e.device_id=d.device_id AND e.kind='resolve' AND e.result='fail') AS resolve_fail,"
        "  (SELECT COUNT(*) FROM events e WHERE e.device_id=d.device_id AND e.kind='download') AS downloads,"
        "  (SELECT COUNT(*) FROM events e WHERE e.device_id=d.device_id AND e.kind='transfer_done') AS transfers,"
        "  (SELECT MAX(e.platform) FROM events e WHERE e.device_id=d.device_id AND e.platform IS NOT NULL AND e.platform<>'') AS last_platform,"
        "  (SELECT MAX(e.url) FROM events e WHERE e.device_id=d.device_id AND e.url IS NOT NULL) AS last_url,"
        "  COALESCE(m.email, d.member_email) AS member_email,"
        "  m.plan AS member_plan, COALESCE(m.id, d.member_id) AS member_id"
        " FROM devices d"
        " LEFT JOIN members m ON (m.device_id = d.device_id OR m.id = d.member_id)"
        " WHERE d.last_seen>=? ORDER BY d.last_seen DESC LIMIT ?",
        (_since(days), limit),
    )
    out = []
    for r in rows:
        d = dict(r)
        # 身份：user:xxx 開頭＝已登入會員；members 有對應＝會員；否則訪客
        did = d.get("device_id") or ""
        if did.startswith("user:"):
            d["kind"] = "member"
            d["kind_label"] = "會員"
        elif d.get("member_id"):
            d["kind"] = "member"
            d["kind_label"] = "會員（已綁帳號）"
        else:
            d["kind"] = "visitor"
            d["kind_label"] = "訪客"
        total = (d.get("resolve_ok") or 0) + (d.get("resolve_fail") or 0)
        d["success_rate"] = round((d.get("resolve_ok") or 0) / total * 100, 1) if total else None
        out.append(d)
    return out


def device_trace(device_id: str, limit: int = 200) -> list[dict]:
    """單一裝置完整軌跡（規格書：後台「會員與裝置」頁）。"""
    rows = db.query(
        "SELECT ts, kind, platform, result, latency_ms, quality, size, mode, error_code,"
        " path, url, referrer FROM events WHERE device_id=? ORDER BY ts DESC LIMIT ?",
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
    """訂單列表（小羅 2026-09-30：要能交叉比對「哪個會員下的單」）。"""
    rows = db.query(
        "SELECT o.*, m.email AS member_email, m.nickname AS member_nick"
        " FROM orders o LEFT JOIN members m ON m.id = substr(o.member_id, 6)"
        " ORDER BY o.created_at DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def set_order_method(order_id: str, method: str) -> None:
    """記錄「這筆是用哪個付款方式付的」（信用卡／ATM／超商／Apple Pay…）。"""
    if method:
        db.execute("UPDATE orders SET pay_method=? WHERE id=?", (str(method)[:40], order_id))


def set_order_txn(order_id: str, txn: str) -> None:
    """記錄金流商的交易序號（以後跟金流商對帳、查這筆付款用）。"""
    if txn:
        db.execute("UPDATE orders SET provider_txn=? WHERE id=?", (str(txn)[:100], order_id))


def add_order(order_id: str, *, member_id: Optional[str], plan: str, amount: float,
              currency: str = "TWD", fee: float = 0.0, status: str = "pending",
              note: Optional[str] = None, provider: str = "") -> None:
    db.execute(
        "INSERT OR REPLACE INTO orders(id, member_id, plan, amount, currency, fee, status,"
        " created_at, note, provider) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (order_id, member_id, plan, amount, currency, fee, status, time.time(), note,
         provider or None),
    )


def mark_paid(order_id: str) -> None:
    db.execute("UPDATE orders SET status='paid', paid_at=? WHERE id=?", (time.time(), order_id))


def mark_failed(order_id: str, reason: str = "") -> None:
    """記錄付款失敗（小羅 2026-10-03：失敗紀錄也要進後台，供客服追查）。

    只把仍在 pending 的訂單標成 failed，不覆蓋已 paid 的。
    """
    if not order_id:
        return
    row = db.one("SELECT status, note FROM orders WHERE id=?", (order_id,))
    if row is None or (row["status"] or "") == "paid":
        return
    old = (row["note"] or "")
    extra = ("付款失敗：" + str(reason)[:120]) if reason else "付款失敗"
    note = (old + " | " + extra).strip(" |")[:400]
    db.execute("UPDATE orders SET status='failed', note=? WHERE id=?", (note, order_id))


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


# ── 成長趨勢（後台「成長趨勢」頁）──────────────────────
def _bucket_rows(days: int, kind: str | None = None) -> list[dict]:
    """每日彙總（台北時間）。"""
    sql = ("SELECT date(ts,'unixepoch','+8 hours') AS d,"
           " COUNT(*) AS c,"
           " COUNT(DISTINCT device_id) AS devices"
           " FROM events WHERE ts>=?")
    params: list = [_since(days)]
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    sql += " GROUP BY d ORDER BY d"
    return [dict(r) for r in db.query(sql, tuple(params))]


def _sum_between(kind: str, start_days_ago: int, end_days_ago: int) -> int:
    lo, hi = _since(start_days_ago), _since(end_days_ago)
    return int(db.scalar(
        "SELECT COUNT(*) FROM events WHERE kind=? AND ts>=? AND ts<?", (kind, lo, hi)))


def _pct(cur: int, prev: int) -> float | None:
    if prev <= 0:
        return None if cur <= 0 else 100.0
    return round((cur - prev) / prev * 100, 1)


def growth(days: int = 90) -> dict:
    """成長趨勢：本期 vs 前期、每日趨勢、留存、轉換。"""
    half = max(1, days // 2)

    def pair(kind: str) -> dict:
        cur = _sum_between(kind, half, 0)
        prev = _sum_between(kind, days, half)
        return {"current": cur, "previous": prev, "change_pct": _pct(cur, prev)}

    visitors_cur = int(db.scalar(
        "SELECT COUNT(DISTINCT device_id) FROM events WHERE ts>=?", (_since(half),)))
    visitors_prev = int(db.scalar(
        "SELECT COUNT(DISTINCT device_id) FROM events WHERE ts>=? AND ts<?", (_since(days), _since(half))))

    total_devices = int(db.scalar("SELECT COUNT(*) FROM devices"))
    returning = int(db.scalar("SELECT COUNT(*) FROM devices WHERE visits >= 2"))
    members_n = int(db.scalar("SELECT COUNT(*) FROM members"))
    paid = int(db.scalar("SELECT COUNT(*) FROM members WHERE plan<>'free'"))
    orders_paid = int(db.scalar("SELECT COUNT(*) FROM orders WHERE status='paid'"))
    revenue_cur = float(db.scalar(
        "SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='paid' AND created_at>=?",
        (_since(half),), default=0.0))
    revenue_prev = float(db.scalar(
        "SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='paid' AND created_at>=? AND created_at<?",
        (_since(days), _since(half)), default=0.0))

    resolve_series = _bucket_rows(days, "resolve")
    dl_series = _bucket_rows(days, "download")
    pv_series = _bucket_rows(days, "page_view")

    return {
        "days": days,
        "period_days": half,
        "visitors": {"current": visitors_cur, "previous": visitors_prev,
                     "change_pct": _pct(visitors_cur, visitors_prev)},
        "resolve": pair("resolve"),
        "download": pair("download"),
        "transfer": pair("transfer_done"),
        "page_view": pair("page_view"),
        "revenue": {"current": round(revenue_cur, 2), "previous": round(revenue_prev, 2),
                    "change_pct": _pct(int(revenue_cur * 100), int(revenue_prev * 100))},
        "funnel": {
            "visitors": visitors_cur,
            "resolvers": int(db.scalar(
                "SELECT COUNT(DISTINCT device_id) FROM events"
                " WHERE kind='resolve' AND result='ok' AND ts>=?", (_since(half),))),
            "downloaders": int(db.scalar(
                "SELECT COUNT(DISTINCT device_id) FROM events WHERE kind='download' AND ts>=?",
                (_since(half),))),
            "members": members_n,
            "paid": paid,
            "orders": orders_paid,
        },
        "retention": {
            "total_devices": total_devices,
            "returning": returning,
            "returning_pct": round(returning / total_devices * 100, 1) if total_devices else None,
        },
        "series": {"page_view": pv_series, "resolve": resolve_series, "download": dl_series},
        "platform_ranking": platform_ranking(days),
    }


# ── 平台排行榜（成長趨勢頁）────────────────────────────
def platform_ranking(days: int = 30, limit: int = 30) -> list[dict]:
    """各平台的排行 ＋ 可交叉比對的指標。

    小羅 2026-09-27：要看得出「客戶比較常去哪邊下載」，
    所以要能**交叉比對**，不是只有單一數字：

      解析次數 / 佔比   → 用戶最想用哪個平台
      解析成功率        → 這個平台的解析穩不穩（低 = 要修）
      下載轉換率        → 解析成功後真的下載的比例
                          （偏低 = 解析到了卻下載不了 → 下載體驗要加強）
      成長率            → 本期 vs 前一期（看趨勢）
    """
    half = max(1, days // 2)
    cur_lo, cur_hi = _since(half), _since(0)
    prev_lo, prev_hi = _since(days), _since(half)

    # 重新用兩段時間分別查（比較好懂也比較準）
    def _bucket(lo: float, hi: float) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in db.query(
            "SELECT platform, kind, result, COUNT(*) AS c FROM events"
            " WHERE kind IN ('resolve','download') AND platform IS NOT NULL AND platform <> ''"
            "   AND ts>=? AND ts<? GROUP BY platform, kind, result",
            (lo, hi),
        ):
            p = out.setdefault(r["platform"], {"resolve_ok": 0, "resolve_fail": 0,
                                               "download_ok": 0, "download_fail": 0})
            if r["kind"] == "resolve":
                p["resolve_ok" if r["result"] == "ok" else "resolve_fail"] = r["c"]
            else:
                p["download_ok" if r["result"] == "ok" else "download_fail"] = r["c"]
        return out

    cur = _bucket(cur_lo, cur_hi)
    prev = _bucket(prev_lo, prev_hi)

    ms_rows = {
        r["platform"]: r["ms"] for r in db.query(
            "SELECT platform, AVG(latency_ms) AS ms FROM events"
            " WHERE kind='resolve' AND result='ok' AND latency_ms IS NOT NULL AND ts>=?"
            " GROUP BY platform", (cur_lo,))
    }

    names = set(cur) | set(prev)
    total_resolve = sum(v["resolve_ok"] + v["resolve_fail"] for v in cur.values()) or 1

    out: list[dict] = []
    for name in names:
        c = cur.get(name, {"resolve_ok": 0, "resolve_fail": 0, "download_ok": 0, "download_fail": 0})
        p = prev.get(name, {"resolve_ok": 0, "resolve_fail": 0, "download_ok": 0, "download_fail": 0})
        rt = c["resolve_ok"] + c["resolve_fail"]
        dt = c["download_ok"] + c["download_fail"]
        prev_rt = p["resolve_ok"] + p["resolve_fail"]
        out.append({
            "platform": name,
            "resolve_ok": c["resolve_ok"], "resolve_fail": c["resolve_fail"],
            "resolve_total": rt,
            "resolve_rate": round(c["resolve_ok"] / rt * 100, 1) if rt else None,
            "download_ok": c["download_ok"], "download_fail": c["download_fail"],
            "download_total": dt,
            "download_rate": round(c["download_ok"] / dt * 100, 1) if dt else None,
            # 交叉比對：解析成功後真的下載的比例
            "download_per_resolve": round(dt / c["resolve_ok"] * 100, 1) if c["resolve_ok"] else None,
            "share": round(rt / total_resolve * 100, 1) if rt else 0.0,
            "growth_pct": _pct(rt, prev_rt),
            "avg_ms": int(ms_rows[name]) if ms_rows.get(name) else None,
        })
    return sorted(out, key=lambda x: -x["resolve_total"])[:limit]
