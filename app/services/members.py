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
    return {"id": mid, "email": email, "plan": "free"}


def login(email: str, password: str, *, device_id: str | None = None) -> dict:
    email = (email or "").strip().lower()
    row = db.one("SELECT * FROM members WHERE email = ?", (email,))
    if row is None or not _verify(password or "", row["password"] or ""):
        raise BadRequest("Email 或密碼錯誤", code="BAD_CREDENTIALS")
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
    return payload.get("m")


def get(member_id: str) -> dict | None:
    """取單一會員（含方案、付費起始、到期、登入等資訊；客服查詢要用）。"""
    row = db.one(
        "SELECT id, email, plan, tz, device_id, created_at, expires_at, country,"
        " plan_started_at, COALESCE(status,'active') AS status, deleted_at,"
        " last_login_at, COALESCE(login_count,0) AS login_count,"
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


def list_members(limit: int = 200) -> list[dict]:
    rows = db.query(
        "SELECT id, email, plan, device_id, tz, created_at, expires_at FROM members"
        " ORDER BY created_at DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def touch_login(member_id: str) -> None:
    """記錄最後登入時間與次數。"""
    db.execute(
        "UPDATE members SET last_login_at=?, login_count=COALESCE(login_count,0)+1"
        " WHERE id=?", (time.time(), member_id))


def log_plan(member_id: str, email: str | None, from_plan: str | None, to_plan: str,
             *, amount: float = 0.0, expires_at: float | None = None,
             reason: str = "admin", note: str = "") -> None:
    """寫一筆方案變更紀錄（小羅：「要知道付費時間、用什麼費率、剩餘多少」）。"""
    db.execute(
        "INSERT INTO plan_history(member_id, email, from_plan, to_plan, amount,"
        " currency, at, expires_at, reason, note) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (member_id, email, from_plan, to_plan, float(amount or 0), "USD",
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
            "SELECT id, email, plan, device_id, tz, created_at, expires_at"
            " FROM members ORDER BY created_at DESC LIMIT ?", (limit,)):
        row = dict(r)
        row["plan_name"] = (plans.get(row["plan"]) or {}).get("name", row["plan"])
        row["paid"] = row["plan"] not in ("", None, billing.PLAN_FREE)
        out.append(row)
    return out


def delete(member_id: str) -> bool:
    """註銷帳號（小羅要求：要能讓會員自己註銷）。

    ⚠️ 只刪「帳號」本身；這個裝置的匿名統計（events）保留，
       因為那是「不記名的流量數字」，刪掉會讓後台數字對不上。
       但會把裝置與帳號的關聯切斷。
    """
    m = get(member_id)
    if not m:
        return False
    db.execute("UPDATE members SET device_id=NULL WHERE id=?", (member_id,))
    db.execute("DELETE FROM members WHERE id=?", (member_id,))
    # token 是自帶簽章的（沒有查表），所以不用另外清
    return True


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
        " AND expires_at>? AND expires_at<=? AND COALESCE(status,'active')='active'"
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
        " AND expires_at<=? AND COALESCE(status,'active')='active'", (time.time(),))]


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
    """客服查詢：用 Email 或會員 ID 找會員。"""
    kw = (keyword or "").strip()
    if not kw:
        return []
    like = f"%{kw}%"
    rows = db.query(
        "SELECT * FROM members WHERE email LIKE ? OR id LIKE ?"
        " ORDER BY created_at DESC LIMIT ?", (like, like, limit))
    out = []
    for r in rows:
        m = dict(r)
        m["remaining_days"] = remaining_days(m)
        out.append(m)
    return out
