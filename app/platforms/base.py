"""平台介面（唯一契約）。

每個平台模組都必須實作這個介面。上層只呼叫 resolve()，
**不准**寫 `if platform == "douyin"` 這種散落各處的判斷。
（規格書第 21-3 章）

新增平台＝新增一個檔（繼承 Resolver）＋ 在 registry 加一行。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.models import VideoInfo


class Resolver(ABC):
    """一個平台 = 一個 Resolver 子類。"""

    #: 平台識別碼（小寫），例如 "douyin"
    name: str = ""

    #: 給人看的名稱（三語由 frontend locales 處理，這裡當後備）
    label: str = ""

    #: 這個平台支援的網域（用於 match()）
    hosts: tuple[str, ...] = ()

    @abstractmethod
    async def match(self, url: str) -> bool:
        """這個網址是不是這個平台的。"""
        raise NotImplementedError

    @abstractmethod
    async def resolve(self, url: str) -> VideoInfo:
        """解析網址，回傳統一格式。失敗一律丟 PlatformError 的子類。"""
        raise NotImplementedError

    # ── 可選：平台自己的健康檢查（tools/daily_check.py 會用）──
    async def health(self, sample_url: str | None = None) -> dict:
        return {"platform": self.name, "ok": None, "note": "未實作健康檢查"}
