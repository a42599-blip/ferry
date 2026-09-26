"""FastAPI 進入點（只組裝，不寫業務邏輯）。

（規格書第 21-2 章：main.py 只做組裝）
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .core.errors import AppError
from .core import timezone as tz_util
from .services import auth, flags, quota, resolve_service
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


# ── 例外處理（統一格式）─────────────────────────────
@app.exception_handler(AppError)
async def _app_error_handler(_req: Request, exc: AppError):
    return JSONResponse(status_code=exc.http_status, content={"ok": False, **exc.to_dict()})


# ── 請求模型 ────────────────────────────────────────
class ResolveIn(BaseModel):
    url: str


# ── API ─────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"ok": True, "service": "ferry"}


@app.get("/api/config")
async def get_config():
    """前端啟動時讀這個：功能開關、平台開關、次數規則。"""
    return {"ok": True, **flags.snapshot()}


@app.get("/api/platforms")
async def get_platforms():
    return {"ok": True, "platforms": await resolve_service.supported_platforms()}


@app.post("/api/resolve")
async def post_resolve(body: ResolveIn, request: Request):
    subject = auth.current_subject(request)
    tz = tz_util.from_request(request)          # ← 依「裝置所在位置」的當地時間
    info = await resolve_service.resolve(body.url)
    used = quota.consume("download", subject, tz_name=tz)
    return {"ok": True, "data": info.to_dict(), "quota": used}


@app.get("/api/quota")
async def get_quota(request: Request):
    subject = auth.current_subject(request)
    tz = tz_util.from_request(request)
    return {"ok": True, "timezone": tz, "quota": quota.status(subject, tz_name=tz)}


# ── 無損傳輸（signaling 名片交換，極小）─────────────
app.include_router(transfer_router, prefix="/api/signal", tags=["transfer"])


# ── 前端（靜態檔）────────────────────────────────────
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
