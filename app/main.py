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
from .services import auth, downloader, events, flags, monitor, proxy, quota, resolve_service
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

#: App 內建瀏覽器 → 這就是「使用者從哪裡點進來」最準的線索
_UA_APP = (
    ("MicroMessenger", "微信"), ("Weixin", "微信"),
    ("Line/", "LINE"), ("LIFF", "LINE"),
    ("FBAN", "Facebook App"), ("FBAV", "Facebook App"), ("FB_IAB", "Facebook App"),
    ("Instagram", "Instagram App"),
    ("Twitter", "X App"), ("KAKAOTALK", "KakaoTalk"),
    ("Telegram", "Telegram"), ("SnapChat", "Snapchat"),
    ("DingTalk", "釘釘"), ("QQ/", "QQ"), ("Weibo", "微博 App"),
    ("TikTok", "TikTok App"), ("BiliApp", "B站 App"),
)

#: 來源網域 → 顯示名稱（讓後台一眼看得懂）
_REF_SOURCE = (
    ("google.", "Google 搜尋"), ("bing.", "Bing"), ("yahoo.", "Yahoo"),
    ("duckduckgo.", "DuckDuckGo"), ("baidu.", "百度"),
    ("douyin.com", "抖音"), ("tiktok.com", "TikTok"),
    ("bilibili.com", "B站"), ("xiaohongshu.com", "小紅書"),
    ("weibo.com", "微博"), ("zhihu.com", "知乎"), ("toutiao.com", "今日頭條"),
    ("facebook.com", "Facebook"), ("instagram.com", "Instagram"),
    ("x.com", "X"), ("twitter.com", "X"), ("threads.", "Threads"),
    ("youtube.com", "YouTube"), ("line.me", "LINE"), ("telegram.", "Telegram"),
    ("shopee.", "蝦皮"), ("pinterest.", "Pinterest"), ("reddit.", "Reddit"),
)


def source_of(request: Request) -> str | None:
    """判斷「這個訪客從哪裡來的」。

    優先序：
      1. App 內建瀏覽器（微信／LINE／FB／IG…）← 最準，因為就醫時 referrer 常是空的
      2. referrer 的網域（Google／抖音／小紅書…）
      3. 有 referrer 但認不出 → 原網域
    """
    ua = request.headers.get("user-agent", "") or ""
    for key, name in _UA_APP:
        if re.search(re.escape(key), ua, re.I):
            return name

    ref = (request.headers.get("referer") or "").strip()
    if not ref:
        return None                                  # 後台顯示為「(直接進入)」
    low = ref.lower()
    if "ferry" in low or "v8i8.com" in low:
        return None                                  # 站內跳轉不算來源
    for key, name in _REF_SOURCE:
        if key in low:
            return name
    m = re.match(r"https?://([^/]+)", ref)
    return m.group(1) if m else None


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
                              source=source_of(request))
        if request.method == "GET" and path in ("/", "/index.html", "/admin", "/admin/"):
            info = _client_info(request)
            src = source_of(request)
            is_new = events.touch_device(
                info["device_id"], country=info["country"],
                os_name=info["os_name"], browser=info["browser"], source=src,
            )
            events.track(
                "page_view", device_id=info["device_id"], path=path,
                country=info["country"], os_name=info["os_name"],
                browser=info["browser"], is_new=is_new,
                referrer=(src or (request.headers.get("referer") or ""))[:300] or None,
                utm=(request.url.query or "")[:200] or None,
            )
    except Exception:  # noqa: BLE001 — 記錄失敗不影響回應
        pass
    return response


# ── 請求模型 ────────────────────────────────────────
class ResolveIn(BaseModel):
    url: str


# ── API ─────────────────────────────────────────────
_YT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36")


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


@app.get("/api/debug/youtube")
async def debug_youtube(v: str = Query("mw-kKYRSEOU")):
    """診斷：從本伺服器的 IP 逐一試 YouTube 的各個 player_client。

    ⚠️ YouTube 會依「IP 信譽」決定要不要給資料（雲端 IP 常被判機器人）。
    本機測試沒用（住宅 IP 都會過），一定要從部署環境測。
    """
    import yt_dlp

    clients = ["all", "web_embedded", "tv_embedded", "android_vr", "web_safari",
               "mweb", "tv", "ios", "android"]
    out = []

    def probe(client: str) -> dict:
        opts = {
            "quiet": True, "no_warnings": True, "skip_download": True, "cachedir": False,
            "socket_timeout": 15, "retries": 0, "extractor_retries": 0,
            "extractor_args": {"youtube": {"player_client": [client]}},
            "js_runtimes": {"deno": {}},
            "http_headers": {"User-Agent": _YT_UA},
        }
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(f"https://www.youtube.com/watch?v={v}", download=False)
            hs = sorted({f.get("height") for f in (info.get("formats") or []) if f.get("height")},
                        reverse=True)
            return {"client": client, "ok": True, "formats": len(info.get("formats") or []),
                    "heights": hs[:5]}
        except Exception as exc:  # noqa: BLE001
            return {"client": client, "ok": False, "error": str(exc)[:120]}

    for c in clients:
        out.append(await asyncio.to_thread(probe, c))
    return {"ok": True, "video": v, "results": out}


@app.get("/api/platforms")
async def get_platforms():
    return {"ok": True, "platforms": await resolve_service.supported_platforms()}


@app.post("/api/resolve")
async def post_resolve(body: ResolveIn, request: Request):
    if flags.feature_enabled("feature.maintenance"):
        return JSONResponse(status_code=503, content={
            "ok": False, "code": "MAINTENANCE", "message": "系統維護中，請稍後再試"})
    if not flags.feature_enabled("feature.download"):
        return JSONResponse(status_code=403, content={
            "ok": False, "code": "FEATURE_DISABLED", "message": "無水印下載目前關閉中"})

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


@app.get("/api/proxy-video")
async def proxy_video(request: Request, k: str = Query(..., description="解析時取得的轉發鍵")):
    """把平台 CDN 的影片轉發給瀏覽器（支援 Range，可續傳／可拖曳）。

    為什麼要繞這一圈：平台 CDN 會檢查 Referer，瀏覽器送的是本網站網址 → 403。
    伺服器補上正確 Referer 就通了（B站等 DASH 平台還會順便合併影音軌）。

    ⚠️ 不是開放代理：k 只能來自剛解析成功的結果（30 分失效）。
    """
    row = proxy.lookup(k)
    if not row:
        raise HTTPException(status_code=404, detail="網址已失效，請重新解析")
    video, audio, headers = row
    events.track("download", device_id=auth._device_id(request),
                 platform=await _platform_of(video), result="ok", url=video, mode="relay")
    return await proxy.relay(video, audio, headers, request.headers.get("range"))


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


# ── 防快取：程式碼與頁面一律「重新驗證」，避免使用者卡在舊版 ──
_NO_CACHE_EXT = (".html", ".js", ".css", ".json", ".webmanifest")


@app.middleware("http")
async def _cache_control(request: Request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path
        if path in ("/", "/admin", "/admin/") or path.endswith(_NO_CACHE_EXT):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif path.startswith(("/logos/", "/favicon", "/icon")):
            response.headers["Cache-Control"] = "public, max-age=604800"
    except Exception:  # noqa: BLE001
        pass
    return response
