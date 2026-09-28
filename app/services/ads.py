"""廣告（預留；小羅 2026-09-29）。

小羅的規則：
  · 訪客（未登入）  → 每用 **3** 次，看一次廣告
  · 免費會員        → 每用 **5** 次，看一次廣告
  · 月會員／永久會員 → **不看廣告**
  · 月會員到期未續  → 打回免費會員 → 恢復「每 5 次看一次廣告」（由 members.tier_of 判定）

⚠️ 開關 `feature.ads` 預設 **False** → 前台不會出現任何廣告；
   要開的時候在後台「功能開關」打開即可（不需要改程式）。
"""
from __future__ import annotations

from . import flags

#: 訪客每幾次看一次廣告
ADS_EVERY_GUEST = 3
#: 免費會員每幾次看一次廣告
ADS_EVERY_MEMBER = 5


def every_of(tier: str) -> int:
    """這個等級每幾次要看一次廣告（0＝不用看）。"""
    if tier == "guest":
        return ADS_EVERY_GUEST
    if tier == "free":
        return ADS_EVERY_MEMBER
    return 0


def state(tier: str, used: int) -> dict:
    """回傳給前台的廣告狀態。due=False 時前台什麼都不做。"""
    enabled = flags.feature_enabled("feature.ads")
    every = every_of(tier)
    due = bool(enabled and every and used > 0 and used % every == 0)
    return {"enabled": enabled, "due": due, "every": every, "used": int(used or 0)}
