"""X（Twitter）解析。

策略：
  1) **官方 syndication API**（公開、不需 cookie、不需登入）
     `cdn.syndication.twimg.com/tweet-result?id=<id>&token=<token>`
  2) yt-dlp（有 cookies 時更完整，例如多段影片）
"""
from __future__ import annotations

import math
import re

from ..core.errors import PlatformError
from ..core.http import HttpClient
from ..core.models import Format, VideoInfo
from ._ytdlp import YtDlpResolver

_SYNDICATION = "https://cdn.syndication.twimg.com/tweet-result"
_URL_RE = re.compile(
    r"https?://(?:www\.|mobile\.|m\.)?(?:twitter\.com|x\.com)/|https?://t\.co/", re.I
)
_STATUS = re.compile(r"/status(?:es)?/(\d{10,25})")
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class TwitterXResolver(YtDlpResolver):
    name = "x"
    label = "X"
    hosts = ("x.com", "twitter.com", "t.co")
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    async def resolve(self, url: str) -> VideoInfo:
        try:
            info = await self._via_syndication(url)
            if info is not None:
                return info
        except PlatformError:
            pass
        except Exception:  # noqa: BLE001
            pass
        return await YtDlpResolver.resolve(self, url)

    async def _via_syndication(self, url: str) -> VideoInfo | None:
        tweet_id = await self._tweet_id(url)
        if not tweet_id:
            return None

        # X 規定的 token：以 tweet id 算出的 base36（社群慣用公式）
        token = format(int((int(tweet_id) / 1e15) * math.pi), "x").replace("0", "")
        headers = {"User-Agent": _UA, "Accept": "application/json"}
        async with HttpClient(ua=_UA, timeout=20) as http:
            try:
                data = await http.get_json(
                    _SYNDICATION, params={"id": tweet_id, "token": token}, headers=headers
                )
            except Exception:  # noqa: BLE001
                return None

        if not isinstance(data, dict) or not data.get("text"):
            return None

        media = data.get("mediaDetails") or []
        fmts: list[Format] = []
        seen: set[str] = set()
        cover = ""

        for item in media:
            if not isinstance(item, dict):
                continue
            cover = cover or item.get("media_url_https") or ""
            info = item.get("video_info") or {}
            for v in info.get("variants") or []:
                u = v.get("url")
                if not u or v.get("content_type") != "video/mp4" or u in seen:
                    continue
                seen.add(u)
                br = v.get("bitrate") or 0
                fmts.append(
                    Format(
                        id=f"x{len(fmts)}",
                        label=f"{br // 1000}kbps" if br else "原畫",
                        url=u,
                        quality_score=int(br) // 1000 or 50,
                        mode="proxy",
                    )
                )

        # 圖片（無影片的貼文）
        if not fmts:
            for item in media:
                u = item.get("media_url_https")
                if u and u not in seen:
                    seen.add(u)
                    fmts.append(
                        Format(id=f"img{len(fmts)}", label=f"圖 {len(fmts) + 1}",
                               url=u, ext="jpg", mode="proxy",
                               quality_score=40 - len(fmts))
                    )

        if not fmts:
            # 沒有原生影片（可能是 amplify／card 影片或純圖片）→ 讓後面的 yt-dlp 路線試
            return None

        fmts.sort(key=lambda f: -(f.quality_score or 0))
        user = data.get("user") or {}
        return VideoInfo(
            platform=self.name,
            title=re.sub(r"\s+", " ", data.get("text") or "")[:120],
            cover=cover,
            source_url=url,
            formats=fmts,
            author=user.get("name"),
            extra={"route": "syndication", "screen_name": user.get("screen_name")},
        )

    async def _tweet_id(self, url: str) -> str | None:
        m = _STATUS.search(url)
        if m:
            return m.group(1)
        if re.search(r"t\.co/", url):
            try:
                async with HttpClient(ua=_UA, timeout=15) as http:
                    resp = await http.get(url)
                    m = _STATUS.search(str(resp.url))
                    return m.group(1) if m else None
            except Exception:  # noqa: BLE001
                return None
        return None
