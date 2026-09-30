"""後台 API（規格書第 10 章）。

五個頁面對應五組端點：
  /admin/api/overview   總覽
  /admin/api/flags      功能與平台開關
  /admin/api/devices    會員與裝置
  /admin/api/revenue    收益報表
  /admin/api/errors     錯誤與告警
外加 /admin/api/system（系統資訊、通知設定）與 /admin/api/data（匯出／刪除）。
"""
from __future__ import annotations

import csv
import io
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..core import db, registry
from ..core.config import settings
from ..services import events, flags
from . import security

router = APIRouter(prefix="/admin/api", tags=["admin"])

#: 有補償（加/減次數）時附加的提醒
TIP_REFRESH = "請重新刷新網頁檢視次數。"


# ── 登入 ──────────────────────────────────────────────
def require_admin(authorization: str = Header(default="")) -> dict:
    token = authorization.replace("Bearer ", "").strip()
    payload = security.verify_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="請重新登入")
    return payload


@router.post("/login")
async def login(body: dict = Body(...)) -> dict:
    user = (body.get("user") or "").strip()
    password = body.get("password") or ""
    code = (body.get("code") or "").strip()

    if not security.check_password(user, password):
        await _alert_login_fail(user)
        time.sleep(0.6)                       # 簡單防暴力
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    if security.totp_enabled():
        if not code:
            return {"ok": False, "need_code": True, "message": "請輸入兩步驟驗證碼"}
        if not security.totp_verify(code):
            await _alert_login_fail(user)
            raise HTTPException(status_code=401, detail="驗證碼錯誤")

    return {"ok": True, "token": security.issue_token(user), "user": user}


async def _alert_login_fail(user: str) -> None:
    """後台登入失敗 → 通知（規格書通知清單 #9）。"""
    try:
        from ..services import notify

        await notify.notify("admin_login_fail", "後台登入失敗",
                            f"有人嘗試登入後台但失敗（帳號：{user}）。若不是你，請盡快改密碼。")
    except Exception:  # noqa: BLE001
        pass


@router.get("/session")
async def session(_: dict = Depends(require_admin)) -> dict:
    return {"ok": True, "totp_enabled": security.totp_enabled(),
            "user": security.current_user()}


# ── 總覽 ──────────────────────────────────────────────
@router.get("/overview")
async def overview(days: int = Query(30, ge=1, le=365), _: dict = Depends(require_admin)) -> dict:
    return {
        "ok": True,
        "summary": events.overview(days),
        "series": {
            "page_view": events.daily_series("page_view", days=min(days, 30)),
            "resolve": events.daily_series("resolve", days=min(days, 30)),
            "download": events.daily_series("download", days=min(days, 30)),
        },
        "platforms": events.by_platform(days=min(days, 30)),
        "countries": events.by_country(days=days),
        "devices_os": events.by_device(days=days),
        "sources": events.by_source(days=days),
        "transfer": events.transfer_stats(days=days),
    }


# ── 功能與平台開關 ────────────────────────────────────
@router.get("/flags")
async def get_flags(_: dict = Depends(require_admin)) -> dict:
    flags.refresh()
    stats = {s["platform"]: s for s in events.by_platform(days=7)}
    plats = []
    for r in registry.all_platforms():
        s = stats.get(r.name, {})
        plats.append({
            "id": r.name,
            "label": r.label,
            "enabled": flags.platform_enabled(r.name),
            "today_total": s.get("total", 0),
            "success_rate": s.get("success_rate"),
            "avg_ms": s.get("avg_ms"),
            "hosts": list(r.hosts),
        })
    return {
        "ok": True,
        "features": flags.all_features(),
        "platforms": plats,
        "auto_off": flags.auto_off_enabled(),
        "threshold": flags.AUTO_OFF_THRESHOLD,
    }


@router.put("/flags")
async def put_flags(body: dict = Body(...), _: dict = Depends(require_admin)) -> dict:
    if isinstance(body.get("features"), dict):
        for k, v in body["features"].items():
            flags.set_feature(k, bool(v))
    if isinstance(body.get("platforms"), dict):
        for k, v in body["platforms"].items():
            flags.set_platform(k, bool(v))
    if "auto_off" in body:
        flags.set_auto_off(bool(body["auto_off"]))
    flags.refresh()
    return {"ok": True, "features": flags.all_features(),
            "platforms": {r.name: flags.platform_enabled(r.name)
                          for r in registry.all_platforms()}}


@router.post("/flags/all")
async def all_flags(on: bool = Query(...), _: dict = Depends(require_admin)) -> dict:
    flags.set_all(
        features={k: on for k in flags.all_features()},
        platforms={r.name: on for r in registry.all_platforms()},
    )
    flags.refresh()
    return {"ok": True, "on": on}


# ── 會員與裝置 ────────────────────────────────────────
# ── 📺 廣告統計（小羅 2026-09-29：後台一定要看到看幾次、看多久、給幾次、誰在看）──
@router.get("/ads")
async def ads_stats(days: int = Query(30, ge=1, le=365), network: str = Query(""),
                    _: dict = Depends(require_admin)) -> dict:
    from ..services import ads as ads_service

    return {"ok": True, **ads_service.stats(days=days, network=network)}


@router.post("/ads/network")
async def ads_set_network(body: dict = Body(...),
                          _: dict = Depends(require_admin)) -> dict:
    """切換「目前上線中的廣告商」（統計會按家分開，不會混在一起）。"""
    from ..services import ads as ads_service

    code = str((body or {}).get("network") or "").strip()
    if code not in [c for c, _ in ads_service.NETWORKS]:
        raise HTTPException(status_code=400, detail="不認識的廣告商代號")
    return {"ok": True, "active_network": ads_service.network_of(code)}


@router.post("/ads/reported")
async def ads_set_reported(body: dict = Body(...),
                           _: dict = Depends(require_admin)) -> dict:
    """手動輸入「廣告商後台回報的曝光數」（對帳用）。"""
    from ..services import ads as ads_service

    try:
        return {"ok": True, **ads_service.set_reported(
            body.get("date"), body.get("count"), body.get("network") or "")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ads/export")
async def ads_export(days: int = Query(180, ge=1, le=365),
                     _: dict = Depends(require_admin)) -> StreamingResponse:
    """匯出廣告明細 CSV（對帳用）。"""
    from ..services import ads as ads_service

    rows = ads_service.export_rows(days=days)
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ad_views.csv"'})


# ── 關於我們（小羅 2026-09-30：前台「關於我們」頁的聯絡資料，後台可改）──
@router.get("/about")
async def get_about(_: dict = Depends(require_admin)) -> dict:
    from ..services import about as about_service

    return {"ok": True, **about_service.get()}


@router.post("/about")
async def save_about(body: dict = Body(...), _: dict = Depends(require_admin)) -> dict:
    from ..services import about as about_service

    return {"ok": True, **about_service.save(body or {})}


@router.get("/devices")
async def devices(days: int = Query(30, ge=1, le=365), _: dict = Depends(require_admin)) -> dict:
    return {"ok": True, "devices": events.list_devices(days=days)}


@router.get("/members")
async def members_list(_: dict = Depends(require_admin)) -> dict:
    """會員統計 ＋ 名單（小羅 2026-09-27：這些數字都要留）。"""
    from ..services import members

    return {"ok": True, "stats": members.stats(), "members": members.list_full()}


# ── 公告管理（小羅 2026-09-27：前台要留一個公告區塊）──────
@router.get("/announcements")
async def list_announcements(_: dict = Depends(require_admin)) -> dict:
    from ..services import announce

    return {"ok": True, "items": announce.list_all(), "levels": announce.LEVELS}


@router.post("/announcements")
async def create_announcement(body: dict = Body(...), _: dict = Depends(require_admin)) -> dict:
    from ..services import announce

    title = (body.get("title") or "").strip()
    content = (body.get("body") or "").strip()
    if not title or len(content) < 2:
        raise HTTPException(status_code=400, detail="標題與內容都要填")
    hours = float(body.get("hours") or 0)
    row = announce.add(title, content, level=body.get("level") or "info",
                       expires_at=(time.time() + hours * 3600) if hours > 0 else None)
    return {"ok": True, "row": row, "items": announce.list_all()}


@router.post("/announcements/{aid}/active")
async def toggle_announcement(aid: int, body: dict = Body(default={}),
                              _: dict = Depends(require_admin)) -> dict:
    from ..services import announce

    announce.set_active(aid, bool((body or {}).get("on", True)))
    return {"ok": True, "items": announce.list_all()}


@router.delete("/announcements/{aid}")
async def delete_announcement(aid: int, _: dict = Depends(require_admin)) -> dict:
    from ..services import announce

    announce.remove(aid)
    return {"ok": True, "items": announce.list_all()}


@router.get("/members/emails")
async def members_emails(_: dict = Depends(require_admin)) -> dict:
    """會員 Email 名單（寄通知用）。"""
    from ..services import members

    return {"ok": True, "all": members.counts_by("all"),
            "paid": members.counts_by("paid"), "free": members.counts_by("free"),
            "sample": members.emails("all")[:50]}


@router.post("/members/broadcast")
async def members_broadcast(body: dict = Body(...), _: dict = Depends(require_admin)) -> dict:
    """對會員發通知（平台有問題／要關掉／要更新時用）。"""
    from ..services import notify

    subject = (body.get("subject") or "").strip()
    content = (body.get("body") or "").strip()
    if not subject or len(content) < 5:
        raise HTTPException(status_code=400, detail="主旨與內容都要填")
    r = await notify.broadcast(subject, content, only=body.get("only") or "all")
    return {"ok": True, "result": r}



# ── 會員查詢與手動管理（小羅 2026-09-27：客服＋賠償機制）────────

# ══════════════════════════════════════════════════════════════
#  會員資料模塊（小羅 2026-09-27：獨立模塊，壞了單獨修）
#  「我要有一個搜尋列，找到他然後點進他的會員資料卡；
#    次數、天數都要我自己填數字，不要固定 7 天 30 天。」
# ══════════════════════════════════════════════════════════════
@router.get("/members/list")
async def members_page(q: str = Query(""), plan: str = Query(""),
                       sort: str = Query("created_desc"),
                       page: int = Query(1, ge=1), size: int = Query(25, ge=5, le=200),
                       _: dict = Depends(require_admin)) -> dict:
    """會員清單（搜尋／篩選／排序／分頁）——1000 個客戶也找得到。"""
    from ..services import members

    return {"ok": True, **members.page(q=q, plan=plan, sort=sort, page=page, size=size)}


@router.get("/members/{member_id}/card")
async def member_card(member_id: str, _: dict = Depends(require_admin)) -> dict:
    """會員資料卡（含方案歷史、最近活動、回報紀錄）。"""
    from ..services import members

    card = members.card(member_id)
    if not card:
        raise HTTPException(status_code=404, detail="找不到這個會員")
    return {"ok": True, "card": card}


@router.post("/members/{member_id}/adjust")
async def adjust_member(member_id: str, body: dict = Body(...),
                        _: dict = Depends(require_admin)) -> dict:
    """手動調整（賠償機制）：加天數、加次數、改方案、停權。

    小羅：「我可以加 1 天 2 天 5 天 8 天甚至一個月；
           次數我可以加 1 次 10 次 100 次 1000 次 —— 我自己填數字。」
    """
    from ..services import members, quota

    days = int(body.get("days") or 0)
    plan = (body.get("plan") or "").strip()
    q_dl = int(body.get("quota_download") or 0)
    q_tr = int(body.get("quota_transfer") or 0)
    note = (body.get("note") or "後台手動調整")[:200]
    done = []

    if plan:
        members.grant_plan(member_id, plan, days, reason="gift", note=note)
        done.append(f"方案→{plan}" + (f'(+{days}天)' if days else ""))
    elif days:
        members.extend_days(member_id, days, reason="gift", note=note)
        done.append(f"加 {days} 天")
    if q_dl:
        quota.adjust("download", f"user:{member_id}", q_dl)
        done.append(f"下載次數 +{q_dl}")
    if q_tr:
        quota.adjust("transfer", f"user:{member_id}", q_tr)
        done.append(f"傳輸次數 +{q_tr}")

    return {"ok": True, "done": done, "card": members.card(member_id)}


@router.post("/members/backfill")
async def members_backfill(_: dict = Depends(require_admin)) -> dict:
    """回填早期資料（登入次數／最後登入／地區）。

    小羅 2026-09-27：「他中間有沒有登入過第 2 次第 3 次？資料要清楚」
    早期註冊的會員沒有這些欄位，從 events 撈回來補上。
    """
    from ..services import members

    logins = members.backfill_logins()
    countries = members.backfill_country()
    return {"ok": True, "logins": logins, "countries": countries,
            "stats": members.stats()}


@router.delete("/members-test/{member_id}")
async def delete_test_member(member_id: str, _: dict = Depends(require_admin)) -> dict:
    """刪除「測試帳號」（只允許 @example.com / @ferry.local / @test 結尾）。

    ⚠️ 安全設計：不開放刪除一般會員，避免誤刪真人帳號。
    小羅 2026-09-27：「這個 email 我看起來不一樣啊」→ 測試帳號沒清掉污染數據。
    """
    from ..services import members

    m = members.get(member_id) or {}
    email = (m.get("email") or "").lower()
    if not email.endswith(("@example.com", "@ferry.local", "@test", "@test.com")):
        raise HTTPException(status_code=400,
                            detail="只允許刪除測試帳號（@example.com 等），避免誤刪真人")
    members.delete(member_id, hard=True)     # 測試帳號才真刪
    return {"ok": True, "deleted": email}


@router.delete("/members/{member_id}")
async def delete_member(member_id: str, confirm: str = Query(""),
                        reason: str = Query(""),
                        _: dict = Depends(require_admin)) -> dict:
    """刪除（註銷）一般會員帳號。

    小羅 2026-09-29：「我停權他就是停掉了我可以復權，那我也可以刪除他這個帳號對不對？」

    ⚠️ 安全設計：
      · 必須帶 `?confirm=DELETE` 才真的動作（避免誤刪）
      · 預設是**軟刪除**（status=deleted）：立即進不去、資料保留、可從「復原」救回
    """
    if confirm != "DELETE":
        raise HTTPException(status_code=400, detail="需要二次確認（confirm=DELETE）")
    from ..services import members

    m = members.get(member_id)
    if not m:
        raise HTTPException(status_code=404, detail="找不到這個會員")
    members.delete(member_id, hard=False)
    return {"ok": True, "deleted": m.get("email") or member_id, "soft": True,
            "reason": reason or "後台註銷",
            "hint": "已註銷（登入會被擋）；可用 /members/{id}/restore 救回"}


@router.post("/members/rebuild")
async def rebuild_member(body: dict = Body(...),
                         _: dict = Depends(require_admin)) -> dict:
    """用已知的原始資料重建一位被刪掉的會員（含裝置／國家／時間）。"""
    from ..services import members

    m = members.rebuild(body.get("email") or "", body.get("device_id") or "",
                        created_at=float(body["created_at"]) if body.get("created_at") else None,
                        plan=body.get("plan") or "free",
                        expires_at=float(body["expires_at"]) if body.get("expires_at") else None,
                        country=body.get("country"))
    return {"ok": True, "member": m, "stats": members.stats()}


@router.post("/members/restore")
async def restore_members(_: dict = Depends(require_admin)) -> dict:
    """從方案歷史把「被刪掉而消失」的會員重建回來。

    小羅 2026-09-27：「把歷史資料再給我撈出來再回來。」
    plan_history 記了每次方案變更（含 email／方案／時間），
    所以 members 表被硬刪除也救得回來。
    """
    from ..services import members

    r = members.restore_from_history()
    return {"ok": True, **r, "stats": members.stats()}


@router.post("/members/{member_id}/restore")
async def restore_one(member_id: str, _: dict = Depends(require_admin)) -> dict:
    """復原一位被「軟刪除」的會員。"""
    from ..services import members

    if not members.restore(member_id):
        raise HTTPException(status_code=404, detail="找不到這個會員")
    return {"ok": True, "member": members.get(member_id) or {}}


@router.get("/members/search")
async def search_members(q: str = Query(""), _: dict = Depends(require_admin)) -> dict:
    """用 Email 或會員 ID 查會員（附方案歷史）。"""
    from ..services import members

    rows = members.search(q)
    for r in rows:
        r["history"] = members.plan_history(r["id"], limit=20)
    return {"ok": True, "rows": rows}


@router.post("/members/{member_id}/grant")
async def grant_member(member_id: str, body: dict = Body(...),
                       _: dict = Depends(require_admin)) -> dict:
    """臨時開通／補償（給方案 ＋ 天數）。"""
    from ..services import members

    m = members.grant_plan(member_id, body.get("plan") or "monthly",
                          int(body.get("days") or 0),
                          reason="gift", note=body.get("note") or "")
    return {"ok": True, "member": m}  


@router.post("/members/{member_id}/days")
async def add_member_days(member_id: str, body: dict = Body(...),
                          _: dict = Depends(require_admin)) -> dict:
    """臨時加時間（補償幾天）。"""
    from ..services import members

    m = members.extend_days(member_id, int(body.get("days") or 1),
                            reason="gift", note=body.get("note") or "")
    return {"ok": True, "member": m}


@router.post("/members/{member_id}/status")
async def set_member_status(member_id: str, body: dict = Body(...),
                            _: dict = Depends(require_admin)) -> dict:
    """停權／復權。"""
    from ..services import members

    status = body.get("status") or "active"
    members.set_status(member_id, status)
    if status == "suspended":
        members.set_plan(member_id, "free", None, reason="suspend", note="後台停權")
    return {"ok": True, "member": members.get(member_id) or {}}


@router.post("/members/{member_id}/quota")
async def add_member_quota(member_id: str, body: dict = Body(...),
                           _: dict = Depends(require_admin)) -> dict:
    """臨時加免費次數（補償；kind = download / transfer）。"""
    from ..services import quota

    kind = body.get("kind") or "download"
    n = int(body.get("n") or 1)
    r = quota.adjust(kind, f"user:{member_id}", n)
    return {"ok": True, "result": r}


@router.post("/members/{member_id}/password")
async def reset_member_password(member_id: str, body: dict = Body(...),
                                _: dict = Depends(require_admin)) -> dict:
    """後台幫會員直接重設密碼（客服模式）。

    小羅 2026-09-29：「忘記密碼要真實有效」→ 在没有寄信設定前，
    客人忘記密碼時就由小羅在這裡直接設一組新密碼給他。
    不帶 password 就自動產生一組好講的（如 K7m4-Qx9p）。
    """
    from ..services import members

    if not members.get(member_id):
        raise HTTPException(status_code=404, detail="找不到這個會員")
    pw = (body.get("password") or "").strip()
    if not pw:
        import secrets as _s
        a = "".join(_s.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(4))
        b = "".join(_s.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(4))
        pw = f"{a}-{b}"
    elif len(pw) < 6:
        raise HTTPException(status_code=400, detail="密碼至少 6 個字")
    members.set_password(member_id, pw)
    return {"ok": True, "password": pw,
            "hint": "請把這組新密碼告給客人（他登入後可以自己在會員頁改）"}


@router.post("/members/{member_id}/nickname")
async def set_member_nickname(member_id: str, body: dict = Body(...),
                              _: dict = Depends(require_admin)) -> dict:
    """後台幫會員改／清喱稱（小羅 2026-09-29：會員資料要有喱稱）。"""
    from ..services import members

    m = members.set_nickname(member_id, str(body.get("nickname") or ""))
    if not m:
        raise HTTPException(status_code=404, detail="找不到這個會員")
    return {"ok": True, "member": m}


@router.get("/devices/{device_id}")
async def device_trace(device_id: str, _: dict = Depends(require_admin)) -> dict:
    return {"ok": True, "device_id": device_id, "trace": events.device_trace(device_id)}


# ── 收益報表 ──────────────────────────────────────────
@router.get("/revenue")
async def revenue(days: int = Query(30, ge=1, le=365), _: dict = Depends(require_admin)) -> dict:
    """收益報表（小羅 2026-09-30：改成對帳式）分平台＋合計、撥款、退款。"""
    from ..services import billing

    return {"ok": True, "summary": events.revenue_summary(days=days),
            "overview": billing.revenue_overview(days=days),
            "orders": events.orders()}


# ── 錯誤與告警 ────────────────────────────────────────
@router.get("/errors")
async def errors(days: int = Query(7, ge=1, le=365), _: dict = Depends(require_admin)) -> dict:
    return {
        "ok": True,
        "top_errors": events.top_errors(days=days),
        "platforms": events.by_platform(days=days),
        "transfer": events.transfer_stats(days=days),
    }


# ── 系統 ──────────────────────────────────────────────
@router.get("/system")
async def system(_: dict = Depends(require_admin)) -> dict:
    egress = None
    try:
        from ..core.http import HttpClient

        async with HttpClient(timeout=8) as http:
            egress = (await http.get_json("https://api.ipify.org?format=json")).get("ip")
    except Exception:  # noqa: BLE001
        egress = None

    return {
        "ok": True,
        "egress_ip": egress,
        "db_path": db.path(),
        "db_size": db.db_size_bytes(),
        "events": events.count_events(),
        "platforms_total": len(registry.all_platforms()),
        "notify_emails": events.notify_recipients(),
        "totp_enabled": security.totp_enabled(),
        "data_dir": settings.data_dir or "(預設 ./data)",
    }


@router.post("/system/account")
async def set_admin_account(body: dict = Body(...), _: dict = Depends(require_admin)) -> dict:
    """改後台帳號／密碼 —— 小羅 2026-09-29：「前後台也要有改帳密的按鈕和邏輯」。

    ⚠️ 安全：必須先輸入「目前的密碼」才能改。
    改完會換掉登入金鑰（舊登入全部失效）→ 請用新帳密重新登入。
    """
    cur = body.get("current") or ""
    user = (body.get("user") or "").strip()
    pw = body.get("password") or ""
    if not security.check_password(security.current_user(), cur):
        raise HTTPException(status_code=400, detail="目前的密碼不正確")
    if not user or len(pw) < 4:
        raise HTTPException(status_code=400, detail="帳號不能空白，密碼至少 4 個字")
    security.set_account(user, pw)
    return {"ok": True, "user": user, "relogin": True,
            "message": "帳密已更新，請用新帳密重新登入"}


@router.post("/system/notify")
async def set_notify(body: dict = Body(...), _: dict = Depends(require_admin)) -> dict:
    emails = [e.strip() for e in (body.get("emails") or []) if str(e).strip()]
    db.set_setting("notify_emails", emails)
    return {"ok": True, "notify_emails": emails}


@router.post("/system/totp")
async def totp(action: str = Query(..., pattern="^(enable|disable|info)$"),
                _: dict = Depends(require_admin)) -> dict:
    if action == "enable":
        secret = security.ensure_totp_secret()
        return {"ok": True, "enabled": True, "secret": secret,
                "otpauth": security.otpauth_url(), "current_code": security.totp_now()}
    if action == "disable":
        security.disable_totp()
        return {"ok": True, "enabled": False}
    return {"ok": True, "enabled": security.totp_enabled(),
            "otpauth": security.otpauth_url() if security.totp_enabled() else None}


# ── 資料管理（規格書 10-2-1：匯出 → 預覽 → 刪除）──────
@router.get("/data/preview")
async def data_preview(days: int = Query(180, ge=1), _: dict = Depends(require_admin)) -> dict:
    return {"ok": True, "older_than_days": days,
            "will_delete": events.preview_cleanup(days),
            "total": events.count_events()}


@router.get("/data/export")
async def data_export(_: dict = Depends(require_admin)) -> StreamingResponse:
    rows = events.export_rows()
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    buf.seek(0)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="ferry-events-{stamp}.csv"'},
    )


@router.post("/data/cleanup")
async def data_cleanup(body: dict = Body(default={}), _: dict = Depends(require_admin)) -> dict:
    if not body.get("confirm"):
        raise HTTPException(status_code=400, detail="需要二次確認")
    days = body.get("older_than_days")
    deleted = events.cleanup(older_than_days=int(days) if days else None)
    return {"ok": True, "deleted": deleted}


@router.post("/health/run")
async def health_run(_: dict = Depends(require_admin)) -> dict:
    out: list[dict[str, Any]] = []
    for r in registry.all_platforms():
        sample = None
        if r.hosts:
            sample = None
        item = {"platform": r.name, "label": r.label, "enabled": flags.platform_enabled(r.name)}
        try:
            h = await r.health(sample)
            item.update({"ok": h.get("ok"), "note": h.get("note")})
        except Exception as exc:  # noqa: BLE001
            item.update({"ok": False, "note": str(exc)[:120]})
        out.append(item)
    return {"ok": True, "results": out}


# ── 通知與監控（規格書 10-4-1）────────────────────────
@router.get("/notify")
async def get_notify(_: dict = Depends(require_admin)) -> dict:
    from ..services import monitor, notify as nt

    return {
        "ok": True,
        "events": nt.EVENTS,
        "toggles": nt._toggles(),
        "transport": nt.transport(),
        "recipients": nt._recipients(),
        "recent": nt.recent(40),
        "cooldown": {k: nt.cooldown_left(k) for k in nt.EVENTS},
        "monitor": monitor.last_state(),
        "process": monitor.process_info(),
    }


@router.put("/notify/toggles")
async def put_notify(body: dict = Body(...), _: dict = Depends(require_admin)) -> dict:
    from ..services import notify as nt

    for k, v in (body.get("toggles") or {}).items():
        nt.set_toggle(k, bool(v))
    return {"ok": True, "toggles": nt._toggles()}


@router.post("/notify/test")
async def notify_test(body: dict = Body(default={}), _: dict = Depends(require_admin)) -> dict:
    from ..services import notify as nt

    subject = body.get("subject") or "【轉運站】測試通知"
    text = body.get("text") or "這是一封測試信，代表通知管道設定正確。"
    return {"ok": True, **await nt.send_now(subject, text)}


@router.post("/notify/digest")
async def notify_digest(days: int = Query(1, ge=1, le=30), _: dict = Depends(require_admin)) -> dict:
    from ..services import notify as nt

    return {"ok": True, **await nt.send_digest(days)}


@router.post("/monitor/run")
async def monitor_run(_: dict = Depends(require_admin)) -> dict:
    from ..services import monitor

    return {"ok": True, "state": await monitor.check_once(notify_on_start=True)}


# ── 方案價格 ─────────────────────────────────────────
@router.get("/plans")
async def get_plans(_: dict = Depends(require_admin)) -> dict:
    from ..services import billing

    return {"ok": True, "plans": billing.plans(),
            "providers": billing.available_providers(),
            "enabled": billing.enabled()}


@router.put("/plans")
async def put_plans(body: dict = Body(...), _: dict = Depends(require_admin)) -> dict:
    from ..services import billing

    for plan, price in (body.get("prices") or {}).items():
        try:
            billing.set_price(plan, float(price))
        except (TypeError, ValueError):
            continue
    return {"ok": True, "plans": billing.plans()}


# ── 會員 ─────────────────────────────────────────────
@router.get("/growth")
async def growth(days: int = Query(90, ge=7, le=730), _: dict = Depends(require_admin)) -> dict:
    return {"ok": True, **events.growth(days)}


# ── 收款、撥款對帳、退款（小羅 2026-09-30 定案）──────────
#   ⚠️ 真實撥款在「金流商後台」操作；這裡只「登記已到帳」＋統計。
@router.get("/payout")
async def get_payout(_: dict = Depends(require_admin)) -> dict:
    """餘額、撥款紀錄、退款紀錄、各金流商設定狀態。"""
    from ..services import billing

    return {"ok": True, "summary": billing.payout_summary(),
            "payouts": billing.payouts(), "refunds": billing.refunds(),
            "providers": billing.provider_setup_guide(),
            "methods": billing.PAYOUT_METHODS,
            "refund_status": billing.REFUND_STATUS,
            "callbacks": {
                "webhook": "/api/billing/webhook/{provider}",
                "return": "/?paid=1",
            }}


@router.post("/payout/add")
async def add_payout(body: dict = Body(...), _: dict = Depends(require_admin)) -> dict:
    """登記一筆撥款（＝金流商已經匯到我的帳戶）。"""
    from ..services import billing

    row = billing.payout_add(
        provider=(body or {}).get("provider") or "",
        amount=float((body or {}).get("amount") or 0),
        fee=float((body or {}).get("fee") or 0),
        method=(body or {}).get("method") or "bank",
        account=(body or {}).get("account") or "",
        currency=(body or {}).get("currency") or "TWD",
        fx_rate=float((body or {}).get("fx_rate") or 0) or None,
        amount_twd=float((body or {}).get("amount_twd") or 0) or None,
        note=(body or {}).get("note") or "",
    )
    return {"ok": True, "row": row, "summary": billing.payout_summary(),
            "payouts": billing.payouts()}


@router.post("/payout/{pid}/status")
async def payout_status(pid: int, body: dict = Body(...),
                        _: dict = Depends(require_admin)) -> dict:
    from ..services import billing

    billing.payout_set_status(pid, (body or {}).get("status") or "done")
    return {"ok": True, "summary": billing.payout_summary(), "payouts": billing.payouts()}


@router.get("/order/lookup")
async def order_lookup(id: str = Query(...), _: dict = Depends(require_admin)) -> dict:
    """訂單核實：訂單 ↔ 會員 ↔ 金額（退款前交叉比對，避免退錯人）。"""
    from ..services import billing

    return {"ok": True, "order": billing.order_detail(id)}


@router.post("/refund/apply")
async def refund_apply(body: dict = Body(...), _: dict = Depends(require_admin)) -> dict:
    """登記一筆退款申請（客戶來信 / 後台代登）。"""
    from ..services import billing

    row = billing.refund_apply(
        order_id=(body or {}).get("order_id") or "",
        email=(body or {}).get("email") or "",
        amount=float((body or {}).get("amount") or 0) or None,
        reason=(body or {}).get("reason") or "",
        note=(body or {}).get("note") or "",
    )
    return {"ok": True, "row": row, "refunds": billing.refunds()}


@router.post("/refund/{rid}/status")
async def refund_status(rid: int, body: dict = Body(...),
                        _: dict = Depends(require_admin)) -> dict:
    """更新退款狀態（processing / done / rejected）。done 會把會員降回免費。"""
    from ..services import billing

    billing.refund_set_status(rid, (body or {}).get("status") or "processing",
                              (body or {}).get("note") or "")
    return {"ok": True, "refunds": billing.refunds()}


# ── 使用者回報（任何人都能送，不限會員）───────────────
@router.get("/notify-count")
async def notify_count(_: dict = Depends(require_admin)) -> dict:
    """輕量端點：後台要顯示的未讀/未處理數量（前端每 15 秒輪詢一次）。

    小羅 2026-09-28：「我不要刷新就可以看到訊息跳出來，
                      而且要像前台那樣有綠色圈圈顯示 1、2、3。」
    """
    from ..services import feedback

    c = feedback.counts(days=365)
    return {"ok": True, "feedback_new": c["new"], "feedback_total": c["total"]}


@router.get("/feedback")
async def get_feedback(days: int = Query(30), only_new: bool = Query(False),
                       _: dict = Depends(require_admin)) -> dict:
    from ..services import feedback

    rows = feedback.list_all(days=days, only_new=only_new)
    # 每筆回報附上「所有回覆紀錄」（小羅 2026-09-28：
    # 「至少也要讓我知道我自己回復了什麼，我回復了兩次就要看得到上次和這次發了什麼」）
    for r in rows:
        r["replies"] = feedback.replies_of(r["id"])
    return {"ok": True, "counts": feedback.counts(days), "rows": rows}


@router.delete("/feedback-test/{fid}")
async def delete_test_feedback(fid: int, _: dict = Depends(require_admin)) -> dict:
    """刪除「測試」回報（只允許內容含『測試』字樣的，避免誤刪真實客戶）。"""
    row = db.one("SELECT message, contact FROM feedback WHERE id=?", (fid,))
    if not row:
        return {"ok": True, "already": True}
    blob = ((row["message"] or "") + (row["contact"] or ""))
    if "測試" not in blob and "test" not in blob.lower():
        raise HTTPException(status_code=400, detail="這不是測試回報，不允許刪除")
    db.execute("DELETE FROM feedback WHERE id=?", (fid,))
    return {"ok": True, "deleted": fid}


@router.post("/feedback/{fid}/handled")
async def mark_feedback(fid: int, body: dict = Body(default={}),
                        _: dict = Depends(require_admin)) -> dict:
    from ..services import feedback

    feedback.mark_handled(fid, (body or {}).get("note") or "")
    return {"ok": True, "counts": feedback.counts()}


# ── 免費次數管理（可以手動還使用者一次）───────────────
@router.get("/quota")
async def get_quota_usage(_: dict = Depends(require_admin)) -> dict:
    from ..services import quota

    from ..core.config import settings

    return {"ok": True, "rows": quota.today_usage(),
            "limits": {"download": settings.free_download_per_day,
                       "transfer": settings.free_transfer_per_day},
            "enabled": settings.free_limit_enabled}


@router.post("/quota/grant")
async def grant_quota(body: dict = Body(...), _: dict = Depends(require_admin)) -> dict:
    """還使用者免費次數（例：解析失敗卻被扣了）。"""
    from ..services import quota

    subject = (body.get("subject") or "").strip()
    if not subject:
        raise HTTPException(status_code=400, detail="缺少 subject（例如 dev:xxxx）")
    kind = body.get("kind") or "download"
    n = int(body.get("n") or 1)
    row = quota.adjust(kind, subject, n)      # 可超過每日上限（贈送）
    return {"ok": True, "result": row, "rows": quota.today_usage()}


@router.post("/quota/reset")
async def reset_quota(body: dict = Body(default={}), _: dict = Depends(require_admin)) -> dict:
    from ..services import quota

    subject = (body or {}).get("subject") or None
    quota.reset_today(subject)
    return {"ok": True, "rows": quota.today_usage()}


# ── 測試帳號清理（審核工具用；避免測試資料污染）────────
@router.post("/members/cleanup-test")
async def cleanup_test_members(_: dict = Depends(require_admin)) -> dict:
    n = int(db.scalar("SELECT COUNT(*) FROM members WHERE email LIKE 'audit%@ferry.local'"))
    db.execute("DELETE FROM members WHERE email LIKE 'audit%@ferry.local'")
    return {"ok": True, "deleted": n}


@router.post("/feedback/{fid}/handle")
async def handle_feedback(fid: int, body: dict = Body(...),
                          _: dict = Depends(require_admin)) -> dict:
    """從「客戶回報」直接處理：加減次數 ＋ 回覆客戶 ＋ 標記完成。

    小羅 2026-09-27：「我點這個回報就能直接幫他加次數、回訊息給他，
                      能加也能減。」
    """
    from ..services import feedback as fb
    from ..services import members as mem
    from ..services import quota

    row = db.one("SELECT * FROM feedback WHERE id=?", (fid,))
    if not row:
        raise HTTPException(status_code=404, detail="找不到這則回報")
    f = dict(row)
    # 裝置 ID 可能長成 dev:xxx 或 dev_xxx 或純 IP；by_device 只認原本存的值
    did = f.get("device_id") or ""
    dev = did
    m = mem.by_device(dev) or {}
    if not m.get("id"):
        # 再試去掉 dev: 前綴的版本
        for cand in (did.replace("dev:", ""), did[len("dev"):] if did.startswith("dev") else ""):
            if cand:
                m = mem.by_device(cand) or {}
                if m.get("id"):
                    dev = cand
                    break
    mid = m.get("id")
    # ⚠️ 裝置 ID 本身已經是 dev:xxx / ip:xxx 格式（auth._device_id 就加了前綴），
    #    不要再補一次 dev: 否則會變成 dev:dev:xxx → 加到錯的地方，前台看不到。
    if mid:
        subject = f"user:{mid}"
    elif dev:
        subject = dev if dev.startswith(("dev:", "ip:")) else f"dev:{dev}"
    else:
        subject = ""

    dl = int(body.get("download") or 0)
    tr = int(body.get("transfer") or 0)
    days = int(body.get("days") or 0)
    actions: list[str] = []
    if subject and dl:
        quota.adjust("download", subject, dl)
        actions.append(("下載次數 +" if dl > 0 else "下載次數 ") + str(dl))
    if subject and tr:
        quota.adjust("transfer", subject, tr)
        actions.append(("傳輸次數 +" if tr > 0 else "傳輸次數 ") + str(tr))
    if mid and days:
        mem.extend_days(mid, abs(days), reason="gift",
                        note=f"客服從回報 #{fid} 補償")
        actions.append(f"加 {abs(days)} 天")

    msg = (body.get("reply") or "").strip()
    if not msg:
        # 沒填回覆 → 自動產生（小羅：「我沒有什麼特別的話要說，用預設的就好」）
        if actions:
            msg = ("你的問題已經幫你處理完成，已協助你 " + "、".join(actions)
                   + "，請再試一次。若還有問題歡迎再回報，謝謝你！")
        else:
            msg = "你的問題我們已經看過並處理，請再試一次。若還有問題歡迎再回報，謝謝你！"

    # 小羅 2026-09-28：有補償（加/減次數）時，多提醒一句去刷新看次數
    if actions and "重新刷新" not in msg:
        msg = msg.rstrip() + chr(10) + chr(10) + TIP_REFRESH
    out = fb.reply(fid, msg, action="、".join(actions),
                   mark_handled=bool(body.get("handled", True)))
    return {"ok": True, "actions": actions, "row": out,
            "subject": subject, "member": mid or "", "email": m.get("email") or ""}

