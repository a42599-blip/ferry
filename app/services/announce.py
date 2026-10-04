"""前台公告（小羅 2026-09-27 要求）。

小羅：「前台也可以留一個區塊做公告。」
用途：系統更新、某平台故障、某平台要取消…這些讓使用者一進站就看到。

⚠️ 公告與 Email 是**兩件事**：
    公告＝前台自己看得到（即時、免費）
    Email＝主動通知會員（要你按了才發，不要每次改東西就寄一次）
"""
from __future__ import annotations

import time as _t
from typing import Optional

from ..core import db
from ..core import timezone as tz_util

LEVELS = {"info": "一般", "warn": "重要", "critical": "緊急"}


def add(title: str, body: str, *, level: str = "info",
        expires_at: Optional[float] = None) -> dict:
    db.execute(
        "INSERT INTO announcements (ts, title, body, level, active, expires_at)"
        " VALUES (?,?,?,?,1,?)",
        (_t.time(), title[:200], body[:3000], level if level in LEVELS else "info", expires_at))
    row = db.one("SELECT * FROM announcements ORDER BY id DESC LIMIT 1")
    return dict(row) if row else {}


def list_all(limit: int = 100) -> list[dict]:
    out = []
    for r in db.query("SELECT * FROM announcements ORDER BY ts DESC LIMIT ?", (limit,)):
        row = dict(r)
        row["when"] = tz_util.fmt(row["ts"], None, "%m-%d %H:%M")
        row["level_label"] = LEVELS.get(row["level"], row["level"])
        row["expired"] = bool(row["expires_at"] and row["expires_at"] < _t.time())
        out.append(row)
    return out


def active(limit: int = 5) -> list[dict]:
    """前台要顯示的公告（只回未過期、已啟用的）。"""
    now = _t.time()
    rows = db.query(
        "SELECT id, title, body, level, ts FROM announcements"
        " WHERE active=1 AND (expires_at IS NULL OR expires_at>?)"
        " ORDER BY ts DESC LIMIT ?", (now, limit))
    return [dict(r) for r in rows]


def set_active(aid: int, on: bool) -> bool:
    db.execute("UPDATE announcements SET active=? WHERE id=?", (1 if on else 0, aid))
    return True


def remove(aid: int) -> bool:
    db.execute("DELETE FROM announcements WHERE id=?", (aid,))
    return True

