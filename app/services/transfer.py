"""無損傳輸（WebRTC signaling）— 我們只交換名片，檔案走區域網，零儲存零流量。

（規格書第 9 章）

流程：
1. 兩台裝置各自向 /api/signal/join 取得 6 位配對碼（或輸入對方的碼）
2. 透過 /api/signal/poll 交換 SDP / ICE（名片）
3. WebRTC 連上後，檔案在兩台裝置之間直接傳（不經我們）

我們只做三件事：發碼、交換名片、記錄「配對成功／傳輸完成」的匿名統計。
"""
from __future__ import annotations

import random
import string
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..core import db
from ..core import timezone as tz_util
from ..core.errors import BadRequest
from ..services import auth, events, quota

router = APIRouter()

CODE_TTL = 120          # 配對碼 2 分鐘有效（規格書第 9 章）
ONLINE_TTL = 40         # 多久沒心跳算離線
#: 關頁面／重新整理時，先給幾秒寬限：重新整理會馬上回來（poll 會清掉），關掉就不會回來
LEAVE_GRACE = 6
#: 閒置上限（小羅 2026-09-29：「5 分鐘以上沒有動作就自動斷開」）
IDLE_TTL = 300
MAX_BOXES = 2000        # 房間上限，避免爆掉
MAX_JOIN_FAILS = 8      # 同一來源 2 分鐘內錯幾次就擋（規格書：錯誤次數限制）
FAIL_WINDOW = 120


@dataclass
class Room:
    code: str
    created_at: float
    peers: dict[str, list[dict]] = field(default_factory=dict)  # peer_id -> 待領取的名片


_rooms: dict[str, Room] = {}
_presence: dict[str, dict] = {}        # peer_id -> {"code":..., "at":..., "ua":...}
_fails: dict[str, list[float]] = {}    # 來源 -> 失敗時間
_leaving: dict[str, float] = {}        # peer_id -> 寬限到什麼時候（關頁面用）


def _touch(peer_id: str, code: str, name: str | None = None) -> None:
    """標記這個 peer 還活著（有活動）→ 順便取消「離開寬限」。

    ⚠️ 2026-09-29（小羅要「同一個裝置回來自動重連」）：只要還有人在活動，房間就**不要過期**。
       原本房間是「建立後 120 秒」就自動消失 → 自動重連會失敗（對方明明還在線上）。
    """
    _leaving.pop(peer_id, None)
    old = _presence.get(peer_id) or {}
    _presence[peer_id] = {"code": code, "at": time.time(),
                          "name": old.get("name", "") if name is None else (name or "")}
    room = _rooms.get(code)
    if room is not None:
        room.created_at = time.time()      # 有人活動＝延長房間壽命


def _gone(peer_id: str) -> bool:
    """這個 peer 是不是「已經不在了」（逾時 or 離開寬限已過）。"""
    now = time.time()
    p = _presence.get(peer_id)
    if p is None or now - p["at"] > ONLINE_TTL:
        return True
    until = _leaving.get(peer_id)
    return bool(until and now > until)


def _live_peers(room: Room, me: str) -> list[str]:
    """房間裡「還活著」的其他裝置（逾時的不要列出來）。"""
    return [p for p in room.peers if p != me and not _gone(p)]


def _new_code() -> str:
    while True:
        code = "".join(random.choices(string.digits, k=6))
        if code not in _rooms:
            return code


def _cleanup() -> None:
    now = time.time()
    for code in [c for c, r in _rooms.items() if now - r.created_at > CODE_TTL]:
        _rooms.pop(code, None)
    for pid in [p for p, v in _presence.items() if now - v["at"] > ONLINE_TTL]:
        _presence.pop(pid, None)
    for pid in [p for p, until in _leaving.items() if now > until]:
        # ⚠️ 寬限時間過了＝真的離開（否則還要再等 ONLINE_TTL 40 秒 → 小羅：「關掉網頁要自動斷開」）
        _leaving.pop(pid, None)
        _presence.pop(pid, None)
    # ⚠️ 2026-09-29（小羅：「配對會自動斷開嗎？」）：逾時／已離開的人要**真的從房間移除**，
    #    否則對方關掉網頁後，另一邊永遠還看到「對方在線」→ 不會自動斷開。
    for room in _rooms.values():
        for pid in [p for p in list(room.peers) if _gone(p)]:
            room.peers.pop(pid, None)
    while len(_rooms) > MAX_BOXES:
        _rooms.pop(next(iter(_rooms)))


def _check_fails(src: str) -> None:
    now = time.time()
    hist = [t for t in _fails.get(src, []) if now - t < FAIL_WINDOW]
    _fails[src] = hist
    if len(hist) >= MAX_JOIN_FAILS:
        raise BadRequest("配對碼錯誤次數過多，請稍後再試", code="PAIRING_RATE")


def _mark_fail(src: str) -> None:
    _fails.setdefault(src, []).append(time.time())


# ── 配對過的裝置（規格書：配對過永久記住）────────────
def _remember_pair(a: str, b: str) -> None:
    try:
        db.execute(
            "INSERT OR REPLACE INTO pairings(device_a, device_b, last_at) VALUES(?,?,?)",
            tuple(sorted((a, b))) + (time.time(),),
        )
    except Exception:  # noqa: BLE001
        pass


def _known_peers(peer_id: str) -> list[str]:
    try:
        rows = db.query(
            "SELECT CASE WHEN device_a=? THEN device_b ELSE device_a END AS other"
            " FROM pairings WHERE device_a=? OR device_b=? ORDER BY last_at DESC LIMIT 10",
            (peer_id, peer_id, peer_id),
        )
        return [r["other"] for r in rows]
    except Exception:  # noqa: BLE001
        return []


# ── 請求模型 ──────────────────────────────────────────
class JoinIn(BaseModel):
    code: str | None = None
    peer_id: str
    name: str | None = None
    #: 關頁面時帶 grace（秒）：先給寬限，重新整理馬上回來就不算離開（小羅 2026-09-29）
    grace: float = 0


class SignalIn(BaseModel):
    code: str
    from_peer: str
    to_peer: str
    payload: dict


class PairIn(BaseModel):
    peer_id: str
    target: str


class DoneIn(BaseModel):
    peer_id: str
    ok: bool = True
    files: int = 0
    total_bytes: int = 0
    duration_ms: int | None = None


# ── API ───────────────────────────────────────────────
@router.post("/join")
async def join(body: JoinIn):
    _cleanup()
    if body.code:
        _check_fails(body.peer_id)
        room = _rooms.get(body.code)
        if room is None:
            _mark_fail(body.peer_id)
            raise BadRequest("配對碼不存在或已過期", code="PAIRING_NOT_FOUND")
        # 房間滿了（第 3 台）
        if len(room.peers) >= 2 and body.peer_id not in room.peers:
            raise BadRequest("這個配對碼已經有兩台裝置了", code="PAIRING_FULL")
    else:
        room = Room(code=_new_code(), created_at=time.time())
        _rooms[room.code] = room

    room.peers.setdefault(body.peer_id, [])
    _touch(body.peer_id, room.code, body.name)

    peers = _live_peers(room, body.peer_id)
    if peers:
        _remember_pair(body.peer_id, peers[0])
        events.track("transfer_pair", device_id=body.peer_id, mode="code",
                     meta={"code": room.code, "with": peers[0]})

    return {
        "ok": True,
        "code": room.code,
        "ttl": CODE_TTL,
        "peers": peers,
        "known": [p for p in _known_peers(body.peer_id)
                  if p in _presence and p != body.peer_id],
    }


@router.post("/pair")
async def pair(body: PairIn):
    """用「配對過的裝置」直接重連（不用再輸入 6 位碼）。"""
    _cleanup()
    target = _presence.get(body.target)
    if target is None:
        raise BadRequest("對方的裝置目前不在線上，請改用 6 位碼", code="PAIRING_NOT_FOUND")
    room = _rooms.get(target["code"])
    if room is None:
        raise BadRequest("配對已過期，請重新產生配對碼", code="PAIRING_NOT_FOUND")
    room.peers.setdefault(body.peer_id, [])
    _touch(body.peer_id, room.code)
    _remember_pair(body.peer_id, body.target)
    events.track("transfer_pair", device_id=body.peer_id, mode="known-peer",
                 meta={"with": body.target})
    return {"ok": True, "code": room.code, "peers": _live_peers(room, body.peer_id)}


class ClaimIn(BaseModel):
    peer_id: str
    files: int = 1


@router.post("/claim")
async def claim(body: ClaimIn, request: Request):
    """開始傳送前先扣一次「無損傳輸」免費次數。

    ⚠️ 小羅 2026-09-27：
      - 無損傳輸的免費次數與無水印下載**分開計算**（各自 5 次／日）
      - 會員（已購買任一方案）→ 全站無限制（下載、傳輸都是）
      - 每日依裝置所在地時區歸零
    """
    subject = auth.current_subject(request)
    tz = tz_util.from_request(request)
    used = quota.consume("transfer", subject, tz_name=tz)   # 用完會丟 QuotaExceeded
    return {"ok": True, "quota": used}


@router.post("/send")
async def send(body: SignalIn):
    room = _rooms.get(body.code)
    if room is None:
        raise BadRequest("配對碼不存在或已過期", code="PAIRING_NOT_FOUND")
    _touch(body.from_peer, body.code)
    box = room.peers.setdefault(body.to_peer, [])
    box.append({"from": body.from_peer, "payload": body.payload, "at": time.time()})
    return {"ok": True, "queued": len(box)}


@router.get("/poll")
async def poll(code: str, peer_id: str):
    # ⚠️ 每次輪詢都清一次：對方關掉頁面／逾時才會被「即時」從房間移除（小羅 2026-09-29）
    _cleanup()
    room = _rooms.get(code)
    if room is None:
        raise BadRequest("配對碼不存在或已過期", code="PAIRING_NOT_FOUND")
    _touch(peer_id, code)
    box = room.peers.setdefault(peer_id, [])
    msgs, box[:] = list(box), []
    return {"ok": True, "messages": msgs, "peers": _live_peers(room, peer_id)}


@router.post("/done")
async def done(body: DoneIn):
    """傳輸完成回報（只記匿名統計，不記檔名內容）。"""
    events.track("transfer_done", device_id=body.peer_id,
                 result="ok" if body.ok else "fail",
                 size=body.total_bytes or None, latency_ms=body.duration_ms,
                 meta={"files": body.files})
    return {"ok": True}


@router.post("/leave")
async def leave(body: JoinIn):
    """離開配對。

    ⚠️ 小羅 2026-09-29：「有一方關掉網頁就要自動斷開；但**重新整理不算**。」
       瀏覽器沒辦法分辨「關閉」和「重新整理」，所以關頁面時前台會帶 `grace`（例如 6 秒）：
         · 是重新整理 → 幾秒內就會重新 poll／join → `_touch()` 會把寬限取消 ✅ 不算離開
         · 是真的關掉 → 寬限一過，`_cleanup()` 就會把這個人從房間移除 ✅ 自動斷開
    """
    grace = float(body.grace or 0)
    if grace > 0:
        _leaving[body.peer_id] = time.time() + min(grace, 60.0)
        return {"ok": True, "grace": grace}

    room = _rooms.get(body.code or "")
    if room:
        room.peers.pop(body.peer_id, None)
        if not room.peers:
            _rooms.pop(room.code, None)
    _presence.pop(body.peer_id, None)
    _leaving.pop(body.peer_id, None)
    return {"ok": True}
