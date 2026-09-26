"""付費／訂閱（🔧 預留接口 — 現在不實作，只定型）。

小羅 2026-09-26：付費「後面才要做」，但**接口先預留好**：
- 付費後 → 開通（不限次數）
- 未付費 → 受限（免費 5 次/日）
- 試用／免費次數每天歸零
- P6 才接金流（綠界／藍新／Stripe）

設計原則：全站只問 `is_unlimited(subject)` 決定要不要限制，
之後接上金流時，其他模組都不用改。
"""
from __future__ import annotations

from . import flags

# 方案代號（與規格書第 11 章一致）
PLAN_FREE = "free"
PLAN_MONTHLY = "monthly"      # US$2.99/月
PLAN_LIFETIME = "lifetime"    # US$59 早鳥

PLANS: dict[str, dict] = {
    PLAN_FREE: {"name": "免費", "unlimited": False},
    PLAN_MONTHLY: {"name": "月會員", "unlimited": True},
    PLAN_LIFETIME: {"name": "終身會員", "unlimited": True},
}


def enabled() -> bool:
    return flags.feature_enabled("feature.billing")


def plan_of(_subject: str) -> str:
    """🔧 TODO(P6)：查資料庫取得該使用者的方案。現在一律 free。"""
    return PLAN_FREE


def is_unlimited(subject: str) -> bool:
    """付費會員＝不限次數。未接金流前一律 False。"""
    if not enabled():
        return False
    return PLANS.get(plan_of(subject), PLANS[PLAN_FREE])["unlimited"]


def create_checkout(*_args, **_kwargs) -> dict:
    """🔧 TODO(P6)：建立付款（綠界／藍新／Stripe）。"""
    raise NotImplementedError("金流尚未實作（P6 階段）")


def handle_webhook(*_args, **_kwargs) -> dict:
    """🔧 TODO(P6)：接收金流商的付款通知。"""
    raise NotImplementedError("金流尚未實作（P6 階段）")


def list_orders(*_args, **_kwargs) -> list:
    """🔧 TODO(P6)：訂單列表（後台收益頁用）。"""
    return []
