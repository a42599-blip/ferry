"""FastAPI 進入點（只組裝，不寫業務邏輯）。

（規格書第 21-2 章：main.py 只做組裝）
"""
from __future__ import annotations

import asyncio
import os
import re

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from .admin.routes import router as admin_router
from .api_member import router as member_router
from .core import db
from .core.errors import AppError
from .core.http import HttpClient
from .core import timezone as tz_util
from .services import auth, downloader, events, flags, monitor, notify, quota, resolve_service
from .services.transfer import router as transfer_router

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="ferry · 轉運站", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 前端與 API 同源部署；開發期放寬
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    db.connect()                  # 建表（第一次啟動）
    # 背景監控（健康檢查、每日摘要）
    try:
        asyncio.create_task(monitor.loop())
    except Exception:  # noqa: BLE001
        pass


# ── 例外處理（統一格式）─────────────────────────────
@app.exception_handler(AppError)
async def _app_error_handler(_req: Request, exc: AppError):
    return JSONResponse(status_code=exc.http_status, content={"ok": False, **exc.to_dict()})


# ── 事件記錄（規格書 10-3）──────────────────────────
_UA_OS = (
    ("Windows", "Windows"), ("Android", "Android"), ("iPhone|iPad|iPod", "iOS"),
    ("HarmonyOS|OpenHarmony", "HarmonyOS"), ("Macintosh|Mac OS X", "macOS"),
    ("Linux", "Linux"),
)
_UA_BROWSER = (
    ("Edg/", "Edge"), ("MicroMessenger", "WeChat"), ("OPR/", "Opera"),
    ("Chrome/", "Chrome"), ("CriOS", "Chrome"), ("Firefox/", "Firefox"),
    ("Safari/", "Safari"),
)


def _client_info(request: Request) -> dict:
    ua = request.headers.get("user-agent", "") or ""
    os_name = next((v for k, v in _UA_OS if re.search(k, ua, re.I)), None)
    browser = next((v for k, v in _UA_BROWSER if re.search(k, ua, re.I)), None)
    return {
        "device_id": auth.current_subject(request),
        "country": request.headers.get("cf-ipcountry"),
        "os_name": os_name,
        "browser": browser,
    }


@app.middleware("http")
async def _track(request: Request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path
        # 任何請求都補上裝置環境（國家／系統／瀏覽器）→ 後台資料才完整
        if not path.startswith(("/admin/api", "/api/signal", "/api/health")):
            info0 = _client_info(request)
            events.touch_meta(info0["device_id"], country=info0["country"],
                              os_name=info0["os_name"], browser=info0["browser"],
                              source=(request.headers.get("referer") or "")[:300] or None)
        if request.method == "GET" and path in ("/", "/index.html", "/admin", "/admin/"):
            info = _client_info(request)
            is_new = events.touch_device(
                info["device_id"], country=info["country"],
                os_name=info["os_name"], browser=info["browser"],
                source=request.headers.get("referer"),
            )
            events.track(
                "page_view", device_id=info["device_id"], path=path,
                country=info["country"], os_name=info["os_name"],
                browser=info["browser"], is_new=is_new,
                referrer=(request.headers.get("referer") or "")[:300] or None,
                utm=(request.url.query or "")[:200] or None,
            )
    except Exception:  # noqa: BLE001 — 記錄失敗不影響回應
        pass
    return response


# ── 請求模型 ────────────────────────────────────────
class ResolveIn(BaseModel):
    url: str


# ── API ─────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"ok": True, "service": "ferry"}


@app.get("/api/debug/ip")
async def debug_ip():
    """出口 IP（維運用）：判斷平台是不是因為 IP 而被擋。"""
    try:
        async with HttpClient() as http:
            data = await http.get_json("https://api.ipify.org?format=json")
        return {"ok": True, "egress_ip": data.get("ip")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)[:120]}


@app.get("/api/config")
async def get_config():
    """前端啟動時讀這個：功能開關、平台開關、次數規則。"""
    return {"ok": True, **flags.snapshot()}


@app.get("/api/platforms")
async def get_platforms():
    return {"ok": True, "platforms": await resolve_service.supported_platforms()}


@app.post("/api/resolve")
async def post_resolve(body: ResolveIn, request: Request):
    if flags.feature_enabled("feature.maintenance"):
        return JSONResponse(status_code=503, content={
            "ok": False, "code": "MAINTENANCE", "message": "系統維護中，請稍後再試"})

    subject = auth.current_subject(request)      # 額度用（會員／裝置）
    device = auth._device_id(request)            # 事件用（永遠是裝置，軌跡才不會斷）
    tz = tz_util.from_request(request)           # ← 依「裝置所在位置」的當地時間
    info_dict: dict = {}
    try:
        info = await resolve_service.resolve(body.url)
        info_dict = info.to_dict()
    except AppError as exc:
        events.track("resolve", device_id=device, platform=getattr(exc, "platform", None) or "",
                     result="fail", error_code=exc.code, url=body.url,
                     country=request.headers.get("cf-ipcountry"))
        raise

    events.track("resolve", device_id=device, platform=info.platform, result="ok",
                 latency_ms=info.extra.get("elapsed_ms"), url=body.url,
                 country=request.headers.get("cf-ipcountry"))

    if not flags.feature_enabled("feature.download"):
        return {"ok": True, "data": info_dict,
                "quota": quota.status(subject, tz_name=tz), "download_disabled": True}

    # 畫質選擇關閉 → 只給最高畫質
    if not flags.feature_enabled("feature.quality") and info_dict.get("formats"):
        best = max(info_dict["formats"], key=lambda f: f.get("quality_score") or 0)
        info_dict["formats"] = [best]
    if not flags.feature_enabled("feature.audio_only"):
        info_dict["formats"] = [f for f in info_dict["formats"] if not f.get("audio")] \
            or info_dict["formats"]

    used = quota.consume("download", subject, tz_name=tz)
    return {"ok": True, "data": info_dict, "quota": used}


@app.get("/api/quota")
async def get_quota(request: Request):
    subject = auth.current_subject(request)
    tz = tz_util.from_request(request)
    return {"ok": True, "timezone": tz, "quota": quota.status(subject, tz_name=tz)}


# ── 伺服器代理下載（CDN 擋 Origin 的平台，例如 YouTube）──
# 原則：串流不落地（下載到暫存 → 送出 → 立刻刪除，不保存任何檔案）
# 安全：只接受「已註冊平台認得的網址」，避免變成開放代理。
@app.get("/api/download")
async def proxied_download(
    request: Request,
    src: str = Query(..., description="原始影片網址"),
    h: int | None = Query(None, description="畫質高度，例如 1080"),
    audio: bool = Query(False, description="只要音訊"),
    name: str | None = Query(None, description="建議檔名"),
):
    path, suggested = await downloader.fetch_to_temp(src, height=h, audio=audio)
    filename = _safe_name(name or suggested)
    ext = filename.rsplit(".", 1)[-1].lower()
    media_type = "video/mp4"
    if audio or ext in ("m4a", "mp3"):
        media_type = "audio/mpeg" if ext == "mp3" else "audio/mp4"
    elif ext in ("jpg", "jpeg"):
        media_type = "image/jpeg"
    elif ext == "png":
        media_type = "image/png"

    size = os.path.getsize(path)
    events.track("download", device_id=auth._device_id(request),
                 platform=(await _platform_of(src)), result="ok", size=size,
                 url=src,
                 quality=str(h or ("audio" if audio else "")), mode="proxy")

    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        background=BackgroundTask(downloader.cleanup, path),
    )


async def _platform_of(src: str) -> str:
    from .core import registry

    try:
        r = await registry.detect(src)
        return r.name if r else ""
    except Exception:  # noqa: BLE001
        return ""


def _safe_name(name: str) -> str:
    """擋掉路徑穿越與非法字元。"""
    bad = '\\/:*?"<>|\r\n\t'
    for ch in bad:
        name = name.replace(ch, "_")
    name = name.strip().strip(".")
    return (name or "video.mp4")[:120]


# ── 無損傳輸（signaling 名片交換，極小）─────────────
app.include_router(transfer_router, prefix="/api/signal", tags=["transfer"])

# ── 後台 ────────────────────────────────────────────
app.include_router(admin_router)

# ── 會員與付款 ──────────────────────────────────────
app.include_router(member_router)


# ── 前端（靜態檔）────────────────────────────────────
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
