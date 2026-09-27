"""平台註冊表（新增平台只改這裡一行）。

（規格書第 21-2 章／第 22-2 章）

⚠️ 順序＝detect 的優先順序，較特殊的網域要排前面。
"""
from __future__ import annotations

from ..platforms.base import Resolver

# ── 註冊清單：新增平台＝import 一行 ＋ 加進 REGISTRY ─────────
from ..platforms.douyin import DouyinResolver
from ..platforms.tiktok import TiktokResolver
from ..platforms.bilibili import BilibiliResolver
from ..platforms.xiaohongshu import XiaohongshuResolver
from ..platforms.instagram import InstagramResolver
from ..platforms.facebook import FacebookResolver
from ..platforms.xigua import XiguaResolver
from ..platforms.toutiao import ToutiaoResolver
from ..platforms.shopee import ShopeeResolver
from ..platforms.weibo import WeiboResolver
from ..platforms.twitter_x import TwitterXResolver
from ..platforms.youtube import YoutubeResolver
from ..platforms.threads import ThreadsResolver

REGISTRY: dict[str, Resolver] = {}


def register(resolver: Resolver) -> None:
    REGISTRY[resolver.name] = resolver


for _cls in (
    # 中國大陸
    XiguaResolver,       # ← 必須在 Douyin 前面（iesdouyin.com/xg/ 是西瓜的分享路徑）
    DouyinResolver,
    BilibiliResolver,
    XiaohongshuResolver,
    WeiboResolver,
    ToutiaoResolver,
    # 海外
    TiktokResolver,
    InstagramResolver,
    FacebookResolver,
    TwitterXResolver,
    YoutubeResolver,
    ThreadsResolver,
    ShopeeResolver,
):
    register(_cls())


def all_platforms() -> list[Resolver]:
    return list(REGISTRY.values())


def platform_names() -> list[str]:
    return list(REGISTRY.keys())


def get(name: str) -> Resolver | None:
    return REGISTRY.get(name)


async def detect(url: str) -> Resolver | None:
    """找出這個網址屬於哪個平台（依註冊順序）。"""
    for resolver in REGISTRY.values():
        try:
            if await resolver.match(url):
                return resolver
        except Exception:  # 單一平台 match 失敗不影響其他平台
            continue
    return None
