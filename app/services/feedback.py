"""使用者回報問題（任何人都能送，不限會員）。

小羅 2026-09-27 的要求：
  1. 「回報問題」要拉出來到首頁 —— 不是只有會員才能用
  2. 「客戶抖音下載不了可以回報給我，我馬上收到訊息」
  3. ⚠️ **不要一則一封信**（會很恐怖）→ 用**彙總**寄
"""
from __future__ import annotations

import time as _t
from typing import Optional

from ..core import db
from ..core import timezone as tz_util


def add(*, message: str, device_id: Optional[str] = None, contact: Optional[str] = None,
        platform: Optional[str] = None, url: Optional[str] = None,
        app_version: Optional[str] = None) -> dict:
    """寫入一則回報，回傳該筆資料。"""
    now = _t.time()
    db.execute(
        "INSERT INTO feedback (ts, device_id, message, contact, platform, url, app_version)"
        " VALUES (?,?,?,?,?,?,?)",
        (now, device_id, message[:1500], (contact or "")[:200] or None,
         platform, (url or "")[:500] or None, app_version))
    row = db.one("SELECT * FROM feedback ORDER BY id DESC LIMIT 1")
    return dict(row) if row else {}


def list_all(*, days: int = 30, only_new: bool = False, limit: int = 300) -> list[dict]:
    since = _t.time() - days * 86400
    sql = "SELECT * FROM feedback WHERE ts>=?"
    if only_new:
        sql += " AND (handled IS NULL OR handled=0)"
    sql += " ORDER BY ts DESC LIMIT ?"
    out = []
    for r in db.query(sql, (since, limit)):
        row = dict(r)
        row["when"] = tz_util.fmt(row["ts"], None, "%m-%d %H:%M")
        row["handled"] = bool(row.get("handled"))
        out.append(row)
    return out


def counts(days: int = 30) -> dict:
    since = _t.time() - days * 86400
    total = int(db.scalar("SELECT COUNT(*) FROM feedback WHERE ts>=?", (since,)))
    new = int(db.scalar(
        "SELECT COUNT(*) FROM feedback WHERE ts>=? AND (handled IS NULL OR handled=0)", (since,)))
    return {"total": total, "new": new, "handled": max(0, total - new)}


def mark_handled(fid: int, note: str = "") -> bool:
    db.execute("UPDATE feedback SET handled=1, note=? WHERE id=?", (note[:400], fid))
    return True


def unhandled_since(ts: float) -> list[dict]:
    """某個時間點之後、還沒處理的回報（給彙總信／告警用）。"""
    return [dict(r) for r in db.query(
        "SELECT * FROM feedback WHERE ts>=? AND (handled IS NULL OR handled=0)"
        " ORDER BY ts", (ts,))]


def digest_since(ts: float) -> tuple[str, str]:
    """把一段時間內的回報**彙總成一封信**（不是一則一封）。"""
    rows = unhandled_since(ts)
    if not rows:
        return "", ""
    lines = [f"【轉運站】使用者回報彙總（{len(rows)} 則）", ""]
    for r in rows[:40]:
        when = tz_util.fmt(r["ts"], None, "%m-%d %H:%M")
        plat = r.get("platform") or "未標示"
        lines.append(f"• [{when}] ({plat}) {r['message'][:160]}")
        if r.get("contact"):
            lines.append(f"    聯絡：{r['contact']}")
        if r.get("url"):
            lines.append(f"    連結：{r['url'][:110]}")
    if len(rows) > 40:
        lines.append(f"…另有 {len(rows) - 40} 則，請到後台查看")
    return f"【轉運站】使用者回報彙總（{len(rows)} 則）", "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  客服處理（小羅 2026-09-27）
#  「我點這個回報，就能直接幫他加次數、回訊息給他」
#  「能加也能減」
# ══════════════════════════════════════════════════════════════════

def reply(fid: int, message: str, *, action: str = "",
          mark_handled: bool = True) -> dict | None:
    """回覆客戶並記錄「處理了什麼」。

    ⚠️ 小羅 2026-09-28：「不要只有一次機會，我可以重複回覆這個訊息。」
    → 每次都**新增一筆**回覆紀錄（feedback_replies），不覆蓋前一次。
    → feedback.reply 只保留「最新一筆」（給列表快速顯示用）。
    """
    msg = (message or "")[:1500]
    act = (action or "")[:200]
    db.execute(
        "INSERT INTO feedback_replies(feedback_id, ts, reply, action)"
        " VALUES(?,?,?,?)", (fid, _t.time(), msg, act))
    db.execute(
        "UPDATE feedback SET reply=?, replied_at=?, action=?, handled=?"
        " WHERE id=?", (msg, _t.time(), act, 1 if mark_handled else 0, fid))
    row = db.one("SELECT * FROM feedback WHERE id=?", (fid,))
    out = dict(row) if row else None
    if out is not None:
        out["replies"] = replies_of(fid)
    return out


def replies_of(fid: int, limit: int = 50) -> list[dict]:
    """某一筆回報的所有回覆紀錄（新→舊）。"""
    rows = db.query(
        "SELECT id, ts, reply, action FROM feedback_replies"
        " WHERE feedback_id=? ORDER BY ts DESC LIMIT ?", (fid, limit))
    out = []
    for r in rows:
        d = dict(r)
        d["when"] = tz_util.fmt(d["ts"], None, "%m-%d %H:%M")
        out.append(d)
    return out


def for_device(device_id: str, member_id: str | None = None, limit: int = 50) -> list[dict]:
    """這個客戶收到的「已回覆」訊息（前台顯示給客戶看）。

    ⚠️ 小羅 2026-09-27：「訪客是這個設備跟我對話；他是會員就直接回他帳號。」
    所以兩種都要查：
      · 訪客 → 用裝置 ID 找
      · 會員 → 除了他的裝置，也把他帳號名下所有裝置的回報一起找出來
        （這樣換裝置登入還是看得到客服回覆）
    """
    devs: list[str] = [device_id] if device_id else []
    if member_id:
        for r in db.query("SELECT device_id FROM members WHERE id=? AND device_id<>''",
                          (member_id,)):
            devs.append(r["device_id"])
        for r in db.query("SELECT device_id FROM devices WHERE member_id=? AND device_id<>''",
                          (member_id,)):
            devs.append(r["device_id"])
        for r in db.query("SELECT DISTINCT device_id FROM events"
                          " WHERE device_id IS NOT NULL AND device_id<>'' AND meta LIKE ?",
                          ("%" + member_id + "%",)):
            devs.append(r["device_id"])
    devs = [d for d in dict.fromkeys(devs) if d]
    if not devs:
        return []
    marks = ",".join("?" for _ in devs)
    # ⚠️ 從「回覆紀錄」取（一筆回報可以有多筆回覆，全部都給客戶看）
    rows = db.query(
        "SELECT r.id AS rid, r.feedback_id, r.ts AS replied_at, r.reply, r.action,"
        " f.message, f.device_id"
        " FROM feedback_replies r JOIN feedback f ON f.id = r.feedback_id"
        " WHERE f.device_id IN (" + marks + ")"
        " ORDER BY r.ts DESC LIMIT ?",
        tuple(devs) + (limit,))
    return [dict(r) for r in rows]


def unhandled_for(device_id: str, member_id: str | None = None) -> int:
    """這個客戶還有幾則沒處理（前台可以提示「處理中」）。"""
    devs = [device_id] if device_id else []
    if member_id:
        for r in db.query("SELECT device_id FROM members WHERE id=? AND device_id<>''",
                          (member_id,)):
            devs.append(r["device_id"])
    devs = [d for d in dict.fromkeys(devs) if d]
    if not devs:
        return 0
    marks = ",".join("?" for _ in devs)
    return int(db.scalar(
        "SELECT COUNT(*) FROM feedback WHERE device_id IN (" + marks + ")"
        " AND (handled IS NULL OR handled=0)", tuple(devs)) or 0)
