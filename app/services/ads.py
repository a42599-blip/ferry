"""廣告（預留；小羅 2026-09-29）。

小羅的規則：
  · 訪客（未登入）  → 每用 **3** 次，看一次廣告
  · 免費會員        → 每用 **5** 次，看一次廣告
  · 月會員／永久會員 → **不看廣告**
  · 月會員到期未續  → 打回免費會員 → 恢復「每 5 次看一次廣告」（由 members.tier_of 判定）

⚠️ 開關有兩個（小羅 2026-09-29：可單獨決定哪種身分要看廣告），預設都是 **False**
   → 前台不會出現任何廣告。後台「功能開關」有中文名稱：
     · 廣告 ── 訪客（每 3 次看一次）      feature.ads_guest
     · 廣告 ── 免費會員（每 5 次看一次）  feature.ads_member
   月會員／永久會員**沒有開關**（永遠不看廣告）。
"""
from __future__ import annotations

import json

from . import flags

#: 訪客每幾次看一次廣告
ADS_EVERY_GUEST = 3
#: 免費會員每幾次看一次廣告
ADS_EVERY_MEMBER = 5


def every_of(tier: str) -> int:
    """這個等級每幾次要看一次廣告（0＝不用看）。"""
    if tier == "guest":
        return ADS_EVERY_GUEST
    if tier == "free":
        return ADS_EVERY_MEMBER
    return 0


#: 各身分對應的開關（後台可單獨開關；月／永久會員沒有開關＝永遠不看）
_FLAG_OF_TIER = {
    "guest": "feature.ads_guest",
    "free": "feature.ads_member",
}


def enabled_for(tier: str) -> bool:
    """這個等級的廣告開關有沒有開（關掉＝該等級**不再限制次數**，見 quota）。

    小羅 2026-09-29：「那兩個三次跟五次的開關你要對應好，**我關掉它就不再限制**。」
      · 訪客   → feature.ads_guest
      · 免費會員 → feature.ads_member
      · 月／永久 → 沒有開關（永遠不看廣告、也沒有限制）
    """
    flag = _FLAG_OF_TIER.get(tier)
    if not flag:
        return False
    try:
        return bool(flags.feature_enabled(flag))
    except Exception:  # noqa: BLE001
        return False


def state(tier: str, used: int) -> dict:
    """回傳給前台的廣告狀態。due=False 時前台什麼都不做。

    `due=True` ＝「**下一次動作之前要先看一次廣告**」（不是用完當下就跳）。
    看完廣告 → 前台打 `POST /api/ads/reward` → 依等級把次數加回來（見 api_member）。
    """
    enabled = enabled_for(tier)
    every = every_of(tier)
    # ⚠️ 2026-09-29 修（小羅：「按繼續之後又跳廣告」）：
    #    原本用 `used % every == 0`（剛好整除才 due）→ 客人「下載 3 次＋傳輸 3 次＝合併 6 次」時，
    #    看完廣告把已用從 6 減 3 → 3 → **3 還是 3 的倍數 → 仍然 due → 又跳一次廣告**（重複跳）。
    #    小羅要的規則：「每用 N 次看一次廣告；看完可以再用 N 次」→
    #    只看「合併已用 >= N」就要看廣告（看完會把已用減回 0，所以不會連續跳）。
    due = bool(enabled and every and used >= every)
    return {"enabled": enabled, "due": due, "every": every, "used": int(used or 0)}


# ── 看廣告計時（伺服器端驗證：沒看滿 N 秒不給次數）────────────
#   小羅 2026-09-29：「彈出來就關掉，當然不能給他加次數，我得不到廣告費啊。」
#   用記憶體記「開始時間」（單一實例、有 TTL）→ 前端改不動、也不能作弊。
_ad_started: dict[str, float] = {}
_AD_TTL = 3600.0          # 1 小時沒動就清掉


def mark_start(subject: str) -> int:
    """記錄「這個人現在開始看廣告」，回傳要看滿幾秒。"""
    import time as _t
    from ..core import config

    now = _t.time()
    for k in [k for k, v in _ad_started.items() if now - v > _AD_TTL]:
        _ad_started.pop(k, None)
    _ad_started[subject] = now
    return int(getattr(config.settings, "ads_min_seconds", 15) or 15)


def watch_ok(subject: str, seconds: int) -> bool:
    """有沒有看滿？沒記錄（直接打 reward）＝沒看過 → 不給。"""
    import time as _t

    started = _ad_started.get(subject)
    if not started:
        return False
    return (_t.time() - started) >= max(0, int(seconds or 0))


# ══════════════════════════════════════════════════════════
# 後台「📺 廣告」統計（小羅 2026-09-29 指定）
#   後台一定要看到：① 看多久 ② 看幾次 ③ 給了幾次 ④ 哪邊的客戶 ⑤ 誰在看
#   ＋ 反向對帳：手動輸入廣告商後台的回報曝光數 → 自動算差異
# ══════════════════════════════════════════════════════════
AD_VIEW_KEEP_DAYS = 180          # 明細保留 180 天（體積很小，但不要無限長大）
_REPORTED_KEY = "ads.reported"   # 廣告商回報數（settings 表，JSON：{"2026-09-29": 123}）


def platform_of(ua: str) -> str:
    """手機／電腦（後台「哪種裝置在看」用）。"""
    u = (ua or "").lower()
    if any(k in u for k in ("iphone", "ipad", "ipod", "android", "mobile")):
        return "手機"
    return "電腦"


def elapsed(subject: str) -> float:
    """從「開始看廣告」到現在實際幾秒（寫進統計用）。"""
    import time as _t

    started = _ad_started.get(subject)
    return max(0.0, _t.time() - started) if started else 0.0


def record_view(*, subject: str, tier: str, member_id: str = "", member_email: str = "",
                seconds: float = 0.0, min_seconds: int = 15, granted: int = 0,
                kind: str = "", used_before: int = 0, used_after: int = 0,
                country: str = "", device_id: str = "", platform: str = "") -> None:
    """記一筆「看廣告」（後台統計＋對帳用；永不拋錯）。"""
    import time as _t

    from ..core import db

    now = _t.time()
    try:
        db.execute(
            "INSERT INTO ad_views(ts, subject, tier, member_id, member_email, seconds,"
            " min_seconds, granted, kind, used_before, used_after, country, device_id, platform)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, subject, tier, member_id or None, member_email or None,
             float(seconds or 0), int(min_seconds or 0), int(granted or 0), kind or None,
             int(used_before or 0), int(used_after or 0), country or None,
             device_id or None, platform or None))
    except Exception:  # noqa: BLE001 — 統計不能影響使用者
        return
    # 順手清舊資料（每 20 筆一次，不要每次掃）
    try:
        if int(now) % 20 == 0:
            db.execute("DELETE FROM ad_views WHERE ts < ?", (now - AD_VIEW_KEEP_DAYS * 86400,))
    except Exception:  # noqa: BLE001
        pass


def _reported_load() -> dict:
    """廣告商後台的回報數（手動輸入）。"""
    from ..core import db

    try:
        rows = db.query("SELECT value FROM settings WHERE key=?", (_REPORTED_KEY,))
        if rows and rows[0]["value"]:
            data = json.loads(rows[0]["value"])
            if isinstance(data, dict):
                return {str(k): int(v or 0) for k, v in data.items()}
    except Exception:  # noqa: BLE001
        pass
    return {}


def set_reported(date: str, count: int) -> dict:
    """設定某一天廣告商回報的曝光數（對帳用）。"""
    import time as _t

    from ..core import db

    data = _reported_load()
    key = str(date or "").strip()
    if not key:
        raise ValueError("缺少日期")
    data[key] = max(0, int(count or 0))
    db.execute(
        "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (_REPORTED_KEY, json.dumps(data, ensure_ascii=False), _t.time()))
    return {"reported": data}


def export_rows(days: int = 180) -> list[dict]:
    """匯出明細（對帳用 CSV）。"""
    from ..core import db

    since = _since(days)
    rows = db.query(
        "SELECT ts, subject, tier, member_email, seconds, min_seconds, granted, kind,"
        " used_before, used_after, country, device_id, platform"
        " FROM ad_views WHERE ts>=? ORDER BY ts DESC", (since,))
    out = []
    for r in rows:
        out.append({
            "時間": _fmt(r["ts"]),
            "誰": r["member_email"] or r["subject"] or "",
            "身分": _tier_label(r["tier"]),
            "看了幾秒": round(float(r["seconds"] or 0), 1),
            "要求秒數": r["min_seconds"],
            "給了幾次": r["granted"],
            "動作": r["kind"] or "",
            "看前已用": r["used_before"],
            "看後已用": r["used_after"],
            "國家": r["country"] or "",
            "裝置": r["device_id"] or "",
            "機型": r["platform"] or "",
        })
    return out


def _since(days: int) -> float:
    import time as _t

    return _t.time() - max(1, int(days)) * 86400


def _fmt(ts: float) -> str:
    import datetime as _dt

    try:
        return _dt.datetime.fromtimestamp(float(ts), _dt.timezone(_dt.timedelta(hours=8))
                                          ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return str(ts)


def _tier_label(tier: str) -> str:
    return {"guest": "訪客", "free": "免費會員", "monthly": "月會員",
            "lifetime": "永久會員"}.get(tier or "", tier or "–")


def stats(days: int = 30) -> dict:
    """後台「📺 廣告」頁的全部數據（含對帳）。"""
    from ..core import db

    since = _since(days)

    def one(sql: str, args: tuple = ()) -> dict:
        rows = db.query(sql, args)
        return dict(rows[0]) if rows else {}

    today = one(
        "SELECT COUNT(*) AS views, COALESCE(SUM(granted),0) AS granted,"
        " COALESCE(SUM(seconds),0) AS secs FROM ad_views"
        " WHERE date(ts,'unixepoch','+8 hours')=date('now','+8 hours')")
    s = one(
        "SELECT COUNT(*) AS views, COALESCE(SUM(seconds),0) AS secs,"
        " COALESCE(AVG(seconds),0) AS avg_secs, COALESCE(SUM(granted),0) AS granted,"
        " COUNT(DISTINCT COALESCE(member_id, subject)) AS users"
        " FROM ad_views WHERE ts>=?", (since,))
    daily = [dict(r) for r in db.query(
        "SELECT date(ts,'unixepoch','+8 hours') AS d, COUNT(*) AS views,"
        " COALESCE(SUM(granted),0) AS granted, COALESCE(SUM(seconds),0) AS secs"
        " FROM ad_views WHERE ts>=? GROUP BY d ORDER BY d", (since,))]
    by_tier = [dict(r) for r in db.query(
        "SELECT tier, COUNT(*) AS views, COALESCE(SUM(granted),0) AS granted,"
        " COALESCE(AVG(seconds),0) AS avg_secs FROM ad_views WHERE ts>=?"
        " GROUP BY tier ORDER BY views DESC", (since,))]
    by_country = [dict(r) for r in db.query(
        "SELECT COALESCE(country,'(未知)') AS country, COUNT(*) AS views,"
        " COALESCE(SUM(granted),0) AS granted FROM ad_views WHERE ts>=?"
        " GROUP BY country ORDER BY views DESC LIMIT 30", (since,))]
    by_platform = [dict(r) for r in db.query(
        "SELECT COALESCE(platform,'(未知)') AS platform, COUNT(*) AS views"
        " FROM ad_views WHERE ts>=? GROUP BY platform ORDER BY views DESC", (since,))]
    recent = []
    for r in db.query(
            "SELECT ts, subject, tier, member_email, seconds, granted, kind, country,"
            " platform, used_before, used_after FROM ad_views WHERE ts>=?"
            " ORDER BY ts DESC LIMIT 50", (since,)):
        r = dict(r)
        r["time"] = _fmt(r["ts"])
        r["who"] = r.get("member_email") or r.get("subject") or "–"
        r["tier_label"] = _tier_label(r.get("tier"))
        r["seconds"] = round(float(r.get("seconds") or 0), 1)
        recent.append(r)

    # ── 反向對帳：我們記的次數 vs 廣告商回報（手動輸入）──
    reported = _reported_load()
    recon = []
    t_ours = t_rep = 0
    for row in daily:
        rep = int(reported.get(row["d"], 0))
        t_ours += int(row["views"] or 0)
        t_rep += rep
        recon.append({"d": row["d"], "ours": int(row["views"] or 0), "reported": rep,
                      "diff": rep - int(row["views"] or 0)})
    return {
        "range_days": max(1, int(days)),
        "summary": {
            "views": int(s.get("views") or 0),
            "secs": float(s.get("secs") or 0),
            "avg_secs": round(float(s.get("avg_secs") or 0), 1),
            "granted": int(s.get("granted") or 0),
            "users": int(s.get("users") or 0),
            "today_views": int(today.get("views") or 0),
            "today_granted": int(today.get("granted") or 0),
            "today_secs": float(today.get("secs") or 0),
        },
        "daily": daily,
        "by_tier": by_tier,
        "by_country": by_country,
        "by_platform": by_platform,
        "recent": recent,
        "recon": {"rows": recon, "total_ours": t_ours, "total_reported": t_rep,
                  "diff": t_rep - t_ours, "has_reported": bool(reported)},
        "reported": reported,
    }
