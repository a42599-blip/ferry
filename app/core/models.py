"""統一資料格式（唯一契約）。

所有平台模組都必須回傳這裡定義的型別，上層程式不需要知道是哪個平台。
（規格書第 12 章／第 21-3 章）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Format:
    """一種可下載的格式（畫質／音訊）。"""

    id: str                    # 唯一鍵，例如 "origin" / "1080p" / "audio"
    label: str                 # 給人看的標籤，例如 "原畫" / "1080P"
    url: str                   # 直接可下載的網址（平台 CDN）
    height: Optional[int] = None
    width: Optional[int] = None
    size: Optional[int] = None       # bytes，未知為 None
    ext: str = "mp4"
    audio: bool = False              # True＝純音訊
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    quality_score: int = 0           # 排序用，越高越好
    # 下載模式（見規格書第 8 章）
    # direct  = 前端 <a href> 直連（CDN 擋 Origin）
    # fetch   = 前端 fetch→blob（CDN 開 CORS，可自訂檔名）
    # stream  = 前端 Service Worker 串流（大檔）
    # proxy   = 必須經伺服器重新抓取（YouTube，yt-dlp 重新下載）
    # relay   = 必須經伺服器「原樣轉發」（CDN 檢查 Referer；DASH 要合併影音）
    mode: str = "fetch"
    headers: dict[str, str] = field(default_factory=dict)  # 下載時需要的標頭
    #: DASH 的「純聲音軌」網址。有值＝必須合併，否則使用者會下載到無聲影片。
    audio_url: Optional[str] = None
    #: 轉發用的短鍵（伺服器登記制；前端只拿得到鍵，不當開放代理）
    relay_key: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "url": self.url,
            "height": self.height,
            "width": self.width,
            "size": self.size,
            "ext": self.ext,
            "audio": self.audio,
            "vcodec": self.vcodec,
            "acodec": self.acodec,
            "quality_score": self.quality_score,
            "mode": self.mode,
            "headers": self.headers,
            "audio_url": self.audio_url,
            "relay_key": self.relay_key,
        }


@dataclass
class VideoInfo:
    """一次解析的結果。"""

    platform: str                    # "douyin" / "bilibili" ...
    title: str
    cover: str                       # 封面圖網址（歷史記錄只存這個，零儲存）
    source_url: str                  # 使用者貼的原始連結
    formats: list[Format] = field(default_factory=list)
    duration: Optional[int] = None   # 秒
    author: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "title": self.title,
            "cover": self.cover,
            "source_url": self.source_url,
            "duration": self.duration,
            "author": self.author,
            "formats": [f.to_dict() for f in self.formats],
            "extra": self.extra,
        }
