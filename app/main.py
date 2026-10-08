"""FastAPI 進入點（只組裝，不寫業務邏輯）。

（規格書第 21-2 章：main.py 只做組裝）
"""
from __future__ import annotations

import asyncio
import json
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
from .core import fail_reason
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
    # 第一次啟動把預設廣告碼「播種」進後台（小羅 2026-09-30：後台那個框要真的看得到碼；
    # 之後完全以後台為準 —— 他把框清空就是「沒有廣告」）。
    try:
        from .services import ads as _ads

        _ads.ensure_seeded()
    except Exception:  # noqa: BLE001
        pass
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
    if "ferry" in low or "v8i8.com" in low or "scefo.com" in low:
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


@app.post("/api/line/webhook")
async def line_webhook(request: Request):
    """LINE Webhook：抓「加好友／傳訊息」的來源 userId → 存成 line.user_id（只發小羅用）。

    驗簽用 Channel secret（line.secret）；沒設 secret 則不驗（仍可收，但會提醒）。
    """
    import base64
    import hashlib
    import hmac

    body = await request.body()
    secret = (db.get_setting("line.secret") or os.getenv("LINE_CHANNEL_SECRET") or "").strip()
    if secret:
        want = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(want, request.headers.get("X-Line-Signature", "")):
            return JSONResponse({"ok": False, "reason": "bad signature"}, status_code=400)
    try:
        data = json.loads(body or b"{}")
    except Exception:  # noqa: BLE001
        data = {}
    got = []
    for ev in (data.get("events") or []):
        uid = ((ev.get("source") or {}).get("userId") or "").strip()
        if uid:
            db.set_setting("line.user_id", uid)
            got.append(uid)
    return {"ok": True, "saved": len(got)}


@app.post("/api/monitor/external")
async def monitor_external(request: Request):
    """外部監測（GitHub Actions）回報一筆通知 → 只記錄在後台（不再重發 LINE）。

    需帶 X-Monitor-Key（後台設定的 monitor.key）；金鑰未設 = 不開放。
    """
    from .services import notify

    key = (db.get_setting("monitor.key") or "").strip()
    if not key:
        return JSONResponse({"ok": False, "reason": "未開放"}, status_code=404)
    if (request.headers.get("x-monitor-key") or "").strip() != key:
        return JSONResponse({"ok": False, "reason": "金鑰錯誤"}, status_code=403)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = {}
    title = str(data.get("title") or "外部監測告警")[:120]
    body = str(data.get("body") or "")[:1000]
    ok = bool(data.get("ok", False))
    notify.log_external(title, body, ok=ok, note=str(data.get("note") or "")[:80])
    return {"ok": True}


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
    """前端啟動時讀這個：功能開關、平台開關、次數規則、目前上線的廣告商、關於我們。"""
    from .services import about as _about
    from .services import ads as _ads

    _net = _ads.network_of()
    return {"ok": True, "ads_network": _net, "ads_network_label": _ads.network_label(_net),
            "ad_codes": _ads.all_slots(),          # 後台「廣告」頁兩個位置貼的碼（空＝前台不顯示）
            "about": _about.get(),
            **flags.snapshot()}


@app.get("/api/debug/youtube")
async def debug_youtube(v: str = Query("mw-kKYRSEOU")):
    """診斷：從本伺服器的 IP 逐一試 YouTube 的各個 player_client。

    ⚠️ YouTube 會依「IP 信譽」決定要不要給資料（雲端 IP 常被判機器人）。
    本機測試沒用（住宅 IP 都會過），一定要從部署環境測。
    """
    import yt_dlp

    # (標籤, player_client, player_skip, 額外參數)
    combos = [
        ("all", "all", None, None),
        ("web_embedded", "web_embedded", None, None),
        ("tv_embedded", "tv_embedded", None, None),
        # 跳過網頁請求，只打 Google 的內部 API（繞過網頁層的機器人檢查）
        ("all+skip_webpage", "all", "webpage", None),
        ("all+skip_webpage_configs", "all", "webpage,configs", None),
        ("android+skip_webpage", "android", "webpage", None),
        ("ios+skip_webpage", "ios", "webpage", None),
        ("tv_embedded+skip", "tv_embedded", "webpage,configs", None),
        ("web_embedded+skip", "web_embedded", "webpage,configs", None),
        # 試帶 visitor data / 不同 player 路徑
        ("all+skip_js", "all", "webpage,js", None),
        ("mweb+skip", "mweb", "webpage,configs", None),
        ("android_producer", "android_producer", "webpage", None),
    ]
    out = []

    def probe(label: str, client: str, skip: str | None, _extra) -> dict:
        ea: dict = {"player_client": [client]}
        if skip:
            ea["player_skip"] = skip.split(",")
        opts = {
            "quiet": True, "no_warnings": True, "skip_download": True, "cachedir": False,
            "socket_timeout": 20,
            "extractor_args": {"youtube": ea},
            "js_runtimes": {"deno": {}},
            "http_headers": {"User-Agent": _YT_UA},
        }
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(f"https://www.youtube.com/watch?v={v}", download=False)
            hs = sorted({f.get("height") for f in (info.get("formats") or []) if f.get("height")},
                        reverse=True)
            return {"client": label, "ok": True, "formats": len(info.get("formats") or []),
                    "heights": hs[:5]}
        except Exception as exc:  # noqa: BLE001
            e = str(exc)
            return {"client": label, "ok": False,
                    "error": "需要登入/機器人判定" if ("Sign in" in e or "bot" in e) else e[:100]}

    for label, c, sk, ex in combos:
        out.append(await asyncio.to_thread(probe, label, c, sk, ex))

    # 把容器裡的版本一起回報（Deno 版本不對會讓 yt-dlp 解不了 YouTube 的 JS 驗證）
    import shutil
    import subprocess

    def ver(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()[:60]
        except Exception:  # noqa: BLE001
            return "（找不到）"

    return {"ok": True, "video": v,
            "deno": ver([shutil.which("deno") or "deno", "--version"]) or "（找不到）",
            "yt_dlp": yt_dlp.version.__version__,
            "deno_path": shutil.which("deno") or "（不在 PATH）",
            "results": out}


@app.get("/api/debug/douyin")
async def debug_douyin(u: str = Query(""), rid: str = Query("7162250624946425122")):
    """診斷抖音各條路線（小羅 2026-09-28：v8i8 能解析但我們不行）。"""
    from .platforms.douyin import DouyinResolver

    url = u or f"https://www.douyin.com/video/{rid}"
    r = DouyinResolver()
    out = {"url": url, "routes": []}

    def note(name: str, ok: bool, info=None, err: str = "") -> None:
        row = {"route": name, "ok": ok}
        if info is not None:
            row["title"] = (info.title or "")[:40]
            row["formats"] = len(info.formats)
        if err:
            row["error"] = err[:160]
        out["routes"].append(row)

    for name, fn in (("official", r._via_official), ("api_watch", r._via_api_watch),
                     ("browser", r._via_browser), ("tikwm", r._via_tikwm)):
        try:
            info = await asyncio.wait_for(fn(url), timeout=45)
            note(name, bool(info), info, "" if info else "回 None")
        except asyncio.TimeoutError:
            note(name, False, None, "逾時 45s")
        except Exception as exc:  # noqa: BLE001
            note(name, False, None, f"{type(exc).__name__}: {exc}")

    # 完整的 url_list（找出「無水印」的那一個）
    try:
        from .platforms.douyin import DouyinResolver as _DR
        from .platforms import _douyin_shared as _sh

        _r = _DR()
        _aid = await _r._aweme_id(url)
        out["aweme_id"] = _aid
        _detail = await _sh.fetch_detail(_aid) if _aid else None
        if _detail:
            _v = _detail.get("video") or {}
            out["url_lists"] = {
                "play_addr": ((_v.get("play_addr") or {}).get("url_list") or [])[:6],
                "download_addr": ((_v.get("download_addr") or {}).get("url_list") or [])[:6],
                "bit_rate": [
                    {"h": b.get("height"), "gear": b.get("gear_name"),
                     "urls": ((b.get("play_addr") or {}).get("url_list") or [])[:4]}
                    for b in (_v.get("bit_rate") or [])[:4]
                ],
            }
    except Exception as _e:  # noqa: BLE001
        out["url_lists_error"] = str(_e)[:120]

    # 真瀏覽器實際看到什麼（有助判斷是不是被風控）
    try:
        from .services.browser import get_context

        ctx = await get_context("douyin")
        page = await ctx.new_page()
        seen: list[str] = []

        async def on_resp(resp) -> None:
            if "aweme" in resp.url or "detail" in resp.url:
                seen.append(f"{resp.status} {resp.url[:90]}")

        page.on("response", on_resp)
        try:
            await page.goto(url, wait_until="commit", timeout=20000)
            await asyncio.sleep(8)
        except Exception:  # noqa: BLE001
            pass
        out["browser_title"] = (await page.title())[:80]
        out["browser_aweme_calls"] = seen[:6]
        html = await page.content()
        out["browser_html_len"] = len(html)
        # 看 HTML 裡到底有哪些關鍵字（判斷資料在哪）
        for key in ("bitRateList", "playAddr", "play_addr", "RENDER_DATA",
                    "_ROUTER_DATA", "awemeId", "aweme_id", "videoResource",
                    "url_list", "douyinvod", "zjcdn"):
            out.setdefault("html_keys", {})[key] = html.count(key)
        # 抓幾個疑似 CDN 網址
        import re as _re
        urls = _re.findall(r"https?://[^\"'\s]*(?:douyinvod|zjcdn|aweme\.snssdk|byteimg\.com)[^\"'\s]*",
                           html)
        out["cdn_like"] = [u[:110] for u in urls[:5]]
        await page.close()
    except Exception as exc:  # noqa: BLE001
        out["browser_error"] = str(exc)[:120]
    return out


@app.get("/api/announcements")
async def get_announcements():
    """前台公告（系統更新／平台故障／平台取消…）。

    ⚠️ 公告是前台看得到的；Email 是另外「手動」發的（不要每次改東西就寄一次）。
    """
    from .services import announce

    return {"ok": True, "items": announce.active()}


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
    # 小羅 2026-10-05：聯動測試（tools/check_linkage.py）會打這支 API，
    # 若照算會污染「解析失敗率」告警 → 標記成 test，統計時排除。
    if request.headers.get("x-linkage-test"):
        device = "linkage-test"
    tz = tz_util.from_request(request)           # ← 依「裝置所在位置」的當地時間
    info_dict: dict = {}
    try:
        info = await resolve_service.resolve(body.url)
        info_dict = info.to_dict()
    except AppError as exc:
        # 依「失敗原因」給客人對應的白話訊息（小羅 2026-10-06）
        # 例：未公開／有觀看限制／付費／地區／版權／已刪除／沒有影片／直播／暫時忙碌
        situation = fail_reason.classify(getattr(exc, "platform", None), exc.code,
                                         getattr(exc, "message", ""))
        if situation != exc.code:
            exc.code = situation            # 前端用 err_<情況> 顯示白話訊息
        events.track("resolve", device_id=device, platform=getattr(exc, "platform", None) or "",
                     result="fail", error_code=situation, url=body.url,
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
    st = quota.status(subject, tz_name=tz)

    # 小羅 2026-09-29：前台要依「訪客／免費／月／永久」顯示不同文字，
    # 所以這裡一併回傳等級、到期日與廣告狀態（廣告開關預設關閉 → 不會出現）
    from .services import ads as ads_service
    from .services import members as members_service

    mid = auth.current_member_id(request)
    tier = members_service.tier_of(mid)
    m = members_service.get(mid) if mid else None
    used = quota.used_total(subject, tz_name=tz)
    ads = ads_service.state(tier, used)
    # ⚠️ 前台是拿「內層 quota」畫畫面 → 等級／到期／廣告要一起放進內層，
    #    否則前台讀不到（實測踩到：月會員仍顯示今日剩餘次數）
    st.update({"tier": tier, "expires_at": (m or {}).get("expires_at"), "ads": ads})
    return {"ok": True, "timezone": tz, "quota": st, "tier": tier,
            "expires_at": (m or {}).get("expires_at"), "ads": ads}


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
