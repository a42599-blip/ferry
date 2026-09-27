"""B 站解析（官方 API ＋ WBI 簽章）。

為何自己簽章：
    B 站自 2023 起要求 `w_rid`（WBI），沒有它一律 **412**。
    簽章是「純演算法」，不依賴 IP／Cookie → 本機與雲端都能用。
    （演算法見 `_bili_wbi.py`，公開常數，非抄襲任何現有專案）

畫質：`playurl` 帶 `fnval=16` 走 DASH → 可拿多軌（4K/2K/1080/720/480…）。
下載：B 站 CDN 擋 Origin → 用「直連下載」模式（規格書第 8 章 A 模式）。
"""
from __future__ import annotations

import re

from ..core.errors import PlatformBlocked, PlatformChanged, PlatformError, PlatformTimeout
from ..core.http import HttpClient
from ..core.models import Format, VideoInfo
from . import _bili_wbi as wbi
from .base import Resolver

_NAV = "https://api.bilibili.com/x/web-interface/nav"
_VIEW = "https://api.bilibili.com/x/web-interface/wbi/view"
_PLAY = "https://api.bilibili.com/x/player/wbi/playurl"
_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_URL_RE = re.compile(r"https?://(?:www\.|m\.)?bilibili\.com/|https?://b23\.tv/", re.I)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_HEADERS = {
    "User-Agent": _UA,
    "Referer": "https://space.bilibili.com/",   # ← 跟 v8i8 一樣（不是 bilibili.com/）
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
}


class BilibiliResolver(Resolver):
    name = "bilibili"
    label = "B站"
    hosts = ("bilibili.com", "b23.tv")

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    #: 短連結用的手機 UA（b23.tv 對資料中心 IP 較友善）
    _UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

    async def _bvid_from_short(self, url: str) -> str | None:
        """b23.tv 短連結 → 取得 BV 號。

        三條路都試（雲端 IP 有時會被短網址服務擋）：
          ① 不跟隨轉址，直接讀 Location 標頭
          ② 跟隨轉址後看最終網址
          ③ 讀頁面內容找 BV 號
        """
        import httpx

        for ua in (_UA, self._UA_MOBILE):
            # ① 只讀 Location（最省、最不容易被擋）
            try:
                async with httpx.AsyncClient(timeout=12, follow_redirects=False,
                                             headers={"User-Agent": ua, "Referer": "https://www.bilibili.com/"}) as c:
                    r = await c.get(url)
                    loc = r.headers.get("location") or ""
                    bv = self._extract_bvid(loc)
                    if bv:
                        return bv
            except Exception:  # noqa: BLE001
                pass

            # ② ③ 跟隨轉址 ／ 直接讀頁面
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                             headers={"User-Agent": ua, "Referer": "https://www.bilibili.com/"}) as c:
                    r = await c.get(url)
                    bv = self._extract_bvid(str(r.url)) or self._extract_bvid(r.text)
                    if bv:
                        return bv
            except Exception:  # noqa: BLE001
                pass
        return None

    async def resolve(self, url: str) -> VideoInfo:
        bvid = self._extract_bvid(url)
        if not bvid:
            # b23.tv 短連結：先跟隨轉址拿到 BV 號
            bvid = await self._bvid_from_short(url)
        if not bvid:
            raise PlatformChanged("找不到 B 站影片編號（BV 號）", platform=self.name)
        try:
            async with HttpClient(ua=_UA) as http:
                # ★關鍵：先拜訪首頁 → 自動取得 buvid3/buvid4 訪客 Cookie
                # （沒有這一步，API 一律回 412；v8i8 是直接寫死 Cookie，我們用自動取得）
                try:
                    await http.get("https://www.bilibili.com/", headers=_HEADERS)
                except Exception:
                    pass

                nav = await http.get_json(_NAV, headers=_HEADERS)
                img_key, sub_key = wbi.extract_keys(nav)
                if not img_key or not sub_key:
                    raise PlatformChanged("B站 nav 取不到簽章金鑰", platform=self.name)

                view = await http.get_json(
                    _VIEW,
                    params=wbi.sign({"bvid": bvid}, img_key, sub_key),
                    headers=_HEADERS,
                )
                if view.get("code") in (-403, -412):
                    raise PlatformBlocked("B站擋住了（風控）", platform=self.name)
                if view.get("code") != 0:
                    raise PlatformChanged(f"B站回傳異常：{view.get('message')}", platform=self.name)

                d = view["data"]
                cid = d["cid"]

                play = await http.get_json(
                    _PLAY,
                    params=wbi.sign({"bvid": bvid, "cid": cid, "fnval": 16, "fourk": 1},
                                    img_key, sub_key),
                    headers=_HEADERS,
                )
                if play.get("code") != 0:
                    raise PlatformError(f"B站取播放網址失敗：{play.get('message')}", platform=self.name)
        except (PlatformError,):
            raise
        except Exception as exc:  # noqa: BLE001
            raise PlatformTimeout(f"B站解析逾時：{exc}", platform=self.name) from exc

        fmts = self._formats(play.get("data") or {})
        if not fmts:
            raise PlatformError("B站沒有可下載的檔案", platform=self.name)

        return VideoInfo(
            platform=self.name,
            title=d.get("title") or "B站影片",
            cover=d.get("pic") or "",
            source_url=url,
            formats=fmts,
            duration=d.get("duration"),
            author=(d.get("owner") or {}).get("name"),
            extra={"bvid": bvid, "cid": cid},
        )

    @staticmethod
    def _extract_bvid(url: str) -> str | None:
        """從網址抽 BV 號。找不到回 None（不要在這裡拋錯，
        呼叫端才能接著試短連結轉址）。
        """
        m = _BV_RE.search(url)
        return m.group(1) if m else None

    @staticmethod
    def _formats(data: dict) -> list[Format]:
        out: list[Format] = []
        dash = data.get("dash") or {}

        # ⚠️ B站是 DASH：影像、聲音是**兩條獨立網址**。
        #    前端直連只會拿到「無聲影片」→ 必須交給伺服器合併。
        audios = dash.get("audio") or []
        best_audio = ""
        if audios:
            best_audio = max(audios, key=lambda x: x.get("bandwidth") or 0).get(
                "baseUrl") or max(audios, key=lambda x: x.get("bandwidth") or 0).get("base_url") or ""
        bili_hdr = {"Referer": "https://www.bilibili.com/"}

        # 同一高度取位元率最高的一支
        best: dict[int, dict] = {}
        for v in dash.get("video") or []:
            h = int(v.get("height") or 0)
            if h and (h not in best or (v.get("bandwidth") or 0) > (best[h].get("bandwidth") or 0)):
                best[h] = v
        for h, v in sorted(best.items(), reverse=True):
            out.append(Format(
                id=f"{h}p", label=_label(h),
                url=v.get("baseUrl") or v.get("base_url") or "",
                height=h, width=v.get("width"),
                vcodec=(v.get("codecs") or "")[:20],
                quality_score=h,
                # 有聲音軌 → 走伺服器 relay（合併後才不會是默片）
                mode="relay" if best_audio else "direct",
                audio_url=best_audio or None,
                headers=bili_hdr,
            ))

        # 後備：durl（舊格式，音視已合併）
        if not out:
            for i, u in enumerate(data.get("durl") or [], 1):
                out.append(Format(id=f"durl{i}", label=f"預設 {i}", url=u.get("url", ""),
                                  size=u.get("size"), quality_score=50 - i, mode="direct",
                                  headers=bili_hdr))

        # 純音訊（DASH 分離軌）
        if audios:
            a = max(audios, key=lambda x: x.get("bandwidth") or 0)
            out.append(Format(id="audio", label="純音訊",
                              url=a.get("baseUrl") or a.get("base_url") or "",
                              audio=True, ext="m4a", quality_score=10, mode="direct",
                              headers=bili_hdr))
        return [f for f in out if f.url]


def _label(h: int) -> str:
    return {2160: "4K", 1440: "2K", 1080: "1080P", 720: "720P",
            480: "480P", 360: "360P", 240: "240P"}.get(h, f"{h}P")
