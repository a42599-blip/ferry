"""抖音系（抖音／西瓜）共用的官方 API 解析。

抽出來的理由：抖音與西瓜有兩份幾乎一樣的 a_bogus 官方 API 流程，
放在一起才不會「改一邊忘一邊」（規格書 21-5：修改流程）。

回傳 `VideoInfo`；失敗回 None 或丟 PlatformError。
"""
from __future__ import annotations

import asyncio
import re

from ..core.errors import PlatformError
from ..core.http import HttpClient
from ..core.models import VideoInfo

DETAIL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
TTWID = "https://ttwid.bytedance.com/ttwid/union/register/"
#: a_bogus 的 ua_code 綁這組 UA → 不可改
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")

_ttwid_cache: str | None = None


def _abogus():
    from ._douyin_abogus import ABogus

    return ABogus()


def build_params(aweme_id: str) -> dict:
    return {
        "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
        "pc_client_type": "1", "version_code": "190500", "version_name": "19.5.0",
        "cookie_enabled": "true", "screen_width": "1920", "screen_height": "1080",
        "browser_language": "zh-CN", "browser_platform": "Win32", "browser_name": "Chrome",
        "browser_online": "true", "engine_name": "Blink", "os_name": "Windows",
        "os_version": "10", "platform": "PC", "browser_version": "90.0.4430.212",
        "engine_version": "90.0.4430.212", "cpu_core_num": "12", "device_memory": "8",
        "aweme_id": aweme_id,
    }


async def _ttwid(http: HttpClient) -> str | None:
    global _ttwid_cache
    if _ttwid_cache:
        return _ttwid_cache
    try:
        resp = await http.post(TTWID, json={
            "region": "cn", "aid": 1768, "needFid": False, "service": "www.ixigua.com",
            "migrate_info": {"ticket": "", "source": "node"},
            "cbUrlProtocol": "https", "union": True,
        })
    except Exception:  # noqa: BLE001
        return None
    for ck in resp.headers.get_list("set-cookie"):
        m = re.search(r"ttwid=([^;]+)", ck)
        if m:
            _ttwid_cache = m.group(1)
            return _ttwid_cache
    return None


async def fetch_detail(aweme_id: str) -> dict | None:
    """打抖音官方 Web API（a_bogus 簽章 ＋ ttwid），回傳 aweme_detail。"""
    params = build_params(aweme_id)
    headers = {
        "User-Agent": UA,
        "Referer": f"https://www.douyin.com/video/{aweme_id}",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    for attempt in range(3):
        params["a_bogus"] = _abogus().get_value({k: v for k, v in params.items()
                                                 if k != "a_bogus"})
        async with HttpClient(ua=UA, timeout=20) as http:
            ttwid = await _ttwid(http)
            if ttwid:
                headers["Cookie"] = f"ttwid={ttwid}"
            resp = await http.get(DETAIL, params=params, headers=headers)
            if resp.status_code != 200:
                if attempt < 2:
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                return None
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                return None
        detail = (data or {}).get("aweme_detail")
        if detail:
            return detail
        if attempt < 2:
            await asyncio.sleep(0.8 * (attempt + 1))
    return None


async def resolve_via_douyin(url: str) -> VideoInfo | None:
    """從 douyin.com/video/<id> 網址解析（給抖音、西瓜共用）。"""
    m = re.search(r"/(?:video|note)/(\d{15,25})", url)
    if not m:
        return None
    detail = await fetch_detail(m.group(1))
    if not detail:
        return None
    from .douyin import DouyinResolver

    r = DouyinResolver()
    info = r._build_official(url, detail)
    if info is None:
        raise PlatformError("抖音系 API 沒有回傳可下載的檔案")
    return info
