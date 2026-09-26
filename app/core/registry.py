"""平台註冊表（新增平台只改這裡一行）。

（規格書第 21-2 章／第 22-2 章）
"""
from __future__ import annotations

from ..platforms.base import Resolver

# ── 註冊清單：新增平台＝import 一行 ＋ 加進 REGISTRY ─────────
from ..platforms.douyin import DouyinResolver
from ..platforms.tiktok import TiktokResolver
from ..platforms.bilibili import BilibiliResolver

REGISTRY: dict[str, Resolver] = {}


def register(resolver: Resolver) -> None:
    REGISTRY[resolver.name] = resolver


for _cls in (
    DouyinResolver,
    TiktokResolver,
    BilibiliResolver,
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
