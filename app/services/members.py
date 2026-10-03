"""會員帳號（P5 預留 → 現在實作；付費方案要綁在帳號上）。

- 密碼用 `hashlib.scrypt`（標準庫，不需額外套件）
- 登入後發 HMAC 權杖（跟後台同一套機制，但獨立金鑰）
- 全站仍只透過 `auth.current_subject()` 取得身份 → 其他模組不用改
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from ..core import db
from ..core.errors import BadRequest


def _secret() -> bytes:
    s = db.get_setting("member_secret")
    if not s:
        s = secrets.token_hex(32)
        db.set_setting("member_secret", s)
    return s.encode()


def _hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def _verify(password: str, stored: str) -> bool:
    try:
        salt_b64, dk_b64 = stored.split("$", 1)
        salt = base64.b64decode(salt_b64)
        want = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, want)
    except Exception:  # noqa: BLE001
        return False


# ── 註冊／登入 ───────────────────────────────────────
def register(email: str, password: str, *, device_id: str | None = None,
             tz: str | None = None, country: str | None = None) -> dict:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 6:
        raise BadRequest("Email 格式不正確", code="BAD_EMAIL")
    if len(password or "") < 6:
        raise BadRequest("密碼至少 6 個字", code="WEAK_PASSWORD")

    if db.one("SELECT id FROM members WHERE email = ?", (email,)):
        raise BadRequest("這個 Email 已經註冊過了", code="EMAIL_TAKEN")

    mid = "u_" + secrets.token_hex(8)
    db.execute(
        "INSERT INTO members(id, email, plan, device_id, tz, created_at, country)"
        " VALUES(?,?,?,?,?,?,?)",
        (mid, email, "free", device_id, tz, time.time(),
         (country or "").upper()[:2] or None),
    )
    db.execute("UPDATE members SET password=? WHERE id=?", (_hash(password), mid))
    from . import events

    events.link_member(device_id or "", mid, email)
    events.track("signup", device_id=device_id, meta={"method": "email", "member_id": mid})
    log_plan(mid, email, None, "free", reason="signup")
    # ⚠️ 註冊完就是「第一次登入」（小羅 2026-09-27 抓到：
    #    「他已經註冊了為什麼登錄次數是 0 次？」）
    #    沒有這一行，後台的「最後登入」會空白、次數永遠從 0 開始，
    #    客服會誤以為「這個帳號沒人用過」。
    touch_login(mid)
    return {"id": mid, "email": email, "plan": "free"}


def login(email: str, password: str, *, device_id: str | None = None) -> dict:
    email = (email or "").strip().lower()
    row = db.one("SELECT * FROM members WHERE email = ?", (email,))
    if row is None or not _verify(password or "", row["password"] or ""):
        raise BadRequest("Email 或密碼錯誤", code="BAD_CREDENTIALS")
    # 停權／註銷要真的進不去（小羅 2026-09-29 要求）
    _r = dict(row)
    _st = _r.get("status") or "active"
    if _st == "deleted":
        raise BadRequest("這個帳號已經註銷了", code="ACCOUNT_DELETED")
    if _st == "suspended":
        raise BadRequest("這個帳號已被停權，請聯絡客服", code="ACCOUNT_SUSPENDED")
    touch_login(row["id"])
    if device_id:
        db.execute("UPDATE members SET device_id=? WHERE id=?", (device_id, row["id"]))
    from . import events

    events.link_member(device_id or "", row["id"], row["email"])
    events.track("login", device_id=device_id, meta={"method": "email", "member_id": row["id"]})
    return {"id": row["id"], "email": row["email"], "plan": row["plan"],
            "token": issue_token(row["id"])}


def issue_token(member_id: str, ttl: int = 30 * 86400) -> str:
    payload = {"m": member_id, "exp": int(time.time()) + ttl}
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def verify_token(token: str) -> str | None:
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
    mid = payload.get("m")
    # 停權／註銷 → token 立即失效（小羅 2026-09-29：「停權要真的停、刪除要真的進不去」）
    if mid:
        st = db.scalar("SELECT COALESCE(status,'active') FROM members WHERE id=?", (mid,))
        if st in ("suspended", "deleted"):
            return None
    return mid


def get(member_id: str) -> dict | None:
    """取單一會員（含方案、付費起始、到期、登入等資訊；客服查詢要用）。"""
    row = db.one(
        "SELECT id, email, COALESCE(nickname,'') AS nickname, plan, tz, device_id,"
        " created_at, expires_at, country,"
        " plan_started_at, COALESCE(status,'active') AS status, deleted_at,"
        " last_login_at, last_seen_at, COALESCE(login_count,0) AS login_count,"
        " COALESCE(marketing_opt_in,0) AS marketing_opt_in"
        " FROM members WHERE id=?", (member_id,))
    if not row:
        return None
    m = dict(row)
    m["remaining_days"] = remaining_days(m)
    return m


def by_device(device_id: str) -> dict | None:
    if not device_id:
        return None
    row = db.one("SELECT id, email, plan FROM members WHERE device_id=?", (device_id,))
    return dict(row) if row else None


SESSION_GAP = 1800


def touch_login(member_id: str) -> None:
    """記錄最後登入時間與次數（明確按下登入時用）。"""
    now = time.time()
    db.execute(
        "UPDATE members SET last_login_at=?, last_seen_at=?,"
        " login_count=COALESCE(login_count,0)+1 WHERE id=?",
        (now, now, member_id))


def touch_session(member_id: str, *, device_id: str | None = None,
                  country: str | None = None) -> bool:
    """每次「帶著登入狀態進站」時呼叫。

    ⚠️ 為什麼需要（小羅 2026-09-27 抓到）：
       原本只有「按下登入按鈕」才算一次，所以會員關掉螢幕再回來、
       或換裝置開網站（token 自動登入）→ 完全不會增加，
       後台的「登入次數」看起來永遠是 1，資料就不準。

    回傳 True＝這次算「新的一次登入」（順便寫一筆事件，讓登入歷史看得到）。
    """
    now = time.time()
    row = db.one("SELECT last_seen_at, last_login_at FROM members WHERE id=?", (member_id,))
    if not row:
        return False
    # 用「最後有動作的時間」判斷：只要他一直在用，就不算新的一次登入
    #   10:00 用到 12:00（中間都有動作）→ 一次
    #   休息後 14:00 再來（中間超過 30 分鐘沒動作）→ 又一次
    last_seen = float(row["last_seen_at"] or row["last_login_at"] or 0)
    if now - last_seen < SESSION_GAP:
        db.execute("UPDATE members SET last_seen_at=? WHERE id=?", (now, member_id))
        return False                     # 同一工作階段，只更新「最後活動」
    db.execute("UPDATE members SET last_seen_at=? WHERE id=?", (now, member_id))
    touch_login(member_id)               # 新的工作階段 → 登入次數 +1
    try:
        from . import events

        events.track("login", device_id=device_id, country=country,
                     meta={"method": "session", "member_id": member_id})
    except Exception:  # noqa: BLE001
        pass
    return True


def log_plan(member_id: str, email: str | None, from_plan: str | None, to_plan: str,
             *, amount: float = 0.0, expires_at: float | None = None,
             reason: str = "admin", note: str = "") -> None:
    """寫一筆方案變更紀錄（小羅：「要知道付費時間、用什麼費率、剩餘多少」）。"""
    db.execute(
        "INSERT INTO plan_history(member_id, email, from_plan, to_plan, amount,"
        " currency, at, expires_at, reason, note) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (member_id, email, from_plan, to_plan, float(amount or 0), "TWD",
         time.time(), expires_at, reason, (note or "")[:300]))


def plan_history(member_id: str, limit: int = 50) -> list[dict]:
    """某個會員的方案變更歷史（後台客服查詢用）。"""
    return [dict(r) for r in db.query(
        "SELECT * FROM plan_history WHERE member_id=? ORDER BY at DESC LIMIT ?",
        (member_id, limit))]


def set_plan(member_id: str, plan: str, expires_at: float | None = None, *,
             amount: float = 0.0, reason: str = "admin", note: str = "") -> None:
    """設定方案（付款成功、後台改方案、到期降級…都會走這裡）。

    ⚠️ 一併記錄「付費起始時間」與寫入方案變更歷史，
       這樣才回答得出「什麼時間加入會員、用什麼費率、什麼時間到期」。
    """
    m = get(member_id) or {}
    old = m.get("plan") or "free"
    db.execute(
        "UPDATE members SET plan=?, expires_at=?,"
        " plan_started_at=CASE WHEN ?<>'free' AND (plan_started_at IS NULL OR ?='free')"
        "   THEN ? ELSE plan_started_at END"
        " WHERE id=?",
        (plan, expires_at, plan, old, time.time(), member_id))
    if old != plan:
        log_plan(member_id, m.get("email"), old, plan, amount=amount,
                 expires_at=expires_at, reason=reason, note=note)


# ══════════════════════════════════════════════════════════════════
#  會員統計（小羅 2026-09-27 要求）
#  「有多少會員、多少付費會員、付費等級分別幾個、今天新增幾個、
#    付費／未付費／訪客各多少 —— 這些數據都要留。」
# ══════════════════════════════════════════════════════════════════

#: 國家／地區代碼 → 中文名（常用的；查不到就顯示代碼本身）
COUNTRY_NAMES: dict[str, str] = {
    "TW": "台灣", "HK": "香港", "MO": "澳門", "CN": "中國", "JP": "日本",
    "KR": "韓國", "SG": "新加坡", "MY": "馬來西亞", "TH": "泰國", "VN": "越南",
    "ID": "印尼", "PH": "菲律賓", "IN": "印度", "AU": "澳洲", "NZ": "紐西蘭",
    "US": "美國", "CA": "加拿大", "MX": "墨西哥", "BR": "巴西", "AR": "阿根廷",
    "GB": "英國", "UK": "英國", "IE": "愛爾蘭", "FR": "法國", "DE": "德國",
    "ES": "西班牙", "IT": "義大利", "NL": "荷蘭", "BE": "比利時", "CH": "瑞士",
    "AT": "奧地利", "SE": "瑞典", "NO": "挪威", "DK": "丹麥", "FI": "芬蘭",
    "PL": "波蘭", "PT": "葡萄牙", "GR": "希臘", "TR": "土耳其", "RU": "俄羅斯",
    "UA": "烏克蘭", "AE": "阿聯酋", "SA": "沙烏地", "IL": "以色列", "EG": "埃及",
    "ZA": "南非", "NG": "奈及利亞", "KE": "肯亞", "PK": "巴基斯坦",
    "BD": "孟加拉", "LK": "斯里蘭卡", "MM": "緬甸", "KH": "柬埔寨", "LA": "寮國",
    "MN": "蒙古", "NP": "尼泊爾", "??": "未記錄",
}


def _day_start(tz_name: str = "Asia/Taipei") -> float:
    """該時區「今天 00:00」的時間戳（後端全部用 UTC 秒，這裡換算當地午夜）。"""
    import datetime as _dt

    try:
        from zoneinfo import ZoneInfo

        now = _dt.datetime.now(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001
        now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()


def stats(tz_name: str = "Asia/Taipei") -> dict:
    """會員統計（後台首頁／會員頁用）。"""
    from . import billing

    total = int(db.scalar("SELECT COUNT(*) FROM members") or 0)
    # 「今天」＝該時區的當天 00:00 起算
    day_start = _day_start(tz_name)
    today_new = int(db.scalar(
        "SELECT COUNT(*) FROM members WHERE created_at>=?", (day_start,)) or 0)
    week_new = int(db.scalar(
        "SELECT COUNT(*) FROM members WHERE created_at>=?", (day_start - 6 * 86400,)) or 0)

    # 各方案分佈（付費等級不同要分別列出來）
    rows = {r["plan"]: int(r["c"]) for r in db.query(
        "SELECT plan, COUNT(*) AS c FROM members GROUP BY plan")}
    plans = []
    for pid, meta in billing.PLANS.items():
        n = rows.get(pid, 0)
        if pid == billing.PLAN_FREE:
            continue
        plans.append({"id": pid, "name": meta["name"], "price": meta["price"], "count": n})
    free_n = rows.get(billing.PLAN_FREE, 0)
    paid_n = sum(x["count"] for x in plans)

    # 訪客＝有活動紀錄但沒有註冊帳號的裝置
    visitor_devices = int(db.scalar(
        "SELECT COUNT(*) FROM devices WHERE device_id NOT IN"
        " (SELECT device_id FROM members WHERE device_id IS NOT NULL AND device_id<>'')") or 0)

    # ── 地區分佈（小羅 2026-09-27：要知道會員來自哪邊、哪邊多哪邊少）──
    #    國家代碼來自 Cloudflare 的 cf-ipcountry（註冊當下寫入 members.country）
    regions = [dict(r) for r in db.query(
        "SELECT COALESCE(NULLIF(country,''),'??') AS code, COUNT(*) AS n"
        " FROM members GROUP BY code ORDER BY n DESC")]
    paid_regions = {r["code"]: int(r["n"]) for r in db.query(
        "SELECT COALESCE(NULLIF(country,''),'??') AS code, COUNT(*) AS n"
        " FROM members WHERE plan<>? GROUP BY code", (billing.PLAN_FREE,))}
    for r in regions:
        r["paid"] = paid_regions.get(r["code"], 0)
        r["name"] = COUNTRY_NAMES.get(r["code"], r["code"])
        r["pct"] = round(r["n"] / total * 100, 1) if total else 0.0

    return {
        "total": total,
        "today_new": today_new,
        "week_new": week_new,
        "free": free_n,
        "paid": paid_n,
        "plans": plans,
        "visitor_devices": visitor_devices,
        "conversion": round(paid_n / total * 100, 1) if total else 0.0,
        "regions": regions,
    }


def list_full(limit: int = 300) -> list[dict]:
    """會員清單（含方案名稱）。"""
    from . import billing

    plans = billing.PLANS
    out = []
    for r in db.query(
            "SELECT id, email, COALESCE(nickname,'') AS nickname, plan, device_id,"
            " tz, created_at, expires_at"
            " FROM members ORDER BY created_at DESC LIMIT ?", (limit,)):
        row = dict(r)
        row["plan_name"] = (plans.get(row["plan"]) or {}).get("name", row["plan"])
        row["paid"] = row["plan"] not in ("", None, billing.PLAN_FREE)
        out.append(row)
    return out


def delete(member_id: str, *, hard: bool = False) -> bool:
    """註銷帳號。

    ⚠️ 預設是**軟刪除**（小羅 2026-09-27 的教訓）：
       我以前寫成硬刪除（DELETE FROM members）→ 資料直接消失、撈不回來。
       改成軟刪除後：狀態標記為 deleted、保留資料，
       要復原隨時可以（restore()）；也符合個資法「保留一段時間可追溯」。

    hard=True 才會真的刪（只給後台清理測試帳號用）。
    """
    m = get(member_id)
    if not m:
        return False
    if hard:
        db.execute("DELETE FROM members WHERE id=?", (member_id,))
        return True
    db.execute(
        "UPDATE members SET status='deleted', deleted_at=?"
        " WHERE id=?", (time.time(), member_id))
    return True


def restore(member_id: str) -> bool:
    """復原被軟刪除的會員。"""
    row = db.one("SELECT id FROM members WHERE id=?", (member_id,))
    if not row:
        return False
    db.execute("UPDATE members SET status='active', deleted_at=NULL"
               " WHERE id=?", (member_id,))
    return True


def restore_from_history() -> dict:
    """從 plan_history 把「被硬刪除而消失」的會員重建回來。

    小羅 2026-09-27：「把歷史資料再給我撈出來再回來。」
    plan_history 記了每一次方案變更（含 email、方案、時間），
    所以即使 members 表被硬刪除，還是能從這裡把帳號重建。
    """
    rebuilt = []
    rows = db.query(
        "SELECT member_id, email, MIN(at) AS born FROM plan_history"
        " WHERE member_id IS NOT NULL GROUP BY member_id")
    for r in rows:
        mid = r["member_id"]
        if db.one("SELECT id FROM members WHERE id=?", (mid,)):
            continue                      # 還在，不用重建
        _l = db.one(
            "SELECT to_plan, expires_at FROM plan_history WHERE member_id=?"
            " ORDER BY at DESC LIMIT 1", (mid,))
        latest = dict(_l) if _l else {}
        plan = latest.get("to_plan") or "free"
        db.execute(
            "INSERT INTO members(id, email, plan, created_at, expires_at, status)"
            " VALUES(?,?,?,?,?,'active')",
            (mid, r["email"], plan, r["born"],
             latest.get("expires_at")))
        rebuilt.append({"id": mid, "email": r["email"], "plan": plan})
    return {"rebuilt": len(rebuilt), "rows": rebuilt}


def remaining_days(m: dict) -> int | None:
    """付費會員還剩幾天（免費或無到期日回 None）。"""
    if not m or m.get("plan") in (None, "", "free"):
        return None
    exp = m.get("expires_at")
    if not exp:
        return None
    return max(0, int((float(exp) - time.time()) // 86400))


def expiring_soon(days: int = 7) -> list[dict]:
    """再過 `days` 天內就到期、且還是付費狀態的會員（到期提醒用）。"""
    now = time.time()
    rows = db.query(
        "SELECT * FROM members WHERE plan<>'free' AND expires_at IS NOT NULL"
        " AND expires_at>? AND expires_at<=? AND COALESCE(status,'') <> 'deleted'"
        " ORDER BY expires_at", (now, now + days * 86400))
    out = []
    for r in rows:
        row = dict(r)
        row["days_left"] = remaining_days(row)
        out.append(row)
    return out


def already_expired() -> list[dict]:
    """已過期但方案還是付費的會員（要降回免費）。"""
    return [dict(r) for r in db.query(
        "SELECT * FROM members WHERE plan<>'free' AND expires_at IS NOT NULL"
        " AND expires_at<=? AND COALESCE(status,'') <> 'deleted'", (time.time(),))]


def emails(only: str = "all") -> list[str]:
    """取得會員 Email 名單（後台寄通知用）。

    only: all（全部）／paid（只付費）／free（只免費）
    """
    from . import billing

    if only == "paid":
        sql = ("SELECT email FROM members WHERE email IS NOT NULL AND email<>''"
               " AND plan<>? ORDER BY created_at DESC")
        rows = db.query(sql, (billing.PLAN_FREE,))
    elif only == "free":
        sql = ("SELECT email FROM members WHERE email IS NOT NULL AND email<>''"
               " AND plan=? ORDER BY created_at DESC")
        rows = db.query(sql, (billing.PLAN_FREE,))
    else:
        rows = db.query("SELECT email FROM members WHERE email IS NOT NULL AND email<>''"
                        " ORDER BY created_at DESC")
    return [r["email"] for r in rows if r["email"]]


def counts_by(only: str = "all") -> int:
    return len(emails(only))


# ══════════════════════════════════════════════════════════════════
#  會員到期處理（小羅 2026-09-27）
#  「時間到之前要提醒客戶，看他願不願意付費；不然就要恢復一天五次的機制。」
#
#  兩件事：
#    ① 到期前 N 天 → 寄提醒信（每階段只寄一次，避免天天騷擾）
#    ② 到期後      → 自動降回免費（免費次數機制就會生效）
# ══════════════════════════════════════════════════════════════════

#: 到期前幾天要提醒（例：7 天前、3 天前、1 天前各一次）
REMIND_DAYS = (7, 3, 1)


async def run_expiry_tasks() -> dict:
    """執行到期提醒與降級（由排程每日呼叫一次）。"""
    from . import billing, notify

    reminded = downgraded = 0

    # ① 到期提醒（每個階段只寄一次：用 settings 記「已經提醒過哪天」）
    for m in expiring_soon(max(REMIND_DAYS)):
        left = m.get("days_left")
        if left not in REMIND_DAYS:
            continue
        key = f"plan_remind:{m['id']}:{left}"
        if db.get_setting(key):
            continue
        plan_name = (billing.PLANS.get(m["plan"]) or {}).get("name", m["plan"])
        when = time.strftime("%Y-%m-%d", time.localtime(m["expires_at"]))
        body = chr(10).join([
            f"你的「{plan_name}」將於 {when} 到期（剩 {left} 天）。",
            "",
            "到期後：",
            "• 會員資格會暫停，無限次數會恢復成「每日免費 5 次」",
            "• 續約後立即恢復無限次數",
            "",
            "續約請回到網站 →「方案」頁。",
            "",
            "（這是系統自動通知，不用回覆）",
        ])
        try:
            await notify.send_to(m["email"], "【轉運站】會員即將到期通知", body)
            db.set_setting(key, "1")
            reminded += 1
        except Exception:  # noqa: BLE001
            pass

    # ② 已到期 → 自動降回免費（免費次數機制立刻生效）
    for m in already_expired():
        subj = f"user:{m['id']}"
        old = m["plan"]
        set_plan(m["id"], "free", None, reason="expire",
                 note=f"原方案 {old} 到期自動降級")
        downgraded += 1
        try:
            await notify.send_to(
                m["email"], "【轉運站】會員已到期",
                chr(10).join([
                    f"你的「{(billing.PLANS.get(old) or {}).get('name', old)}」" 
                    f"已於 {time.strftime('%Y-%m-%d', time.localtime(m['expires_at']))} 到期。",
                    "",
                    "目前已恢復成免費方案（每日免費 5 次）。",
                    "隨時可以回到網站「方案」頁續約，續約後立即恢復無限次數。",
                ]))
        except Exception:  # noqa: BLE001
            pass

    return {"reminded": reminded, "downgraded": downgraded}


# ══════════════════════════════════════════════════════════════════
#  後台手動管理（小羅 2026-09-27 要求）
#  「我可以開給他測試次數多一點、臨時開通、臨時加次數或加時間，
#    有點像賠償機制 —— 萬一是我們的問題造成他下載不成功。」
# ══════════════════════════════════════════════════════════════════

def grant_plan(member_id: str, plan: str, days: int = 0, *,
               reason: str = "gift", note: str = "") -> dict:
    """後台臨時開通／補償會員資格。

    days > 0 → 從「現在」或「原本到期日」往後加幾天（取較晚的）。
    days = 0 且 plan 有到期設定 → 用方案預設天數。
    """
    from . import billing

    m = get(member_id)
    if not m:
        raise BadRequest("找不到這個會員")
    if plan not in billing.PLANS:
        raise BadRequest("方案不正確")
    period = (billing.PLANS.get(plan) or {}).get("period_days")
    add_days = days or (period or 0)
    base = time.time()
    cur_exp = m.get("expires_at") or 0
    if cur_exp and float(cur_exp) > base:
        base = float(cur_exp)          # 還沒到期 → 從原到期日往後加
    new_exp = (base + add_days * 86400) if add_days else None
    set_plan(member_id, plan, new_exp, reason=reason, note=note or f"後台補償 {add_days} 天")
    return get(member_id) or {}


def extend_days(member_id: str, days: int, *, reason: str = "gift", note: str = "") -> dict:
    """只加到期時間（不改方案）。給「補償幾天」用。"""
    m = get(member_id)
    if not m:
        raise BadRequest("找不到這個會員")
    if (m.get("plan") or "free") == "free":
        # 免費會員要加時間 → 先給月會員（臨時開通）
        return grant_plan(member_id, "monthly", days, reason=reason, note=note)
    cur = float(m.get("expires_at") or time.time())
    base = max(time.time(), cur)
    new_exp = base + max(1, int(days)) * 86400
    set_plan(member_id, m["plan"], new_exp, reason=reason, note=note or f"加 {days} 天")
    return get(member_id) or {}


def set_status(member_id: str, status: str) -> bool:
    """停權／復權（status: active / suspended）。

    ⚠️ 停權只影響會員資格（降回免費），帳號資料保留，之後可以復權。
    """
    if status not in ("active", "suspended"):
        raise BadRequest("狀態不正確")
    db.execute("UPDATE members SET status=? WHERE id=?", (status, member_id))
    return True


def search(keyword: str, limit: int = 50) -> list[dict]:
    """客服查詢：用 Email／會員 ID／暱稱 找會員（小羅 2026-09-29：「賬號或 ID 或昵稱都要能搜」）。"""
    kw = (keyword or "").strip()
    if not kw:
        return []
    like = f"%{kw}%"
    rows = db.query(
        "SELECT * FROM members WHERE email LIKE ? OR id LIKE ?"
        " OR COALESCE(nickname,'') LIKE ?"
        " ORDER BY created_at DESC LIMIT ?", (like, like, like, limit))
    out = []
    for r in rows:
        m = dict(r)
        m["remaining_days"] = remaining_days(m)
        out.append(m)
    return out


# ══════════════════════════════════════════════════════════════════
#  會員資料模塊：清單（搜尋／排序／分頁）＋ 資料卡
#  小羅：「1000 個客戶時我一條一條刷找不到人，要有搜尋列，
#         點進去就是他的會員資料卡。」
# ══════════════════════════════════════════════════════════════════

SORTS = {
    "created_desc": "created_at DESC",
    "created_asc": "created_at ASC",
    "expires_asc": "CASE WHEN expires_at IS NULL THEN 1 ELSE 0 END, expires_at ASC",
    "login_desc": "COALESCE(last_login_at,0) DESC",
    "email_asc": "email ASC",
}


def page(q: str = "", plan: str = "", sort: str = "created_desc",
         page: int = 1, size: int = 25) -> dict:
    """會員清單（分頁）。回傳 total 讓前端算頁數。"""
    from . import billing

    where, params = [], []
    kw = (q or "").strip()
    if kw:
        where.append("(email LIKE ? OR id LIKE ? OR COALESCE(nickname,'') LIKE ?)")
        like = f"%{kw}%"
        params += [like, like, like]
    if plan == "paid":
        where.append("plan<>?")
        params.append(billing.PLAN_FREE)
    elif plan == "free":
        where.append("plan=?")
        params.append(billing.PLAN_FREE)
    elif plan:
        where.append("plan=?")
        params.append(plan)
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = int(db.scalar("SELECT COUNT(*) FROM members" + sql_where, tuple(params)) or 0)
    order = SORTS.get(sort, SORTS["created_desc"])
    offset = max(0, (page - 1) * size)
    rows = db.query(
        "SELECT id, email, COALESCE(nickname,'') AS nickname, plan, device_id, tz,"
        " created_at, expires_at, country,"
        " plan_started_at, COALESCE(status,'active') AS status,"
        " last_login_at, last_seen_at, COALESCE(login_count,0) AS login_count"
        " FROM members" + sql_where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
        tuple(params + [size, offset]))
    plans = billing.PLANS
    out = []
    for r in rows:
        m = dict(r)
        m["remaining_days"] = remaining_days(m)
        m["plan_name"] = (plans.get(m["plan"]) or {}).get("name", m["plan"])
        m["paid"] = m["plan"] not in ("", None, billing.PLAN_FREE)
        out.append(m)
    return {"rows": out, "total": total, "page": page, "size": size,
            "pages": max(1, (total + size - 1) // size)}


def card(member_id: str) -> dict | None:
    """會員資料卡：基本資料 ＋ 方案歷史 ＋ 最近活動 ＋ 回報紀錄。"""
    from . import billing, events

    m = get(member_id)
    if not m:
        return None
    m["plan_name"] = (billing.PLANS.get(m.get("plan")) or {}).get("name", m.get("plan"))
    m["price"] = (billing.PLANS.get(m.get("plan")) or {}).get("price", 0)
    m["history"] = plan_history(member_id, limit=30)
    dev = m.get("device_id") or ""
    m["recent"] = events.device_trace(dev, limit=15) if dev else []
    # 登入歷史（小羅：「他中間有沒有登入過第 2 次第 3 次？資料要清楚」）
    like = "%" + member_id + "%"
    m["logins"] = [dict(r) for r in db.query(
        "SELECT ts, country, os, browser FROM events"
        " WHERE kind='login' AND (device_id=? OR meta LIKE ?)"
        " ORDER BY ts DESC LIMIT 30", (dev, like))]
    m["quota"] = {
        "download": _quota_state("download", member_id),
        "transfer": _quota_state("transfer", member_id),
    }
    return m


def _quota_state(kind: str, member_id: str) -> dict:
    from . import quota as _q

    subj = f"user:{member_id}"
    return {"used": _q.used(kind, subj), "limit": _q.daily_limit(kind, subj),
            "remaining": _q.remaining(kind, subj)}


def backfill_logins() -> int:
    """把 events 裡的登入紀錄回填到 members（補早期資料）。

    ⚠️ 只補「login_count 還是 0」的會員，不會覆蓋已有資料。
    小羅 2026-09-27：「他中間有沒有登入過？叫你做數據就是要清楚」。
    """
    fixed = 0
    rows = db.query(
        "SELECT id, email, device_id, created_at, COALESCE(login_count,0) AS lc"
        " FROM members WHERE COALESCE(login_count,0)=0")
    for r in rows:
        mid = r["id"]
        dev = r["device_id"] or ""
        cnt = 0
        last = None
        if dev:
            cnt = int(db.scalar(
                "SELECT COUNT(*) FROM events WHERE kind='login' AND device_id=?", (dev,)) or 0)
            last = db.scalar(
                "SELECT MAX(ts) FROM events WHERE kind='login' AND device_id=?", (dev,))
        if not cnt:
            like = "%" + mid + "%"
            cnt = int(db.scalar(
                "SELECT COUNT(*) FROM events WHERE kind='login' AND meta LIKE ?", (like,)) or 0)
            last = last or db.scalar(
                "SELECT MAX(ts) FROM events WHERE kind='login' AND meta LIKE ?", (like,))
        if not cnt:
            # 完全沒有登入紀錄 → 至少把「註冊」算成第一次
            cnt, last = 1, r["created_at"]
        db.execute("UPDATE members SET login_count=?, last_login_at=? WHERE id=?",
                   (cnt, last, mid))
        fixed += 1
    return fixed


def backfill_country() -> int:
    """把 events／devices 裡的地區回填到 members（補早期資料）。"""
    fixed = 0
    for r in db.query(
            "SELECT id, device_id FROM members"
            " WHERE country IS NULL OR country=''"):
        dev = r["device_id"] or ""
        cc = None
        if dev:
            cc = db.scalar(
                "SELECT country FROM events WHERE device_id=? AND country IS NOT NULL"
                " AND country<>'' ORDER BY ts DESC LIMIT 1", (dev,))
            if not cc:
                cc = db.scalar(
                    "SELECT country FROM devices WHERE device_id=? AND country IS NOT NULL"
                    " AND country<>''", (dev,))
        if cc:
            db.execute("UPDATE members SET country=? WHERE id=?", (cc.upper()[:2], r["id"]))
            fixed += 1
    return fixed


def rebuild(email: str, device_id: str, *, created_at: float | None = None,
            plan: str = "free", expires_at: float | None = None,
            country: str | None = None) -> dict:
    """用「已知的原始資料」把一位被刪掉的會員重建回來。

    為什麼需要（小羅 2026-09-27：「我要你恢復剛剛那個會員和他的歷史資料」）：
    plan_history 只有「新版本」才會寫；比較早期建立的會員被刪掉後，
    plan_history 裡沒有紀錄 → restore_from_history() 救不回來。
    但 devices / events 還留著（裝置、國家、活動時間），
    所以可以用這些線索把帳號重建。
    """
    email = (email or "").strip().lower()
    if not email:
        raise BadRequest("缺少 email")
    old = db.one("SELECT id FROM members WHERE email=?", (email,))
    if old:
        return dict(get(old["id"]) or {})     # 已經在，不用重建
    mid = "u_" + secrets.token_hex(8)
    born = float(created_at or time.time())
    # 國家／首次活動時間可以從 devices 補
    if not country:
        country = db.scalar(
            "SELECT country FROM devices WHERE device_id=?", (device_id,))
    if not created_at:
        fs = db.scalar("SELECT first_seen FROM devices WHERE device_id=?", (device_id,))
        born = float(fs or born)
    db.execute(
        "INSERT INTO members(id, email, plan, device_id, created_at, expires_at,"
        " country, status, login_count, last_login_at)"
        " VALUES(?,?,?,?,?,?,?,'active',1,?)",
        (mid, email, plan, device_id, born, expires_at,
         (country or "").upper()[:2] or None, born))
    log_plan(mid, email, None, plan, expires_at=expires_at, reason="restore",
             note="從裝置紀錄重建（原本被刪除）")
    return dict(get(mid) or {})


#: 暱稱長度上限（太長會撐破版面；客服也用不到那麼長）
NICKNAME_MAX = 20


def set_nickname(member_id: str, nickname: str) -> dict | None:
    """設定／清除暱稱（小羅 2026-09-29：「讓客戶自己改自己的名稱」）。

    空白＝清除（回到用 Email 顯示）。會去掉控制字元並限制長度。
    """
    name = re.sub(r"[\x00-\x1f<>]", "", (nickname or "")).strip()
    name = re.sub(r"\s+", " ", name)[:NICKNAME_MAX]
    if name:
        cur = db.one("SELECT nickname FROM members WHERE id=?", (member_id,))
        if not cur or (cur["nickname"] or "") != name:
            dup = db.one("SELECT id FROM members WHERE nickname=? AND id<>?"
                         " AND COALESCE(status,'') <> 'deleted'", (name, member_id))
            if dup:
                raise ValueError("這個昵稱已經有人用了，請換一個")
    db.execute("UPDATE members SET nickname=? WHERE id=?", (name or None, member_id))
    return get(member_id)


def set_password(member_id: str, new_password: str) -> dict | None:
    """會員自己改密碼（前台「會員中心」）。

    小羅 2026-09-29：「前後台你也都沒有給我設計改帳密的方法或按鈕和邏輯呀」
    → 後台走 admin 帳密模塊；前台就是這裡（會員改自己的密碼）。
    """
    if len(new_password or "") < 6:
        raise BadRequest("密碼至少 6 個字", code="WEAK_PASSWORD")
    db.execute("UPDATE members SET password=? WHERE id=?",
               (_hash(new_password), member_id))
    return get(member_id)


def check_password(member_id: str, password: str) -> bool:
    """驗某位會員的密碼（會員自己改密碼時要先驗舊密碼）。"""
    row = db.one("SELECT password FROM members WHERE id=?", (member_id,))
    return bool(row) and _verify(password or "", row["password"] or "")


# ══════════════════════════════════════════════════════════
#  忘記密碼 ／ 忘記帳號（小羅 2026-09-29：前台登入頁要有這兩個，
#  而且「忘記密碼也要真實有效」，不能只是好看的按钮）
# ══════════════════════════════════════════════════════════
RESET_TTL = 1800          # 重設連結有效 30 分鐘


def _mask(s: str) -> str:
    """把帳號打碼：a42599 → a****9（只留頭尾，不洩漏完整信箱）。"""
    s = s or ""
    if len(s) <= 2:
        return (s[:1] or "*") + "*"
    return s[0] + "*" * (len(s) - 2) + s[-1]


def create_reset(email: str) -> dict | None:
    """產生「忘記密碼」重設碼。

    找不到會員就回 None（**不告訴對方帳號存不存在**，避免被拿去探測帳號）。
    """
    email = (email or "").strip().lower()
    if "@" not in email:
        return None
    row = db.one("SELECT id, email FROM members WHERE email=?"
                 " AND COALESCE(status,'') <> 'deleted'", (email,))
    if not row:
        return None
    token = secrets.token_urlsafe(24)
    now = time.time()
    db.execute(
        "INSERT INTO password_resets(token, member_id, email, created_at, expires_at)"
        " VALUES(?,?,?,?,?)", (token, row["id"], email, now, now + RESET_TTL))
    return {"token": token, "email": email, "expires_at": now + RESET_TTL}


def use_reset(token: str, new_password: str) -> dict:
    """用重設碼改密碼（一次性、30 分鐘內有效）。"""
    token = (token or "").strip()
    if len(new_password or "") < 6:
        raise BadRequest("密碼至少 6 個字", code="WEAK_PASSWORD")
    row = db.one("SELECT * FROM password_resets WHERE token=?", (token,))
    if not row:
        raise BadRequest("這個重設連結無效，請重新申請", code="BAD_RESET")
    if row["used"]:
        raise BadRequest("這個重設連結已經用過了，請重新申請", code="RESET_USED")
    if float(row["expires_at"]) < time.time():
        raise BadRequest("重設連結已過期（30 分鐘），請重新申請", code="RESET_EXPIRED")
    db.execute("UPDATE password_resets SET used=1 WHERE token=?", (token,))
    db.execute("UPDATE members SET password=? WHERE id=?",
               (_hash(new_password), row["member_id"]))
    return {"member_id": row["member_id"], "email": row["email"] or ""}


def find_account(nickname: str) -> list[str]:
    """忘記帳號：用暱稱找出符合的**完整 Email**（只給內部寄信用，不對外顯示）。

    小羅 2026-10-04：「不然他隨便一個人寫個忘記帳號你就隨便給他一個帳號也不對」
    → 改為：只拿來寄信到那個信箱，畫面不對外揭露任何帳號。
    """
    nk = (nickname or "").strip()
    if len(nk) < 1:
        return []
    rows = db.query("SELECT email, nickname, COALESCE(status,'') AS st FROM members"
                    " WHERE COALESCE(nickname,'') LIKE ? LIMIT 10", (f"%{nk}%",))
    return [(r["email"] or "").strip() for r in rows
            if r["st"] != "deleted" and "@" in (r["email"] or "")]


def tier_of(member_id: str | None) -> str:
    """身分等級：guest（未登入）／free／monthly／lifetime。

    ⚠️ 到期判斷：付費方案過期 → 當成 free（小羅：「到期沒續費就打回會員這邊」）。
    """
    if not member_id:
        return "guest"
    m = get(member_id)
    if not m or (m.get("status") or "active") != "active":
        return "free"
    plan = m.get("plan") or "free"
    if plan == "free":
        return "free"
    exp = m.get("expires_at")
    import time as _t

    if exp and float(exp) < _t.time():
        return "free"          # 過期 → 打回免費會員
    return plan if plan in ("monthly", "lifetime") else "free"
