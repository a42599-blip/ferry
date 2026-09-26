"""B 站 WBI 簽章（w_rid）。

B 站自 2023 起要求 `w_rid` 簽章，沒有它會回 **412 Precondition Failed**。
演算法（公開）：
    1. 從 nav 介面取 img_url / sub_url → 取出 img_key / sub_key
    2. 兩把 key 併起來，用固定的 mixinKeyEncTab 重排 → mixin_key（取前 32 字）
    3. 參數排序 → 過濾特殊字元 → urlencode → 加時間戳 wts
    4. MD5(query + mixin_key) = w_rid

註：這裡只放「演算法」，不含任何 v8i8 的業務邏輯。
"""
from __future__ import annotations

import hashlib
import time
import urllib.parse
from typing import Any

# B 站固定的重排表（公開常數）
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

_FILTER = "!'()*"


def _mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in _MIXIN_KEY_ENC_TAB)[:32]


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def extract_keys(nav: dict[str, Any]) -> tuple[str, str]:
    """從 nav 介面回應取出 (img_key, sub_key)。"""
    wbi = (nav.get("data") or {}).get("wbi_img") or {}
    img_url = wbi.get("img_url") or ""
    sub_url = wbi.get("sub_url") or ""
    img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
    return img_key, sub_key


def sign(params: dict[str, Any], img_key: str, sub_key: str,
         *, wts: int | None = None) -> dict[str, Any]:
    """回傳已簽章的參數（含 wts 與 w_rid）。"""
    mixin = _mixin_key(img_key + sub_key)
    signed = dict(params)
    signed["wts"] = int(wts if wts is not None else time.time())

    # 依 key 排序 → 過濾特殊字元 → 串成 query
    items = sorted(signed.items())
    clean = {
        k: "".join(ch for ch in str(v) if ch not in _FILTER)
        for k, v in items
    }
    query = urllib.parse.urlencode(clean)
    signed["w_rid"] = _md5(query + mixin)
    return signed


def sign_query(params: dict[str, Any], img_key: str, sub_key: str,
               *, wts: int | None = None) -> str:
    signed = sign(params, img_key, sub_key, wts=wts)
    return urllib.parse.urlencode(sorted(signed.items()))
