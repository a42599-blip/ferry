"""廣告（預留；小羅 2026-09-29）。

小羅的規則：
  · 訪客（未登入）  → 每用 **3** 次，看一次廣告
  · 免費會員        → 每用 **5** 次，看一次廣告
  · 月會員／永久會員 → **不看廣告**
  · 月會員到期未續  → 打回免費會員 → 恢復「每 5 次看一次廣告」（由 members.tier_of 判定）

⚠️ 開關有兩個（小羅 2026-09-29：可單獨決定哪種身分要看廣告），預設都是 **False**
   → 前台不會出現任何廣告。後台「功能開關」有中文名稱：
     · 廣告 ── 訪客（每 3 次看一次）      feature.ads_guest
     · 廣告 ── 免費會員（每 5 次看一次）  feature.ads_member
   月會員／永久會員**沒有開關**（永遠不看廣告）。
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


#: 各身分對應的開關（後台可單獨開關；月／永久會員沒有開關＝永遠不看）
_FLAG_OF_TIER = {
    "guest": "feature.ads_guest",
    "free": "feature.ads_member",
}


def state(tier: str, used: int) -> dict:
    """回傳給前台的廣告狀態。due=False 時前台什麼都不做。"""
    flag = _FLAG_OF_TIER.get(tier)
    enabled = bool(flag) and flags.feature_enabled(flag)
    every = every_of(tier)
    due = bool(enabled and every and used > 0 and used % every == 0)
    return {"enabled": enabled, "due": due, "every": every, "used": int(used or 0)}
