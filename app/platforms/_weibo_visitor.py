"""微博「匿名訪客」cookie 取得（不需要登入、不需要使用者 cookies）。

微博現在的匿名呼叫會被導到訪客系統（Sina Visitor System）。
標準流程（公開演算法）：
  1. POST passport.weibo.com/visitor/genvisitor  → 拿 tid
  2. GET  passport.weibo.com/visitor/visitor?a=incarnate&t=<tid> → 拿 SUB / SUBP
  3. 帶著 SUB/SUBP 就能呼叫 weibo.com 的 ajax API

cookie 會快取（有效期內重複使用）。
"""
from __future__ import annotations

import asyncio
import re
import time

from ..core.http import HttpClient

_FP = (
    '{"os":"1","browser":"Chrome131,0,0,0","fonts":"undefined",'
    '"screenInfo":"1440*900*24","plugins":""}'
)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: cookie 快取（(cookie_header, 到期時間)）
_cache: tuple[str, float] | None = None
_lock = asyncio.Lock()

#: 快取多久（秒）——訪客 cookie 壽命不長，保守取 30 分鐘
_TTL = 1800


async def get_visitor_cookie(*, force: bool = False) -> str:
    """取得（並快取）匿名訪客 cookie；失敗回空字串。"""
    global _cache
    async with _lock:
        if not force and _cache and _cache[1] > time.time():
            return _cache[0]

        try:
            cookie = await _fetch()
        except Exception:  # noqa: BLE001
            return ""
        if cookie:
            _cache = (cookie, time.time() + _TTL)
        return cookie


async def _fetch() -> str:
    async with HttpClient(ua=_UA, timeout=20) as http:
        # ① genvisitor
        resp = await http.post(
            "https://passport.weibo.com/visitor/genvisitor",
            data={"cb": "gen_callback", "fp": _FP},
            headers={"Referer": "https://passport.weibo.com/visitor/visitor"},
        )
        m = re.search(r'"tid":"([^"]+)"', resp.text)
        if not m:
            return ""
        tid = m.group(1)

        # ② incarnate → Set-Cookie
        rand = int(time.time() * 1000)
        url = (
            "https://passport.weibo.com/visitor/visitor"
            f"?a=incarnate&t={tid}&w=2&c=095&gc=&cb=cross_domain&from=weibo&_rand={rand}"
        )
        resp2 = await http.get(url, headers={"Referer": "https://passport.weibo.com/"})

    pairs: list[str] = []
    for ck in resp2.headers.get_list("set-cookie"):
        head = ck.split(";", 1)[0]
        name = head.split("=", 1)[0].strip()
        if name in ("SUB", "SUBP", "SRT", "ALF"):
            pairs.append(head)
    return "; ".join(pairs)


def cookie_header() -> dict[str, str]:
    return {"User-Agent": _UA}


async def ajax_headers() -> dict[str, str]:
    ck = await get_visitor_cookie()
    h = {
        "User-Agent": _UA,
        "Referer": "https://weibo.com/",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    if ck:
        h["Cookie"] = ck
    return h


# ── mblogid ↔ mid 轉換（微博的 base62 編碼，公開演算法）─────
_B62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def mblogid_to_mid(mblogid: str) -> str:
    """把網址中的英數 id（mblogid）轉成數字 mid。失敗回空字串。"""
    if not mblogid:
        return ""
    n = 0
    for ch in mblogid:
        if ch not in _B62:
            return ""
        n = n * 62 + _B62.index(ch)
    mid = str(n)
    # 微博的 mid 至少 16 碼，左補 0
    return mid.zfill(16) if len(mid) < 16 else mid
