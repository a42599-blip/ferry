"""會員與付款 API（規格書第 6、11、20 章）。

會員：/api/member/register、/login、/me
付款：/api/pay/plans、/checkout、/webhook/{provider}
"""
from __future__ import annotations

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
    msg = (body.get("message") or "").strip()[:800]
    if len(msg) < 4:
        raise HTTPException(status_code=400, detail="請多描述一點")
    await notify.notify("user_report", "使用者回報問題",
                        f"內容：{msg}\n裝置：{_device(request)}\n"
                        f"聯絡：{body.get('contact') or '（未提供）'}")
    return {"ok": True, "message": "已收到，謝謝你！我們會盡快處理。"}


# ── 下載事件（前端下載完成後回報，用於後台統計）──────
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
