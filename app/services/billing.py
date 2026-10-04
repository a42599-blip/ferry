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

import httpx

from ..core import db
from ..core.errors import AppError, BadRequest, LoginRequired
from . import flags

PLAN_FREE = "free"
PLAN_MONTHLY = "monthly"
PLAN_LIFETIME = "lifetime"

PLANS: dict[str, dict[str, Any]] = {
    PLAN_FREE: {"name": "免費", "price": 0.0, "unlimited": False, "period_days": None},
    # 2026-09-30 小羅定案：月會員 NT$88、終身會員 NT$988（台幣，配合台灣金流商）
    PLAN_MONTHLY: {"name": "月會員", "price": 88.0, "unlimited": True, "period_days": 31},
    PLAN_LIFETIME: {"name": "終身會員", "price": 988.0, "unlimited": True, "period_days": None},
}

#: 付款方式（渠道）中文化：金流商回傳的值 → 顯示名稱
#  藍新的 PaymentType／綠界的 PaymentType 都會走這張表（大小寫、有無底線都吃）
PAY_METHODS = {
    "credit": "信用卡", "credit_nod3d": "信用卡（無 3D）", "信用卡": "信用卡",
    "unionpay": "銀聯卡", "credit_unionpay": "銀聯卡",
    "applepay": "Apple Pay", "googlepay": "Google Pay", "samsungpay": "Samsung Pay",
    "linepay": "LINE Pay", "esunwallet": "玉山 Wallet", "taiwanpay": "台灣 Pay",
    "twqr": "TWQR（台灣Pay）", "jkopay": "街口支付", "pi": "Pi 拍錢包",
    "webatm": "網路 ATM", "atm": "ATM 轉帳",
    "cvs": "超商代碼", "barcode": "超商條碼",
    "alipay": "支付寶", "wechat": "微信支付", "weixin": "微信支付",
    "bnpl": "無卡分期", "afreecatv": "AfreecaTV",
    "payoneer": "Payoneer 信用卡", "card": "信用卡",
}
#: 金流商通知裡可能放「付款方式」的欄位名（依重要性排序）
_METHOD_KEYS = ("PaymentType", "payment_type", "pay_method", "PaymentMethod",
                "paymentType", "ChoosePayment", "PayType", "支付方式", "payment_method")


def _norm_method(raw: str) -> str:
    """把金流商回傳的付款方式正規化（回中文顯示名稱；認不得就保留原文）。"""
    v = str(raw or "").strip()
    if not v:
        return ""
    key = v.lower().replace("-", "_").replace(" ", "")
    for prefix in ("credit_", "credit"):          # CREDIT_NOD3D / CREDIT_3D → 信用卡
        if key.startswith(prefix) and key not in PAY_METHODS:
            key = "credit"
            break
    return PAY_METHODS.get(key, v[:40])


PROVIDERS = {
    "newebpay": {"label": "藍新 NewebPay", "env": ["NEWEBPAY_MERCHANT_ID", "NEWEBPAY_HASH_KEY", "NEWEBPAY_HASH_IV"]},
    "stripe": {"label": "Stripe", "env": ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"]},
    # 小羅 2026-10-03：PayPal（站內彈窗；卡在「付款成功才開通」的同一套流程）
    "paypal": {"label": "PayPal", "env": ["PAYPAL_CLIENT_ID", "PAYPAL_SECRET"]},
}

#: 各付款平台對應的「開關」（小羅 2026-10-03：可只開某一家）
PROVIDER_FLAGS = {
    "paypal": "feature.pay_paypal",
    "stripe": "feature.pay_stripe",
    "newebpay": "feature.pay_newebpay",
}

#: 各平台「支援的付款渠道」（前台圖標聯動用；多平台取聯集並去重）
#   2026-10-03 查證：
#   · PayPal：PayPal 餘額、信用卡（Visa/MC/JCB，部分地區含銀聯）
#   · Stripe（台灣）：Visa / Mastercard / JCB / AmEx / 中國銀聯
#   · 藍新：信用卡（Visa/MC/JCB）・銀聯卡・ATM・超商代碼/條碼・Apple Pay / Google Pay / Samsung Pay / 台灣Pay
PROVIDER_CHANNELS = {
    "paypal": ["paypal"],
    "stripe": ["visa", "mastercard", "jcb", "amex", "unionpay"],
    "newebpay": ["visa", "mastercard", "jcb", "unionpay", "cvs", "atm",
                 "applepay", "googlepay", "taiwanpay"],
}

#: PayPal REST 端點（沙箱測試可設 PAYPAL_API_BASE=https://api-m.sandbox.paypal.com）
def _paypal_base() -> str:
    return os.getenv("PAYPAL_API_BASE", "https://api-m.paypal.com").rstrip("/")


def paypal_client_id() -> str:
    """前端 JS SDK 用的 client id（client id 是公開值，可以給前端）。"""
    return os.getenv("PAYPAL_CLIENT_ID", "")


def _paypal_token() -> str:
    cid, sec = os.getenv("PAYPAL_CLIENT_ID", ""), os.getenv("PAYPAL_SECRET", "")
    if not cid or not sec:
        raise PaymentNotConfigured("PayPal 尚未設定完成，缺少：PAYPAL_CLIENT_ID／PAYPAL_SECRET")
    r = httpx.post(_paypal_base() + "/v1/oauth2/token", auth=(cid, sec),
                   data={"grant_type": "client_credentials"}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def paypal_create_order(order_id: str, amount: float, *, return_url: str = "",
                        cancel_url: str = "") -> dict:
    """建立 PayPal 訂單（回傳 PayPal order 物件；前端用 links 的 approve 開付款頁）。"""
    token = _paypal_token()
    app_ctx: dict[str, str] = {"user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING"}
    if return_url:
        app_ctx["return_url"] = return_url
    if cancel_url:
        app_ctx["cancel_url"] = cancel_url
    r = httpx.post(
        _paypal_base() + "/v2/checkout/orders",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        json={
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": order_id,
                "amount": {"currency_code": "TWD", "value": f"{float(amount):.2f}"},
            }],
            "application_context": app_ctx,
        }, timeout=25)
    if r.status_code >= 400:
        raise BadRequest("PayPal 建立訂單失敗：" + r.text[:200])
    return r.json()


def _paypal_approve_url(po: dict) -> str:
    for lk in (po.get("links") or []):
        if lk.get("rel") in ("approve", "payer-action"):
            return lk.get("href", "")
    return ""


def paypal_capture(paypal_order_id: str) -> dict:
    """擄取（capture）PayPal 訂單；只有狀態 COMPLETED 才算付款成功。"""
    token = _paypal_token()
    r = httpx.post(
        _paypal_base() + f"/v2/checkout/orders/{paypal_order_id}/capture",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        timeout=25)
    if r.status_code >= 400:
        raise BadRequest("PayPal 付款失敗：" + r.text[:200])
    return r.json()


def capture_paypal(order_id: str, paypal_order_id: str) -> dict:
    """付款完成後呼叫：**向 PayPal 確認真的收到錢，才開通**。

    ⚠️ 小羅 2026-10-03：付款失敗／未完成 → 不開通。
    order_id 可留空（由 PayPal 回傳的 reference_id 反查我們的訂單）。
    """
    if not paypal_order_id:
        raise BadRequest("缺少 PayPal 訂單資訊")
    cap = paypal_capture(paypal_order_id)
    if (cap.get("status") or "").upper() != "COMPLETED":
        st = cap.get("status") or "unknown"
        pu0 = (cap.get("purchase_units") or [{}])[0]
        oid0 = order_id or (pu0.get("reference_id") or "")
        if oid0:
            from . import events as _ev
            _ev.mark_failed(oid0, "PayPal 未完成（%s）" % st)
        raise BadRequest("PayPal 付款未完成（狀態：%s）" % st)
    pu = (cap.get("purchase_units") or [{}])[0]
    if not order_id:
        order_id = pu.get("reference_id") or ""
    if not order_id:
        raise BadRequest("PayPal 回傳資料缺少訂單編號")
    row = db.one("SELECT * FROM orders WHERE id=?", (order_id,))
    if row is None:
        raise BadRequest("找不到這筆訂單")
    if row["status"] == "paid":
        return {"ok": True, "already": True, "order_id": order_id}
    amt: float | None = None
    txn = ""
    try:
        c0 = ((pu.get("payments") or {}).get("captures") or [{}])[0]
        amt = float(((c0.get("amount") or {}).get("value")) or 0) or None
        txn = c0.get("id", "") or ""
    except Exception:  # noqa: BLE001
        pass
    res = activate(order_id, raw_amount=amt, txn=txn, method="信用卡")
    res["provider"] = "paypal"
    return res


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
    """可用金流商（含「開關」與「支援的付款渠道」給前台用）。

    小羅 2026-10-03：三個平台可**分開開關**；關掉就不出現在前台、圖標也跟著消失。
    """
    out = {}
    for pid, meta in PROVIDERS.items():
        flag = PROVIDER_FLAGS.get(pid)
        on = flags.feature_enabled(flag) if flag else True
        have = all(os.getenv(k) for k in meta["env"])
        out[pid] = {"label": meta["label"], "ready": bool(have and on),
                    "enabled": on, "missing": [k for k in meta["env"] if not os.getenv(k)],
                    "channels": PROVIDER_CHANNELS.get(pid, [])}
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
    if (m.get("status") or "active") != "active":
        return PLAN_FREE            # 停權／註銷 → 一律當免費身分（真的停掉）
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
    """訂單編號（小羅 2026-09-30：要能念給客服／跟金流商對帳）

    格式：FY + 年月日 + 4 碼英數，例如 FY260930A1B2
    """
    return "FY%s%s" % (time.strftime("%y%m%d"), secrets.token_hex(2).upper())


def create_checkout(subject: str, plan: str, provider: str,
                    *, base_url: str = "") -> dict:
    """建立付款。尚未設定金流商 → 明確回報缺什麼（不假裝成功）。

    ⚠️ 小羅 2026-09-30：**必須是已登入會員**才能下單。
       未登入＝裝置身分（dev:xxx），之後換人／換帳號就可能「A 買卻開給 B」，
       所以這裡直接擋掉，要求先登入再付款。
    """
    if not str(subject or "").startswith("user:"):
        raise LoginRequired("請先登入會員再付款（付款後會開通到你的帳號）")
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

    # 訂單成立時就把「購買人」記下來（之後開通只看這筆訂單，不信任通知帶來的帳號）
    buyer = db.one("SELECT id, email, status FROM members WHERE id=?", (subject[5:],))
    if not buyer or (buyer["status"] or "active") == "deleted":
        raise LoginRequired("找不到這個會員帳號，請重新登入後再付款")

    oid = _order_id()
    price = float(plans()[plan]["price"])
    from . import events

    events.add_order(oid, member_id=subject, plan=plan, amount=price,
                     currency="TWD", status="pending", note=f"provider={provider}",
                     provider=provider)
    if provider == "paypal":
        po = paypal_create_order(oid, price,
                                 return_url=f"{base_url}/api/pay/paypal/return",
                                 cancel_url=f"{base_url}/?pay=cancel")
        return {"ok": True, "order_id": oid, "provider": "paypal",
                "paypal_order_id": po.get("id", ""),
                "approve_url": _paypal_approve_url(po),
                "checkout_url": f"{base_url}/api/pay/checkout/{oid}",
                "note": "PayPal：開啟付款頁；付款成功才開通"}
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

    if provider == "newebpay":
        key = os.getenv("NEWEBPAY_HASH_KEY", "")
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


def activate(order_id: str, *, raw_amount: float | None = None, txn: str = "",
             method: str = "") -> dict:
    """把訂單標成已付款，並開通會員方案。

    ⚠️ 金額一律以「訂單上的金額」為準（防止通知帶來的金額被改），
       若金流商回報的金額跟訂單不同 → 記在備註並通知（核實用）。
    """
    from . import events, members, notify

    row = db.one("SELECT * FROM orders WHERE id=?", (order_id,))
    if row is None:
        raise BadRequest("找不到這筆訂單")
    if row["status"] == "paid":
        return {"ok": True, "already": True}

    # ⚠️ 小羅 2026-09-30：開通前再核實一次「這筆訂單要開給誰」
    #    只認訂單上記錄的 member_id（不信任金流商通知帶來的任何帳號），
    #    而且該帳號必須存在、不是已註銷 → 才開通（避免開錯帳號／開到空帳號）。
    buyer = (row["member_id"] or "")
    if not buyer.startswith("user:"):
        raise BadRequest("這筆訂單沒有綁定會員帳號，為避免開錯人，請人工確認")
    bm = db.one("SELECT id, email, status FROM members WHERE id=?", (buyer[5:],))
    if not bm:
        from . import notify as _n

        try:
            import asyncio

            asyncio.get_event_loop().create_task(_n.notify(
                "pay_no_member", "付款成功但找不到會員帳號（未開通）",
                "訂單 %s：會員 %s 不存在，已付款但未自動開通，請人工處理"
                % (order_id, buyer), force=True))
        except Exception:  # noqa: BLE001
            pass
        raise BadRequest("找不到訂單上的會員帳號（未開通，已通知管理員）")
    if (bm["status"] or "active") == "deleted":
        raise BadRequest("這筆訂單的帳號已註銷（未開通，已通知管理員）")

    plan = row["plan"]
    expected = float(row["amount"])         # ← 金額在下單時就鎖定（月 88／終身 988）
    paid = float(raw_amount) if raw_amount is not None else expected
    mismatch = raw_amount is not None and abs(paid - expected) > 0.5
    price = expected
    fee = round(price * 0.05, 2)            # 概估手續費（實際以金流商帳單為準）
    note = (row["note"] or "")

    # ⚠️ 小羅 2026-10-04：金額在下單時就鎖定；金流商回報的金額若跟訂單不符
    #   → **一律不開通**（避免「選 88 卻只付 50 也開通」）→ 記錄並通知管理員人工核實。
    if mismatch:
        try:
            import asyncio

            asyncio.get_event_loop().create_task(notify.notify(
                "pay_amount_mismatch", "付款金額與訂單不符（未開通）",
                "訂單 %s：訂單金額 %s，金流商通知 %s（已擋下、未開通，請人工核實）"
                % (order_id, expected, paid), force=True))
        except Exception:  # noqa: BLE001
            pass
        raise BadRequest("付款金額與訂單不符，未開通（已通知管理員）")

    events.add_order(order_id, member_id=row["member_id"], plan=plan, amount=price,
                     currency=row["currency"] or "TWD", fee=fee, status="paid",
                     note=note, provider=(row["provider"] or ""))
    events.mark_paid(order_id)
    events.set_order_txn(order_id, txn)
    if method:
        events.set_order_method(order_id, method)

    # ── 到期日計算（小羅 2026-09-27 定案的規則）────────────────
    #  ① 月會員一次算 31 天
    #  ② **續約要累加**：如果他還剩 3 天又買一次月會員 → 3 + 31 = 34 天
    #     剩 10 天又買 → 10 + 31 = 41 天
    #  ③ **終身會員不累加**（永久，expires_at 永遠是 None）
    period = PLANS.get(plan, {}).get("period_days")
    mid = row["member_id"] or ""
    expires = None
    if period:
        base = time.time()
        if mid.startswith("user:"):
            cur = members.get(mid[5:]) or {}
            cur_exp = cur.get("expires_at")
            # 還是同一個月會員身分、且還沒到期 → 從原到期日繼續加（累加）
            if (cur.get("plan") == plan and cur_exp
                    and float(cur_exp) > base):
                base = float(cur_exp)
        expires = base + period * 86400
    if mid.startswith("user:"):
        members.set_plan(mid[5:], plan, expires, amount=price,
                         reason="renew" if expires else "first_pay",
                         note="付款成功（續約累加）" if expires else "付款成功（終身）")

    plan_name = PLANS.get(plan, {}).get("name", plan)
    currency = row["currency"] or "TWD"
    money = ("NT$ %.0f" % price) if currency in ("TWD", "NTD") else ("US$ %.2f" % price)

    notify_task = notify.notify(
        "pay_success", "新付款成功",
        f"訂單：{order_id}\n方案：{plan_name}\n"
        f"金額：{money}\n會員：{mid or '(未登入裝置)'}",
        force=True,
    )

    # ── 給「客人」的付款確認信（小羅 2026-10-04）────────────────
    #   「你寄給客人這封信的內容是什麼？必須要有訂單編號，讓他能跟我們對帳；
    #     而且後台要用這個訂單號就能搜尋這筆交易。」
    #   → 信裡一定帶「訂單編號」，客服用編號就能在後台以 /order/lookup 查到。
    if expires:
        expiry = time.strftime("%Y-%m-%d %H:%M", time.localtime(expires)) + "（月會員 31 天）"
    else:
        expiry = "永久（終身會員）"
    cust_body = (
        "您好，\n\n"
        "我們已收到您的付款，會員方案已自動開通 🎉\n\n"
        f"訂單編號：{order_id}\n"
        f"方案：{plan_name}\n"
        f"金額：{money}\n"
        f"到期日：{expiry}\n"
        + (f"付款方式：{method}\n" if method else "")
        + (f"金流商交易序號：{txn}\n" if txn else "")
        + "\n請保留「訂單編號」，若有付款或開通問題，"
        "來信客服並附上訂單編號，我們會盡快為你處理。\n\n"
        "轉運站 scefo.com\n"
        "客服信箱：a42599@gmail.com\n"
    )
    try:
        import asyncio

        loop = asyncio.get_event_loop()
        loop.create_task(notify_task)
        if bm["email"]:
            loop.create_task(notify.send_to(
                bm["email"], f"【轉運站】付款成功通知（訂單 {order_id}）", cust_body))
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

    oid = (data.get("order_id") or data.get("MerchantOrderNo") or data.get("OrderNo")
           or data.get("MerchantTradeNo"))
    if not oid:
        raise BadRequest("通知缺少訂單編號")
    # 金流商交易序號（對帳要跟金流商互相核對用）＋回報金額（核實用）
    txn = (data.get("TradeNo") or data.get("trade_no") or data.get("TransactionId")
           or data.get("交易序號") or "")
    # 付款方式（信用卡／ATM／超商／Apple Pay／LINE Pay…）→ 後台可依此分開統計
    method = ""
    for k in _METHOD_KEYS:
        if data.get(k):
            method = _norm_method(data[k])
            break
    amt = None
    for k in ("amount", "Amt", "TradeAmt", "Amount", "TotalAmount", "TradeAmount"):
        if data.get(k) not in (None, ""):
            try:
                amt = float(data[k])
                break
            except (TypeError, ValueError):
                continue
    return activate(str(oid), raw_amount=amt, txn=str(txn), method=method)



def summary(days: int = 30) -> dict:
    from . import events

    return events.revenue_summary(days)


# ══════════════════════════════════════════════════════════════════
#  收款與提現（小羅 2026-09-27 要求先預留）
#
#  收款：PROVIDERS 三個金流商（綠界／藍新／Stripe）。
#        只要在 Railway 設好環境變數，前台付款與回呼（webhook）就會自動生效。
#  提現：這裡只記錄「提現申請與狀態」；真正的撥款由金流商後台操作。
#        為什麼不由程式自動撥款：牽涉金流商的驗證與 2FA，人工確認比較安全。
# ══════════════════════════════════════════════════════════════════

#: 撥款方式（這只是「記錄」；真實撥款在金流商後台操作）
PAYOUT_METHODS = {
    "bank": "銀行匯款（金流商 → 我的銀行帳戶）",
    "payoneer": "Payoneer 提現",
    "other": "其他",
}

#: 金流商抽成（僅供試算；實際以各家帳單為準）
FEE_RATE = {"newebpay": 0.028, "stripe": 0.034, "payoneer": 0.03,
            "ezpay": 0.03, "": 0.03}


def _since(days: int) -> float:
    return time.time() - days * 86400 if days else 0.0


# ══════════════════════════════════════════════════════════════════
#  撥款紀錄（小羅 2026-09-30）
#  ⚠️ 真實撥款在「金流商後台」按（藍新 → 申請撥款 → 匯到台新），
#     這裡只登記「哪個平台、哪天、撥了多少、匯到哪個帳戶」，用來對帳。
# ══════════════════════════════════════════════════════════════════
def payout_add(*, provider: str = "", amount: float = 0, fee: float = 0,
               method: str = "bank", account: str = "", currency: str = "TWD",
               fx_rate: float | None = None, amount_twd: float | None = None,
               note: str = "", status: str = "done") -> dict:
    amount = round(float(amount or 0), 2)
    if amount <= 0:
        raise BadRequest("撥款金額要大於 0")
    twd = amount_twd
    if twd is None:
        twd = round(amount * (fx_rate or 1.0), 2) if (currency or "TWD") != "TWD" else amount
    db.execute(
        "INSERT INTO payouts (ts, amount, fee, currency, method, note, status, done_at,"
        " provider, account, fx_rate, amount_twd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (time.time(), amount, round(float(fee or 0), 2), currency or "TWD",
         method or "bank", (note or "")[:300], status,
         time.time() if status == "done" else None, provider or None, account or None,
         fx_rate, twd))
    row = db.one("SELECT * FROM payouts ORDER BY id DESC LIMIT 1")
    return dict(row) if row else {}


def payouts(limit: int = 200) -> list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM payouts ORDER BY ts DESC LIMIT ?", (limit,))]


def payout_set_status(pid: int, status: str) -> bool:
    if status not in ("pending", "done", "cancelled"):
        raise BadRequest("狀態不正確")
    db.execute("UPDATE payouts SET status=?, done_at=? WHERE id=?",
               (status, time.time() if status == "done" else None, pid))
    return True


def payout_summary(days: int = 0) -> dict:
    """（相容舊介面）由 revenue_overview 算出來。"""
    t = revenue_overview(days)["totals"]
    return {"gross": t["gross_all"], "paid_out": t["paid_out"], "pending": t["pending_out"],
            "fees": t["fees"], "available": t["balance"], "methods": PAYOUT_METHODS}


# ══════════════════════════════════════════════════════════════════
#  退款（小羅 2026-09-30：要看得出「已退／未退／處理中」）
#  狀態：applied 申請中 → processing 處理中 → done 已退款（或 rejected 已拒絕）
#  實際刷退由金流商處理；狀態改成 done 時，會把會員降回免費並寫方案歷史。
# ══════════════════════════════════════════════════════════════════
REFUND_STATUS = {
    "applied": "申請中（已收到，待確認）",
    "processing": "處理中（已向金流商提出）",
    "done": "已退款",
    "rejected": "已拒絕",
}


def refund_apply(*, order_id: str = "", email: str = "", amount: float | None = None,
                 reason: str = "", note: str = "") -> dict:
    o = db.one("SELECT * FROM orders WHERE id=?", (order_id,)) if order_id else None
    member_id = (o["member_id"] if o else "") or ""
    provider = (o["provider"] if o else "") or ""
    if not email and member_id.startswith("user:"):
        from . import members

        email = (members.get(member_id[5:]) or {}).get("email") or ""
    if amount is None:
        amount = float(o["amount"]) if o else 0
    amount = round(float(amount or 0), 2)
    if amount <= 0:
        raise BadRequest("退款金額要大於 0")
    db.execute(
        "INSERT INTO refunds (order_id, member_id, email, provider, amount, currency,"
        " reason, status, apply_at, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (order_id or None, member_id or None, (email or "")[:200], provider or None,
         amount, ((o["currency"] if o else "TWD") or "TWD"), (reason or "")[:300],
         "applied", time.time(), (note or "")[:300]))
    row = db.one("SELECT * FROM refunds ORDER BY id DESC LIMIT 1")
    return dict(row) if row else {}


def refunds(limit: int = 200) -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT * FROM refunds ORDER BY apply_at DESC LIMIT ?", (limit,))]


def refund_set_status(rid: int, status: str, note: str = "") -> bool:
    if status not in REFUND_STATUS:
        raise BadRequest("退款狀態不正確")
    row = db.one("SELECT * FROM refunds WHERE id=?", (rid,))
    if not row:
        raise BadRequest("找不到這筆退款")
    db.execute("UPDATE refunds SET status=?, done_at=?,"
               " note=COALESCE(?, note) WHERE id=?",
               (status, time.time() if status in ("done", "rejected") else None,
                (note or None), rid))
    if status == "done":
        oid = row["order_id"]
        if oid:
            db.execute("UPDATE orders SET refund_amount=COALESCE(refund_amount,0)+?"
                       " WHERE id=?", (float(row["amount"] or 0), oid))
        mid = row["member_id"] or ""
        if mid.startswith("user:"):
            from . import members

            mid = mid[5:]
            m = members.get(mid)
            if m and (m.get("plan") or "free") != "free":
                members.set_plan(mid, "free", None, amount=-float(row["amount"] or 0),
                                 reason="refund", note="退款 #%s" % rid)
    return True


def order_detail(order_id: str) -> dict:
    """訂單核實（小羅 2026-09-30）：訂單 ↔ 會員 ↔ 金額 ↔ 退款紀錄。

    退款前先用這個交叉比對，才不會「退到別人身上」。
    """
    from . import members

    o = db.one("SELECT * FROM orders WHERE id=?", (order_id,))
    if not o:
        raise BadRequest("找不到這筆訂單")
    d = dict(o)
    mid = d.get("member_id") or ""
    m = members.get(mid[5:]) if mid.startswith("user:") else None
    m = m or {}
    d["member"] = {
        "id": mid,
        "email": m.get("email") or "",
        "nickname": m.get("nickname") or "",
        "plan": m.get("plan") or "free",
        "expires_at": m.get("expires_at"),
    }
    d["refunds"] = [dict(r) for r in db.query(
        "SELECT * FROM refunds WHERE order_id=? ORDER BY apply_at DESC", (order_id,))]
    return d


# ══════════════════════════════════════════════════════════════════
#  收益總覽（對帳用）：分平台 ＋ 合計 ＋ 撥款 ＋ 退款
# ══════════════════════════════════════════════════════════════════
def revenue_overview(days: int = 0) -> dict:
    """收益總覽（對帳用）：**分平台** ＋ **分付款方式** ＋ 合計 ＋ 撥款 ＋ 退款。

    小羅 2026-09-30 要求：
      ① 各平台/各渠道（信用卡、行動支付、ATM、超商…）**分開統計**，最後也要有合計
      ② 待撥款＝平台帳上還沒撥給我的錢
    """
    since = _since(days)

    def dim(key_expr: str, *, period: bool) -> dict:
        where = "status IN ('paid','refunded')"
        params: tuple = ()
        if period:
            where += " AND created_at>=?"
            params = (since,)
        rows = db.query(
            "SELECT COALESCE(NULLIF(%s,''),'（未記錄）') AS k,"
            " COUNT(*) AS orders, COUNT(DISTINCT member_id) AS payers,"
            " COALESCE(SUM(amount),0) AS gross, COALESCE(SUM(fee),0) AS fees,"
            " COALESCE(SUM(COALESCE(refund_amount,0)),0) AS refunded"
            " FROM orders WHERE %s GROUP BY k" % (key_expr, where), params)
        return {str(r["k"]): dict(r) for r in rows}

    def payout_map(col: str, status: str) -> dict:
        rows = db.query(
            "SELECT COALESCE(NULLIF(%s,''),'（未記錄）') AS k,"
            " COALESCE(SUM(COALESCE(amount_twd, amount)),0) AS amt"
            " FROM payouts WHERE status=? GROUP BY k" % col, (status,))
        return {str(r["k"]): float(r["amt"] or 0) for r in rows}

    def build(per: dict, allt: dict, paid: dict, pend: dict, *, with_payout: bool) -> list[dict]:
        out = []
        for k in sorted(set(list(per) + list(allt) + list(paid) + list(pend))):
            a = allt.get(k, {})
            q = per.get(k, {})
            gross_all = round(float(a.get("gross", 0)), 2)
            refunded = round(float(a.get("refunded", 0)), 2)
            net_all = round(gross_all - refunded, 2)
            po = round(float(paid.get(k, 0)), 2) if with_payout else 0.0
            pe = round(float(pend.get(k, 0)), 2) if with_payout else 0.0
            out.append({
                "provider": k,
                "orders": int(q.get("orders", 0)),
                "payers": int(q.get("payers", 0)),
                "gross": round(float(q.get("gross", 0)), 2),
                "fees": round(float(q.get("fees", 0)), 2),
                "refunded": refunded,
                "net": round(round(float(q.get("gross", 0)), 2) - refunded, 2),
                "gross_all": gross_all,
                "net_all": net_all,
                "paid_out": po,
                "pending_out": pe,
                "balance": round(net_all - po - pe, 2),
            })
        return out

    def totals(rows: list[dict]) -> dict:
        keys = ("orders", "payers", "gross", "fees", "refunded", "net", "gross_all",
                "net_all", "paid_out", "pending_out", "balance")
        return {k: round(sum(r[k] for r in rows), 2) for k in keys}

    rows = build(dim("provider", period=True), dim("provider", period=False),
                 payout_map("provider", "done"), payout_map("provider", "pending"),
                 with_payout=True)
    rows_method = build(dim("pay_method", period=True), dim("pay_method", period=False),
                        {}, {}, with_payout=False)

    refund_by: dict[str, dict] = {}
    for r in db.query("SELECT status, COUNT(*) AS c, COALESCE(SUM(amount),0) AS amt"
                      " FROM refunds GROUP BY status"):
        refund_by[str(r["status"])] = {"count": int(r["c"]),
                                       "amount": round(float(r["amt"]), 2)}

    return {
        "days": days,
        "rows": rows,
        "rows_method": rows_method,
        "totals": totals(rows),
        "totals_method": totals(rows_method),
        "refunds": refund_by,
        "refund_status": REFUND_STATUS,
        "payout_methods": PAYOUT_METHODS,
        "pay_channels": {k: v for k, v in PAY_METHODS.items()},
        "payout_list": payouts(50),
        "refund_list": refunds(50),
    }


def provider_setup_guide() -> list[dict]:
    """每個金流商要設哪些環境變數、回呼網址是什麼（給後台顯示）。"""
    out = []
    for pid, meta in PROVIDERS.items():
        out.append({
            "id": pid, "label": meta["label"],
            "env": [{"key": k, "set": bool(os.getenv(k))} for k in meta["env"]],
            "ready": all(os.getenv(k) for k in meta["env"]),
        })
    return out
