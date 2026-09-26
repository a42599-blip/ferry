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

from fastapi import APIRouter
from pydantic import BaseModel

from ..core import db
from ..core.errors import BadRequest
from ..services import events

router = APIRouter()

CODE_TTL = 120          # 配對碼 2 分鐘有效（規格書第 9 章）
ONLINE_TTL = 40         # 多久沒心跳算離線
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
    _presence[body.peer_id] = {"code": room.code, "at": time.time(), "name": body.name or ""}

    peers = [p for p in room.peers if p != body.peer_id]
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
    _presence[body.peer_id] = {"code": room.code, "at": time.time()}
    _remember_pair(body.peer_id, body.target)
    events.track("transfer_pair", device_id=body.peer_id, mode="known-peer",
                 meta={"with": body.target})
    return {"ok": True, "code": room.code,
            "peers": [p for p in room.peers if p != body.peer_id]}


@router.post("/send")
async def send(body: SignalIn):
    room = _rooms.get(body.code)
    if room is None:
        raise BadRequest("配對碼不存在或已過期", code="PAIRING_NOT_FOUND")
    _presence[body.from_peer] = {
        "code": body.code, "at": time.time(),
        "name": _presence.get(body.from_peer, {}).get("name", ""),
    }
    box = room.peers.setdefault(body.to_peer, [])
    box.append({"from": body.from_peer, "payload": body.payload, "at": time.time()})
    return {"ok": True, "queued": len(box)}


@router.get("/poll")
async def poll(code: str, peer_id: str):
    room = _rooms.get(code)
    if room is None:
        raise BadRequest("配對碼不存在或已過期", code="PAIRING_NOT_FOUND")
    _presence[peer_id] = {
        "code": code, "at": time.time(),
        "name": _presence.get(peer_id, {}).get("name", ""),
    }
    box = room.peers.setdefault(peer_id, [])
    msgs, box[:] = list(box), []
    return {"ok": True, "messages": msgs,
            "peers": [p for p in room.peers if p != peer_id]}


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
    room = _rooms.get(body.code or "")
    if room:
        room.peers.pop(body.peer_id, None)
        if not room.peers:
            _rooms.pop(room.code, None)
    _presence.pop(body.peer_id, None)
    return {"ok": True}
