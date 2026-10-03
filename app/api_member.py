"""會員與付款 API（規格書第 6、11、20 章）。

會員：/api/member/register、/login、/me
付款：/api/pay/plans、/checkout、/webhook/{provider}
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse

from .core.errors import BadRequest
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
    # 記錄註冊時的地區（Cloudflare 會給 cf-ipcountry）→ 後台可看地區分佈
    m = members.register(body.get("email", ""), body.get("password", ""),
                         device_id=_device(request), tz=body.get("tz"),
                         country=request.headers.get("cf-ipcountry"))
    return {"ok": True, "member": m, "token": members.issue_token(m["id"])}


@router.post("/api/member/login")
async def login(request: Request, body: dict = Body(...)) -> dict:
    m = members.login(body.get("email", ""), body.get("password", ""),
                      device_id=_device(request))
    return {"ok": True, "member": {"id": m["id"], "email": m["email"], "plan": m["plan"]},
            "token": m["token"]}


@router.post("/api/ads/start")
async def ads_start(request: Request) -> dict:
    """開始看廣告（記錄時間）。看滿 `ADS_MIN_SECONDS` 秒才能領次數。

    小羅 2026-09-29：「彈出來就關掉，當然不能給他加次數，我得不到廣告費啊。」
    """
    from .core import config
    from .services import ads as ads_service

    subject = auth.current_subject(request)
    seconds = ads_service.mark_start(subject)
    return {"ok": True, "min_seconds": seconds, "default": int(getattr(config.settings, "ads_min_seconds", 15) or 15)}


@router.post("/api/ads/reward")
async def ads_reward(request: Request, body: dict = Body(default={})) -> dict:
    """看完廣告 → 依等級把免費次數加回來（小羅 2026-09-29）。

    ⚠️ 現在**只是預留**：廣告碼還沒接（彈窗是空白的）。
       之後接廣告時，只要把廣告碼貼進前台 `#ad-slot` 就好，這裡不用改。

    規則：
      · 訪客：用滿 3 次 → 第 4 次要看廣告 → 看完「再給 3 次」
      · 免費會員：用滿 5 次 → 第 6 次要看廣告 → 看完「再給 5 次」
      · 月／永久會員：沒有開關、永遠不看
    防濫用（三個都要過）：① 該等級的廣告開關要開 ② 現在真的「該看廣告」③ 一次只補一格
    """
    from .services import ads as ads_service
    from .services import members as members_service
    from .services import quota as quota_service

    mid = auth.current_member_id(request)
    tier = members_service.tier_of(mid)
    subject = auth.current_subject(request)
    kind = "transfer" if str((body or {}).get("kind") or "") == "transfer" else "download"

    # 廣告的計次＝下載＋傳輸「合併」（與 /api/quota 的 ads.used 一致）
    used_all = quota_service.used("download", subject) + quota_service.used("transfer", subject)
    st = ads_service.state(tier, used_all)
    if not st["enabled"]:
        raise BadRequest("廣告功能尚未啟用")
    if not st["due"]:
        raise BadRequest("目前不需要看廣告")

    # 必須「看滿廣告秒數」才給（伺服器端判定，前端改不動）
    from .core import config

    min_sec = int(getattr(config.settings, "ads_min_seconds", 15) or 15)
    if not ads_service.watch_ok(subject, min_sec):
        raise BadRequest(f"請先看廣告 {min_sec} 秒，看完才能繼續")

    n = ads_service.every_of(tier) or 1

    # ⚠️⚠️ 2026-09-29 修（小羅：「看完 15 秒按繼續，又跳一個廣告，一直跳、根本不能用」）：
    #   ① `quota_service.adjust` 的規則是「**正數＝加次數（把已用減掉）**」（見 quota.adjust 說明），
    #      原本傳 `-n` 反而＝「再用掉 n 次」→ 已用越看越多 → 廣告永遠 due（無限迴圈）。
    #   ② 廣告的計次是「下載＋傳輸**合併**」，所以加次數也要對「合併」處理：
    #      只扣被擋的那一種時，若另一種已經用滿，合併次數還是 >= 門檻 → 又跳一次廣告。
    #   ③ 目標：看完之後「合併已用」要**低於門檻**（不然客人會再被跳一次），
    #      而且「至少可以再用 n 次」。
    def _net(k: str) -> int:
        return max(0, quota_service.used(k, subject))

    total = _net("download") + _net("transfer")
    _used_before = total
    target = min(max(0, total - n), max(0, n - 1))
    other = "transfer" if kind == "download" else "download"
    for _k in (kind, other):
        if total <= target:
            break
        cut = min(_net(_k), total - target)
        if cut:
            quota_service.adjust(_k, subject, cut)      # 正數＝把已用減掉（真的加次數）
            total -= cut
    # ── 後台「📺 廣告」統計（小羅 2026-09-29：誰看幾次、看幾秒、給幾次、哪邊在看）──
    _after = _net("download") + _net("transfer")
    _email = ""
    if mid:
        try:
            _email = (members_service.get(mid) or {}).get("email") or ""
        except Exception:  # noqa: BLE001
            _email = ""
    ads_service.record_view(
        subject=subject, tier=tier, member_id=mid or "", member_email=_email,
        seconds=ads_service.elapsed(subject), min_seconds=min_sec, granted=n, kind=kind,
        used_before=_used_before, used_after=_after,
        country=(request.headers.get("cf-ipcountry") or "").upper(),
        device_id=auth._device_id(request),
        platform=ads_service.platform_of(request.headers.get("user-agent") or ""))

    return {"ok": True, "granted": n, "kind": kind,
            "quota": quota_service.status(subject), "tier": tier}


@router.post("/api/member/nickname")
async def set_nickname(request: Request, body: dict = Body(...)) -> dict:
    """會員自己改暱稱（小羅 2026-09-29：「讓客戶自己改自己的名稱」）。"""
    mid = auth._member_from_request(request)
    if not mid:
        raise BadRequest("請先登入會員")
    from .services import members

    try:
        m = members.set_nickname(mid, str(body.get("nickname") or ""))
    except ValueError as exc:
        raise BadRequest(str(exc))
    return {"ok": True, "nickname": (m or {}).get("nickname") or "", "member": m}


@router.post("/api/member/password")
async def change_password(request: Request, body: dict = Body(...)) -> dict:
    """會員自己改密碼（小羅 2026-09-29：「前後台都要有改帳密的按鈕和邏輯」）。

    必須先輸入目前的密碼（避免手機被別人拿去改）。
    """
    mid = auth._member_from_request(request)
    if not mid:
        raise BadRequest("請先登入會員")
    cur = str(body.get("current") or "")
    new = str(body.get("password") or "")
    if not members.check_password(mid, cur):
        raise BadRequest("目前的密碼不正確")
    if len(new) < 6:
        raise BadRequest("新密碼至少 6 個字")
    members.set_password(mid, new)
    return {"ok": True, "message": "密碼已更新，下次請用新密碼登入"}


@router.post("/api/member/forgot")
async def forgot_password(request: Request, body: dict = Body(...)) -> dict:
    """忘記密碼：寄一封重設信。

    小羅 2026-09-29：「前台登入頁是不是也應該有一個忘記密碼，
    那這個忘記密碼也要真實有效，可以恢復或者是重設密碼。」

    ⚠️ 不管這個 Email 有沒有註冊，回覆都一樣（避免被拿去探測帳號）。
    """
    from .services import notify

    email = str(body.get("email") or "").strip().lower()
    r = members.create_reset(email)
    same_msg = ("如果這個 Email 有在我們這裡註冊，"
                "我們已經寄出重設信，請去收信（也看一下垃圾信匣）。重設連結 30 分鐘內有效。")
    if not r:
        return {"ok": True, "message": same_msg}
    # 小羅 2026-10-04：Railway 在 Cloudflare 後面會偵測成 http → 一律用 https
    base = str(request.base_url).rstrip("/").replace("http://", "https://")
    link = f"{base}/?reset={r['token']}"
    text = ("你好，\n\n"
            "我們收到你在「轉運站」的重設密碼申請。\n"
            "請點下面的連結設定新密碼（30 分鐘內有效，只能用一次）：\n\n"
            f"{link}\n\n"
            "如果不是你本人申請，忽略這封信就好，你的密碼不會被更改。\n\n"
            "轉運站  https://scefo.com\n")
    try:
        await notify.send_now("[轉運站] 重設你的密碼", text, to=[r["email"]])
    except Exception:  # noqa: BLE001
        pass          # 寄不出去也不讓外界知道（後台通知紀錄看得到）
    return {"ok": True, "message": same_msg}


@router.post("/api/member/reset")
async def reset_password(body: dict = Body(...)) -> dict:
    """用重設碼設定新密碼（一次性、30 分鐘內有效）。"""
    r = members.use_reset(str(body.get("token") or ""),
                          str(body.get("password") or ""))
    return {"ok": True, "email": r.get("email") or "",
            "message": "密碼已重設，請用新密碼登入"}


@router.post("/api/member/find-account")
async def find_account(body: dict = Body(...)) -> dict:
    """忘記帳號：用暱稱查 → **寄到那個帳號的信箱**（畫面統一回覆，不洩漏帳號）。

    小羅 2026-10-04：不可以「随便寫個忘記帳號就給帳號」→
    所以改成：不管有沒有找到，畫面都回一樣的話；
    真的有對應帳號時，把「你的登入帳號」寄到那個信箱去。
    """
    from .services import notify

    nk = str(body.get("nickname") or "").strip()
    if len(nk) < 1:
        raise BadRequest("請輸入你的暱稱")

    same = ("如果這個暱稱有對應的帳號，我們已經把「你的登入帳號」"
            "寄到那個 Email 了，請去收信（也看一下垃圾信匣）。")
    emails = members.find_account(nk)
    sent = 0
    for email in emails:
        text = ("你好，\n\n"
                "有人（應該是你）在「轉運站」使用「忘記帳號」功能。\n\n"
                f"你的登入帳號是：{email}\n\n"
                "登入後可以到會員頁設定/修改密碼。\n"
                "如果不是你本人查詢，忽略這封信就好。\n\n"
                "轉運站  https://scefo.com\n")
        try:
            r = await notify.send_now("[轉運站] 你的登入帳號", text, to=[email])
            sent += 1 if r.get("ok") else 0
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "sent": sent, "message": same}


@router.get("/api/member/me")
async def me(request: Request) -> dict:
    mid = auth._member_from_request(request)
    if not mid:
        return {"ok": True, "logged_in": False, "plan": "free", "tier": "guest",
                "nickname": "", "unlimited": billing.is_unlimited(auth.current_subject(request))}
    # 帶著登入狀態進站 → 記一次「上線」（同一工作階段不重複計數）
    members.touch_session(mid, device_id=_device(request),
                          country=request.headers.get("cf-ipcountry"))
    m = members.get(mid) or {}
    # 小羅 2026-09-29：前台要能分辨「訪客／免費／月／永久」，也要顯示暱稱
    return {"ok": True, "logged_in": True, "member": m,
            "plan": m.get("plan", "free"),
            "tier": members.tier_of(mid),
            "nickname": m.get("nickname") or "",
            "unlimited": billing.is_unlimited(f"user:{mid}")}


@router.get("/api/my-replies")
async def my_replies(request: Request) -> dict:
    """客戶看自己送出回報後，客服給的回覆。

    小羅 2026-09-27：客服在後台回覆後，客戶要能看得到
    （例如「很抱歉，已幫你補回下載次數 3 次」）。
    """
    from .services import feedback as fb

    dev = _device(request)
    mid = auth.current_member_id(request)      # 已登入就一併查他帳號名下所有裝置
    return {"ok": True,
            "items": fb.for_device(dev, mid),
            "pending": fb.unhandled_for(dev, mid),
            "logged_in": bool(mid)}


# ── 註銷帳號（**使用者自己**註銷；小羅 2026-09-27 要求）──────────
#    ⚠️ 不是後台代為註銷 —— 要讓使用者自己決定。
#    警語在**前端**跳（依有沒有付費給不同警告）：
#      • 已付費 → 費用不退、資格立即失效
#      • 未付費 → 之後要再用會員功能必須重新申請
@router.delete("/api/member/me")
async def delete_me(request: Request) -> dict:
    """註銷自己的帳號。

    ⚠️ 只刪「帳號」本身；不記名的流量統計（events）保留，
       否則後台的流量數字會對不上。裝置與帳號的關聯會切斷。
    """
    mid = auth.current_member_id(request)
    if not mid:
        raise HTTPException(status_code=401, detail="尚未登入")
    members.delete(mid)
    return {"ok": True, "message": "帳號已註銷，謝謝你曾經使用。"}


# ── 付款 ─────────────────────────────────────────────
@router.get("/api/pay/providers")
async def pay_providers() -> dict:
    """前台付款方式（金流商設好環境變數才會 ready）。

    小羅 2026-09-27：先把付款機制的位置留好，
    之後接第三方支付只要設環境變數，不用改程式。
    """
    return {"ok": True, "providers": billing.available_providers(),
            "plans": billing.plans(), "enabled": billing.enabled(),
            "paypal_client_id": billing.paypal_client_id()}


@router.post("/api/pay/checkout")
async def checkout(request: Request, body: dict = Body(...)) -> dict:
    subject = auth.current_subject(request)
    plan = body.get("plan", "")
    provider = body.get("provider", "ecpay")
    # 小羅 2026-10-04：Railway 在 Cloudflare 後面會偵測成 http → 一律用 https
    base = str(request.base_url).rstrip("/").replace("http://", "https://")
    return {"ok": True, **billing.create_checkout(subject, plan, provider, base_url=base)}


@router.get("/api/pay/paypal/return", response_class=HTMLResponse)
async def paypal_return(token: str = "", order_id: str = "") -> HTMLResponse:
    """PayPal 付款完成後導回（彈窗內）。確認收款才開通，並顯示可關閉的完成畫面。"""
    ok = False
    err = ""
    try:
        billing.capture_paypal(order_id, token)
        ok = True
    except Exception as e:  # noqa: BLE001
        ok = False
        err = str(e)[:120]
        try:
            from .services import events as _ev
            if order_id:
                _ev.mark_failed(order_id, err or "付款失敗")
        except Exception:  # noqa: BLE001
            pass
    title = "開通成功" if ok else "付款未完成"
    emoji = "🎉" if ok else "⚠️"
    desc = ("恭喜你，已開通成為會員！\n這個視窗會自動關閉。"
            if ok else "付款沒有完成，未開通會員。\n請關閉視窗後再試一次。")
    desc_html = desc.replace("\n", "<br>")
    html = f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{margin:0;font-family:-apple-system,"Microsoft JhengHei",sans-serif;background:#0f1115;color:#eef2f7;
 display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px;box-sizing:border-box}}
.card{{background:#171b22;border:1px solid #2a313d;border-radius:16px;padding:28px 24px;max-width:420px;width:100%;text-align:center}}
h1{{font-size:20px;margin:12px 0 10px}}p{{color:#c7d0dc;line-height:1.7;font-size:15px;margin:0 0 18px}}
button{{width:100%;padding:13px;border:0;border-radius:10px;font-size:16px;font-weight:700;cursor:pointer;
 background:linear-gradient(90deg,#7b5cff,#2f80ff);color:#fff}}
</style></head><body><div class="card">
<div style="font-size:44px">{emoji}</div><h1>{title}</h1><p>{desc_html}</p>
<button onclick="goHome()">回到轉運站主頁</button>
<div style="margin-top:10px;font-size:12.5px;color:#8b95a3">回到主頁後會自動更新為會員狀態</div>
</div><script>
function goHome(){{try{{if(window.opener&&!window.opener.closed){{window.opener.location.href="/?pay=ok";window.opener.location.reload();}}}}catch(e){{}}try{{window.close()}}catch(e){{location.href="/";}}}}
setTimeout(function(){{try{{window.close()}}catch(e){{}}}}, {3000 if ok else 10000});
</script></body></html>"""
    return HTMLResponse(content=html)


@router.post("/api/pay/paypal/capture")
async def paypal_capture(body: dict = Body(...)) -> dict:
    """站內彈窗付款完成後呼叫；確認 PayPal 真的收到錢才開通（付款失敗不開通）。"""
    return {"ok": True, **billing.capture_paypal(
        str(body.get("order_id", "")), str(body.get("paypal_order_id", "")))}


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
