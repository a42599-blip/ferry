"""會員與付款 API（規格書第 6、11、20 章）。

會員：/api/member/register、/login、/me
付款：/api/pay/plans、/checkout、/webhook/{provider}
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Body, HTTPException, Request

from .services import auth, billing, members, notify

router = APIRouter(tags=["member"])


def _device(request: Request) -> str:
    return auth._device_id(request)


# ── 方案（前台「方案」頁用）─────────────────────────
@router.get("/api/pay/plans")
async def get_plans() -> dict:
    return {
        "ok": True,
        "enabled": billing.enabled(),
        "plans": billing.plans(),
        "providers": billing.available_providers(),
    }


# ── 會員 ─────────────────────────────────────────────
@router.post("/api/member/register")
async def register(request: Request, body: dict = Body(...)) -> dict:
    m = members.register(body.get("email", ""), body.get("password", ""),
                         device_id=_device(request), tz=body.get("tz"))
    return {"ok": True, "member": m, "token": members.issue_token(m["id"])}


@router.post("/api/member/login")
async def login(request: Request, body: dict = Body(...)) -> dict:
    m = members.login(body.get("email", ""), body.get("password", ""),
                      device_id=_device(request))
    return {"ok": True, "member": {"id": m["id"], "email": m["email"], "plan": m["plan"]},
            "token": m["token"]}


@router.get("/api/member/me")
async def me(request: Request) -> dict:
    mid = auth._member_from_request(request)
    if not mid:
        return {"ok": True, "logged_in": False, "plan": "free",
                "unlimited": billing.is_unlimited(auth.current_subject(request))}
    m = members.get(mid) or {}
    return {"ok": True, "logged_in": True, "member": m,
            "plan": m.get("plan", "free"),
            "unlimited": billing.is_unlimited(f"user:{mid}")}


# ── 付款 ─────────────────────────────────────────────
@router.get("/api/pay/providers")
async def pay_providers() -> dict:
    """前台付款方式（金流商設好環境變數才會 ready）。

    小羅 2026-09-27：先把付款機制的位置留好，
    之後接第三方支付只要設環境變數，不用改程式。
    """
    return {"ok": True, "providers": billing.available_providers(),
            "plans": billing.plans(), "enabled": billing.enabled()}


@router.post("/api/pay/checkout")
async def checkout(request: Request, body: dict = Body(...)) -> dict:
    subject = auth.current_subject(request)
    plan = body.get("plan", "")
    provider = body.get("provider", "ecpay")
    base = str(request.base_url).rstrip("/")
    return {"ok": True, **billing.create_checkout(subject, plan, provider, base_url=base)}


@router.post("/api/pay/webhook/{provider}")
async def webhook(provider: str, request: Request) -> dict:
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    return {"ok": True, **billing.handle_webhook(provider, raw, headers)}



# ── 使用者回報問題（規格書通知清單第 13 項）───────────
@router.post("/api/report")
async def report(request: Request, body: dict = Body(...)) -> dict:
    """收下使用者的回報（任何人都能送，不限會員）。

    ⚠️ 小羅 2026-09-27 的要求：
      1. 不是只有會員才能回報（免費用完次數的人也能送）
      2. 信件要**彙總**，不要一則回報寄一封（會很恐怖）
         → 靠 notify 的 30 分鐘冷卻：冷卻期間的回報會合併進下一封
    """
    from .core import db
    from .services import feedback

    msg = (body.get("message") or "").strip()[:1500]
    if len(msg) < 4:
        raise HTTPException(status_code=400, detail="請多描述一點")
    feedback.add(message=msg, device_id=_device(request), contact=body.get("contact"),
                 platform=body.get("platform"), url=body.get("url"))

    try:
        since = float(db.get_setting("notify_last_report_ts") or 0)
    except (TypeError, ValueError):
        since = 0.0
    since = since or (time.time() - 86400)
    subject, digest = feedback.digest_since(since)
    if subject:
        n = len(feedback.unhandled_since(since))
        r = await notify.notify("user_report", f"使用者回報（{n} 則）", digest)
        if r.get("sent"):
            db.set_setting("notify_last_report_ts", time.time())
    return {"ok": True, "message": "已收到，謝謝你！我們會盡快處理。"}


# ── 解析事件（前端回報；特別是「請求還沒到伺服器」的失敗）──────
@router.post("/api/track/resolve")
async def track_resolve(request: Request, body: dict = Body(...)) -> dict:
    """讓前端補報解析結果。

    為什麼需要：手機在「請求還沒送到伺服器」就逾時、或 Cloudflare 直接回 502 時，
    後端完全不會有紀錄 → 後台成功率會假性 100%（小羅 2026-09-27 發現）。
    """
    from .services import events

    events.track("resolve", device_id=auth._device_id(request),
                 platform=body.get("platform") or "",
                 result="ok" if body.get("result") == "ok" else "fail",
                 error_code=body.get("code") or body.get("error") or "CLIENT_ERROR",
                 url=body.get("url"),
                 country=request.headers.get("cf-ipcountry"))
    return {"ok": True}

@router.post("/api/track/download")
async def track_download(request: Request, body: dict = Body(...)) -> dict:
    from .services import events

    events.track("download", device_id=auth._device_id(request),
                 platform=body.get("platform"), quality=body.get("quality"),
                 size=body.get("size"), mode=body.get("mode"),
                 result="ok" if body.get("ok", True) else "fail",
                 error_code=body.get("error"),
                 url=body.get("url"))
    return {"ok": True}
