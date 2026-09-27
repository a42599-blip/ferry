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
             tz: str | None = None) -> dict:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 6:
        raise BadRequest("Email 格式不正確", code="BAD_EMAIL")
    if len(password or "") < 6:
        raise BadRequest("密碼至少 6 個字", code="WEAK_PASSWORD")

    if db.one("SELECT id FROM members WHERE email = ?", (email,)):
        raise BadRequest("這個 Email 已經註冊過了", code="EMAIL_TAKEN")

    mid = "u_" + secrets.token_hex(8)
    db.execute(
        "INSERT INTO members(id, email, plan, device_id, tz, created_at) VALUES(?,?,?,?,?,?)",
        (mid, email, "free", device_id, tz, time.time()),
    )
    db.execute("UPDATE members SET password=? WHERE id=?", (_hash(password), mid))
    from . import events

    events.link_member(device_id or "", mid, email)
    events.track("signup", device_id=device_id, meta={"method": "email", "member_id": mid})
    return {"id": mid, "email": email, "plan": "free"}


def login(email: str, password: str, *, device_id: str | None = None) -> dict:
    email = (email or "").strip().lower()
    row = db.one("SELECT * FROM members WHERE email = ?", (email,))
    if row is None or not _verify(password or "", row["password"] or ""):
        raise BadRequest("Email 或密碼錯誤", code="BAD_CREDENTIALS")
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
    row = db.one("SELECT id, email, plan, tz, created_at, expires_at FROM members WHERE id=?",
                 (member_id,))
    return dict(row) if row else None


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


def set_plan(member_id: str, plan: str, expires_at: float | None = None) -> None:
    db.execute("UPDATE members SET plan=?, expires_at=? WHERE id=?",
               (plan, expires_at, member_id))


# ══════════════════════════════════════════════════════════════════
#  會員統計（小羅 2026-09-27 要求）
#  「有多少會員、多少付費會員、付費等級分別幾個、今天新增幾個、
#    付費／未付費／訪客各多少 —— 這些數據都要留。」
# ══════════════════════════════════════════════════════════════════

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

    return {
        "total": total,
        "today_new": today_new,
        "week_new": week_new,
        "free": free_n,
        "paid": paid_n,
        "plans": plans,
        "visitor_devices": visitor_devices,
        "conversion": round(paid_n / total * 100, 1) if total else 0.0,
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
