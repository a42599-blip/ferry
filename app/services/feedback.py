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
        row["when"] = _t.strftime("%m-%d %H:%M", _t.localtime(row["ts"]))
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
        when = _t.strftime("%m-%d %H:%M", _t.localtime(r["ts"]))
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

    action 例：「補回下載次數 3 次」「加 7 天」
    → 客戶下次進站會在公告區看到這則回覆。
    """
    db.execute(
        "UPDATE feedback SET reply=?, replied_at=?, action=?, handled=?"
        " WHERE id=?",
        ((message or "")[:1500], _t.time(), (action or "")[:200],
         1 if mark_handled else 0, fid))
    row = db.one("SELECT * FROM feedback WHERE id=?", (fid,))
    return dict(row) if row else None


def for_device(device_id: str, member_id: str | None = None, limit: int = 5) -> list[dict]:
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
    rows = db.query(
        "SELECT id, message, reply, replied_at, action FROM feedback"
        " WHERE device_id IN (" + marks + ") AND reply IS NOT NULL AND reply<>''"
        " ORDER BY replied_at DESC LIMIT ?",
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
