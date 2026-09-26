"""無損傳輸（WebRTC signaling）— 我們只交換名片，檔案走區域網，零儲存零流量。

（規格書第 9 章）

流程：
1. 兩台裝置各自向 /api/signal/join 取得 6 位配對碼（或輸入對方的碼）
2. 透過 /api/signal/poll 交換 SDP / ICE（名片）
3. WebRTC 連上後，檔案在兩台裝置間直接傳（不經我們）
"""
from __future__ import annotations

import random
import string
import time
from dataclasses import dataclass, field

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.errors import BadRequest

router = APIRouter()

CODE_TTL = 120          # 配對碼 2 分鐘有效（規格書第 9 章）
MAX_BOXES = 2000        # 記憶體上限，避免爆掉


@dataclass
class Room:
    code: str
    created_at: float
    peers: dict[str, list[dict]] = field(default_factory=dict)  # peer_id -> 待領取的名片


_rooms: dict[str, Room] = {}


def _new_code() -> str:
    while True:
        code = "".join(random.choices(string.digits, k=6))
        if code not in _rooms:
            return code


def _cleanup() -> None:
    now = time.time()
    for code in [c for c, r in _rooms.items() if now - r.created_at > CODE_TTL]:
        _rooms.pop(code, None)
    while len(_rooms) > MAX_BOXES:
        _rooms.pop(next(iter(_rooms)))


class JoinIn(BaseModel):
    code: str | None = None
    peer_id: str


class SignalIn(BaseModel):
    code: str
    from_peer: str
    to_peer: str
    payload: dict


@router.post("/join")
async def join(body: JoinIn):
    _cleanup()
    if body.code:
        room = _rooms.get(body.code)
        if room is None:
            raise BadRequest("配對碼不存在或已過期")
    else:
        room = Room(code=_new_code(), created_at=time.time())
        _rooms[room.code] = room
    room.peers.setdefault(body.peer_id, [])
    return {"ok": True, "code": room.code, "ttl": CODE_TTL, "peers": list(room.peers.keys())}


@router.post("/send")
async def send(body: SignalIn):
    room = _rooms.get(body.code)
    if room is None:
        raise BadRequest("配對碼不存在或已過期")
    box = room.peers.setdefault(body.to_peer, [])
    box.append({"from": body.from_peer, "payload": body.payload, "at": time.time()})
    return {"ok": True, "queued": len(box)}


@router.get("/poll")
async def poll(code: str, peer_id: str):
    room = _rooms.get(code)
    if room is None:
        raise BadRequest("配對碼不存在或已過期")
    box = room.peers.setdefault(peer_id, [])
    msgs, box[:] = list(box), []
    return {"ok": True, "messages": msgs, "peers": list(room.peers.keys())}


@router.post("/leave")
async def leave(body: JoinIn):
    room = _rooms.get(body.code or "")
    if room:
        room.peers.pop(body.peer_id, None)
        if not room.peers:
            _rooms.pop(room.code, None)
    return {"ok": True}
