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
            "user": settings.admin_user}


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
@router.get("/devices")
async def devices(days: int = Query(30, ge=1, le=365), _: dict = Depends(require_admin)) -> dict:
    return {"ok": True, "devices": events.list_devices(days=days)}


@router.get("/devices/{device_id}")
async def device_trace(device_id: str, _: dict = Depends(require_admin)) -> dict:
    return {"ok": True, "device_id": device_id, "trace": events.device_trace(device_id)}


# ── 收益報表 ──────────────────────────────────────────
@router.get("/revenue")
async def revenue(days: int = Query(30, ge=1, le=365), _: dict = Depends(require_admin)) -> dict:
    return {"ok": True, "summary": events.revenue_summary(days=days),
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
    from ..services import cookies

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
        "cookies": cookies.available(),
        "notify_emails": events.notify_recipients(),
        "totp_enabled": security.totp_enabled(),
        "data_dir": settings.data_dir or "(預設 ./data)",
    }


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


@router.post("/quota/reset")
async def quota_reset(_: dict = Depends(require_admin)) -> dict:
    from ..services import quota

    quota.reset_all()
    return {"ok": True}


# ── 健康檢查（每平台實測一輪）─────────────────────────
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
@router.get("/members")
async def get_members(_: dict = Depends(require_admin)) -> dict:
    from ..services import members

    return {"ok": True, "members": members.list_members()}


# ── 成長趨勢 ─────────────────────────────────────────
@router.get("/growth")
async def growth(days: int = Query(90, ge=7, le=730), _: dict = Depends(require_admin)) -> dict:
    return {"ok": True, **events.growth(days)}


# ── Cookies 管理（IG／FB／西瓜／頭條 需要）──────────────
@router.get("/cookies")
async def get_cookies(_: dict = Depends(require_admin)) -> dict:
    from ..services import cookies

    return {"ok": True, "dir": cookies.target_dir(), "platforms": cookies.overview()}


@router.post("/cookies/{platform}")
async def post_cookies(platform: str, body: dict = Body(...), _: dict = Depends(require_admin)) -> dict:
    from ..services import cookies

    try:
        info = cookies.save(platform, body.get("content") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "saved": info, "platforms": cookies.overview()}


@router.delete("/cookies/{platform}")
async def delete_cookies(platform: str, _: dict = Depends(require_admin)) -> dict:
    from ..services import cookies

    ok = cookies.remove(platform)
    return {"ok": True, "removed": ok, "platforms": cookies.overview()}


# ── 測試帳號清理（審核工具用；避免測試資料污染）────────
@router.post("/members/cleanup-test")
async def cleanup_test_members(_: dict = Depends(require_admin)) -> dict:
    n = int(db.scalar("SELECT COUNT(*) FROM members WHERE email LIKE 'audit%@ferry.local'"))
    db.execute("DELETE FROM members WHERE email LIKE 'audit%@ferry.local'")
    return {"ok": True, "deleted": n}
