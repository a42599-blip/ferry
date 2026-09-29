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
    """回傳給前台的廣告狀態。due=False 時前台什麼都不做。

    `due=True` ＝「**下一次動作之前要先看一次廣告**」（不是用完當下就跳）。
    看完廣告 → 前台打 `POST /api/ads/reward` → 依等級把次數加回來（見 api_member）。
    """
    flag = _FLAG_OF_TIER.get(tier)
    enabled = bool(flag) and flags.feature_enabled(flag)
    every = every_of(tier)
    due = bool(enabled and every and used > 0 and used % every == 0)
    return {"enabled": enabled, "due": due, "every": every, "used": int(used or 0)}


# ── 看廣告計時（伺服器端驗證：沒看滿 N 秒不給次數）────────────
#   小羅 2026-09-29：「彈出來就關掉，當然不能給他加次數，我得不到廣告費啊。」
#   用記憶體記「開始時間」（單一實例、有 TTL）→ 前端改不動、也不能作弊。
_ad_started: dict[str, float] = {}
_AD_TTL = 3600.0          # 1 小時沒動就清掉


def mark_start(subject: str) -> int:
    """記錄「這個人現在開始看廣告」，回傳要看滿幾秒。"""
    import time as _t
    from ..core import config

    now = _t.time()
    for k in [k for k, v in _ad_started.items() if now - v > _AD_TTL]:
        _ad_started.pop(k, None)
    _ad_started[subject] = now
    return int(getattr(config.settings, "ads_min_seconds", 15) or 15)


def watch_ok(subject: str, seconds: int) -> bool:
    """有沒有看滿？沒記錄（直接打 reward）＝沒看過 → 不給。"""
    import time as _t

    started = _ad_started.get(subject)
    if not started:
        return False
    return (_t.time() - started) >= max(0, int(seconds or 0))
