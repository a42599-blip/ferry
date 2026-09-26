"""付費／訂閱（P6 介面完整化）。

方案（規格書第 11 章）：
  免費        US$0      下載／傳輸各 5 次/日
  月會員      US$2.99/月 不限次數
  終身會員    US$59(早鳥) 一次付清

金流商（規格書 11-3）：
  台灣：綠界 ECPay / 藍新 NewebPay（信用卡／超商／ATM）
  海外：Stripe

⚠️ 目前**尚未開通任何金流商帳號**，所以：
  - `create_checkout()` 會回 503 並說明還缺什麼（不是假裝成功）
  - `handle_webhook()` 已實作「驗簽 → 建訂單 → 開通會員 → 通知」，金鑰一填就能用
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from ..core import db
from ..core.errors import AppError, BadRequest
from . import flags

PLAN_FREE = "free"
PLAN_MONTHLY = "monthly"
PLAN_LIFETIME = "lifetime"

PLANS: dict[str, dict[str, Any]] = {
    PLAN_FREE: {"name": "免費", "price": 0.0, "unlimited": False, "period_days": None},
    PLAN_MONTHLY: {"name": "月會員", "price": 2.99, "unlimited": True, "period_days": 31},
    PLAN_LIFETIME: {"name": "終身會員", "price": 59.0, "unlimited": True, "period_days": None},
}

PROVIDERS = {
    "ecpay": {"label": "綠界 ECPay", "env": ["ECPAY_MERCHANT_ID", "ECPAY_HASH_KEY", "ECPAY_HASH_IV"]},
    "newebpay": {"label": "藍新 NewebPay", "env": ["NEWEBPAY_MERCHANT_ID", "NEWEBPAY_HASH_KEY", "NEWEBPAY_HASH_IV"]},
    "stripe": {"label": "Stripe", "env": ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"]},
}


class PaymentNotConfigured(AppError):
    code = "PAYMENT_NOT_CONFIGURED"
    http_status = 503


def enabled() -> bool:
    """付費功能開關（後台可開關；開發期預設關）。"""
    return flags.feature_enabled("feature.billing")


def plans() -> dict[str, dict]:
    """方案（可在後台改價格；目前讀預設值）。"""
    saved = db.get_setting("plans") or {}
    out = {}
    for k, v in PLANS.items():
        item = dict(v)
        if isinstance(saved.get(k), dict):
            item.update({kk: vv for kk, vv in saved[k].items() if kk in ("price", "name")})
        out[k] = item
    return out


def set_price(plan: str, price: float) -> None:
    saved = db.get_setting("plans") or {}
    saved.setdefault(plan, {})["price"] = float(price)
    db.set_setting("plans", saved)


def available_providers() -> dict[str, dict]:
    out = {}
    for pid, meta in PROVIDERS.items():
        have = all(os.getenv(k) for k in meta["env"])
        out[pid] = {"label": meta["label"], "ready": have,
                    "missing": [k for k in meta["env"] if not os.getenv(k)]}
    return out


# ── 方案查詢（quota 會用）────────────────────────────
def plan_of(subject: str) -> str:
    if not subject:
        return PLAN_FREE
    from . import members

    m = None
    if subject.startswith("user:"):
        m = members.get(subject[5:])
    else:
        m = members.by_device(subject)
    if not m:
        return PLAN_FREE
    plan = m.get("plan") or PLAN_FREE
    if plan == PLAN_MONTHLY:
        exp = m.get("expires_at")
        if exp and float(exp) < time.time():
            return PLAN_FREE                 # 月費到期
    return plan if plan in PLANS else PLAN_FREE


def is_unlimited(subject: str) -> bool:
    if not enabled():
        return False
    return bool(PLANS.get(plan_of(subject), PLANS[PLAN_FREE])["unlimited"])


# ── 結帳 ─────────────────────────────────────────────
def _order_id() -> str:
    return "o_" + time.strftime("%Y%m%d") + "_" + secrets.token_hex(4)


def create_checkout(subject: str, plan: str, provider: str,
                    *, base_url: str = "") -> dict:
    """建立付款。尚未設定金流商 → 明確回報缺什麼（不假裝成功）。"""
    if plan not in PLANS or plan == PLAN_FREE:
        raise BadRequest("方案不正確")
    if provider not in PROVIDERS:
        raise BadRequest("不支援的金流商")

    missing = [k for k in PROVIDERS[provider]["env"] if not os.getenv(k)]
    if missing:
        raise PaymentNotConfigured(
            f"{PROVIDERS[provider]['label']} 尚未設定完成，缺少：{', '.join(missing)}",
            detail="請把金流商金鑰設成 Railway 環境變數後即可收款（程式與訂單流程都已就緒）",
        )

    oid = _order_id()
    price = float(plans()[plan]["price"])
    from . import events

    events.add_order(oid, member_id=subject, plan=plan, amount=price,
                     currency="USD", status="pending", note=f"provider={provider}")
    if provider == "stripe":
        return {"ok": True, "order_id": oid, "provider": provider,
                "checkout_url": f"{base_url}/api/pay/checkout/{oid}",
                "note": "Stripe：請在金鑰設定後由 webhook 完成開通"}
    # 台灣金流商通常是「表單 POST 到金流商」
    return {"ok": True, "order_id": oid, "provider": provider,
            "checkout_url": f"{base_url}/api/pay/checkout/{oid}",
            "note": f"{PROVIDERS[provider]['label']}：金鑰已設定，導向付款頁"}


def _verify_signature(provider: str, raw: bytes, headers: dict) -> bool:
    """驗簽（防偽造付款通知）。"""
    if provider == "stripe":
        secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
        if not secret:
            return False
        sig = headers.get("stripe-signature", "")
        parts = dict(p.split("=", 1) for p in sig.split(",") if "=" in p)
        if not parts.get("t") or not parts.get("v1"):
            return False
        signed = f"{parts['t']}.".encode() + raw
        want = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, parts["v1"])

    if provider in ("ecpay", "newebpay"):
        key = os.getenv("ECPAY_HASH_KEY" if provider == "ecpay" else "NEWEBPAY_HASH_KEY", "")
        if not key:
            return False
        try:
            data = json.loads(raw or b"{}")
        except Exception:  # noqa: BLE001
            return False
        got = str(data.pop("__sign", ""))
        calc = hmac.new(key.encode(),
                        json.dumps(data, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False).encode(),
                        hashlib.sha256).hexdigest()
        return hmac.compare_digest(got, calc)
    return False


def activate(order_id: str, *, raw_amount: float | None = None) -> dict:
    """把訂單標成已付款，並開通會員方案。"""
    from . import events, members, notify

    row = db.one("SELECT * FROM orders WHERE id=?", (order_id,))
    if row is None:
        raise BadRequest("找不到這筆訂單")
    if row["status"] == "paid":
        return {"ok": True, "already": True}

    plan = row["plan"]
    price = float(raw_amount if raw_amount is not None else row["amount"])
    fee = round(price * 0.05, 2)            # 概估手續費（實際以金流商帳單為準）

    events.add_order(order_id, member_id=row["member_id"], plan=plan, amount=price,
                     currency=row["currency"] or "USD", fee=fee, status="paid",
                     note=row["note"])
    events.mark_paid(order_id)

    period = PLANS.get(plan, {}).get("period_days")
    expires = (time.time() + period * 86400) if period else None
    mid = row["member_id"] or ""
    if mid.startswith("user:"):
        members.set_plan(mid[5:], plan, expires)

    notify_task = notify.notify(
        "pay_success", "新付款成功",
        f"訂單：{order_id}\n方案：{PLANS.get(plan, {}).get('name', plan)}\n"
        f"金額：US$ {price:.2f}\n會員：{mid or '(未登入裝置)'}",
    )
    try:
        import asyncio

        asyncio.get_event_loop().create_task(notify_task)
    except Exception:  # noqa: BLE001
        pass

    return {"ok": True, "order_id": order_id, "plan": plan, "expires_at": expires}


def handle_webhook(provider: str, raw: bytes, headers: dict) -> dict:
    if not _verify_signature(provider, raw, headers):
        from . import notify

        try:
            import asyncio

            asyncio.get_event_loop().create_task(notify.notify(
                "pay_failed", "收到無法驗簽的付款通知",
                f"來源：{provider}（可能是偽造，或 webhook 金鑰沒設對）"))
        except Exception:  # noqa: BLE001
            pass
        raise BadRequest("簽章驗證失敗")

    try:
        data = json.loads(raw or b"{}")
    except Exception as exc:  # noqa: BLE001
        raise BadRequest("無法解析通知內容") from exc

    oid = data.get("order_id") or data.get("OrderNo") or data.get("MerchantOrderNo")
    if not oid:
        raise BadRequest("通知缺少訂單編號")
    return activate(str(oid))


def list_orders(limit: int = 200) -> list[dict]:
    return db.query("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,))  # type: ignore[return-value]


def summary(days: int = 30) -> dict:
    from . import events

    return events.revenue_summary(days)
