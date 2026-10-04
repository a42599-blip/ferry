"""即時監控（後台「錯誤與告警」用）—— 全部中文。

小羅 2026-10-04：「後台要能抓到即時的伺服器狀態：記憶體、硬碟容量、
為什麼故障……全部幫我中文化，可以寫英文錯誤碼但一定要有中文。」

資料來源：
  · Railway 官方狀態  https://status.railway.com/api/status（公開，不需金鑰）
  · 本機系統          /proc、shutil.disk_usage
  · 資料庫            SQLite 反應時間（儲存變慢時會直接變長 → 一眼看出）

⚠️ 這個模組只「讀取」狀態，不會改任何東西，也不會動資料。
"""
from __future__ import annotations

import os
import re
import shutil
import time
from typing import Any

from ..core import db
from ..core.config import settings

# 本站機房（Railway）。可用環境變數 RAILWAY_REGION 覆寫。
OUR_REGION = (os.getenv("RAILWAY_REGION") or "us-west2").strip()

_STATUS_URL = "https://status.railway.com/api/status"
_STATUS_TTL = 60.0                     # Railway 狀態快取 60 秒（避免一直打）
_status_cache: dict[str, Any] = {"at": 0.0, "data": None}

# ── 中文對照表 ────────────────────────────────────────
_REGION_ZH: dict[str, str] = {
    "us-west1": "美西（奧勒岡）",
    "us-west2": "美西（加州）",
    "us-east4": "美東（維吉尼亞）",
    "us-east-1": "美東",
    "asia-southeast1": "新加坡",
    "europe-west4": "歐洲（荷蘭）",
}
_STATUS_ZH: dict[str, str] = {
    "INVESTIGATING": "調查中",
    "IDENTIFIED": "已找到原因、修復中",
    "MONITORING": "監控中",
    "VERIFYING": "驗證中",
    "RESOLVED": "已恢復",
    "SCHEDULED": "已排程",
    "IN_PROGRESS": "進行中",
    "COMPLETED": "已完成",
}
_IMPACT_ZH: dict[str, str] = {
    "PARTIAL_OUTAGE": "部分中斷",
    "MAJOR_OUTAGE": "嚴重中斷",
    "MINOR": "輕微",
    "DEGRADED_PERFORMANCE": "效能下降",
    "MAINTENANCE": "維護",
}
#: 常見字詞 → 中文（找不到的整句就保留原文，另外附上）
_PHRASE_ZH: tuple[tuple[str, str], ...] = (
    ("we have identified the cause of the storage performance degradation affecting some users in us west",
     "我們已找到「美西部分用戶儲存效能下降」的原因"),
    ("we are aware of an issue affecting attached storage performance for some users in us west.",
     "我們知道美西部分用戶的附加儲存效能出了問題。"),
    ("we have identified the cause and are actively working to restore normal performance.",
     "我們已找到原因，正積極恢復正常效能。"),
    ("we will continue to provide updates as we make progress.",
     "我們會持續更新修復進度。"),
    ("we will provide updates as we learn more.", "有進一步消息會再更新。"),
    ("users may experience slow services and databases,", "用戶可能會遇到服務與資料庫變慢，"),
    ("deploys that are slow to become healthy, or", "部署要很久才會健康，或"),
    ("and are actively working to resolve it", "，正在積極修復中"),
    ("queries taking longer than expected,", "查詢比平常久，"),
    ("intermittent crash loops.", "間歇性重啟。"),
    ("storage performance degradation", "儲存效能下降"),
    ("slow attached storage for some users in us west", "美西部分用戶的附加儲存變慢"),
    ("slow attached storage", "附加儲存變慢"),
    ("attached storage", "附加儲存（伺服器硬碟）"),
    ("degraded performance", "效能下降"),
    ("partial outage", "部分中斷"),
    ("major outage", "嚴重中斷"),
    ("us west", "美西"),
    ("us east", "美東"),
    ("storage", "儲存（硬碟）"),
    ("outage", "中斷"),
    ("europe", "歐洲"),
    ("singapore", "新加坡"),
)


def _zh(text: str) -> str:
    """把英文說明盡量轉成中文（已知句直接翻；其餘逐詞替換）。"""
    s = (text or "").strip()
    if not s:
        return ""
    out = s
    for en, zh in _PHRASE_ZH:
        out = re.sub(re.escape(en), zh, out, flags=re.IGNORECASE)
    return out


def _region_zh(region: str) -> str:
    for key, zh in _REGION_ZH.items():
        if region.startswith(key):
            return zh
    return region or "未知"


def _region_hit(group_name: str, region: str) -> bool:
    """這個故障元件是不是落在「本站機房」所在的區域。"""
    g = (group_name or "").lower()
    r = (region or "").lower()
    if r.startswith("us-west") and "west" in g:
        return True
    if r.startswith("us-east") and "east" in g:
        return True
    if r.startswith("asia") and ("asia" in g or "singapore" in g):
        return True
    if r.startswith("europe") and ("europe" in g or "netherlands" in g):
        return True
    return False


# ── Railway 機房狀態 ─────────────────────────────────
async def railway_status(force: bool = False) -> dict:
    now = time.time()
    cached = _status_cache["data"]
    if cached and not force and (now - _status_cache["at"]) < _STATUS_TTL:
        return cached

    out: dict[str, Any] = {"ok": False, "incidents": [], "affected": False, "error": ""}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8) as c:
            resp = await c.get(_STATUS_URL)
        raw = resp.json()
        for it in raw.get("activeIncidents") or []:
            comps = it.get("components") or []
            groups = [c.get("groupName") or "" for c in comps]
            affects = any(_region_hit(g, OUR_REGION) for g in groups)
            updates = it.get("updates") or []
            latest = updates[0].get("message", "") if updates else ""
            out["incidents"].append({
                "title": _zh(it.get("title", "")),
                "title_raw": it.get("title", ""),
                "status": _STATUS_ZH.get(it.get("status", ""), it.get("status", "")),
                "impact": _IMPACT_ZH.get(
                    (comps[0].get("impact") if comps else "") or "", (comps[0].get("impact") if comps else "") or ""),
                "areas": "、".join(dict.fromkeys(
                    f"{_zh(c.get('name', ''))}（{_zh(c.get('groupName', ''))}）" for c in comps if c.get("name"))),
                "latest": _zh(latest),
                "url": f"https://status.railway.com/incident/{it.get('slug', '')}",
                "affects_us": affects,
            })
            if affects:
                out["affected"] = True
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"讀取 Railway 狀態失敗：{str(exc)[:80]}"

    _status_cache["data"] = out
    _status_cache["at"] = now
    return out


# ── 本機系統 ─────────────────────────────────────────
def disk_info() -> list[dict]:
    """網站資料磁碟（Railway Volume `/data`）的容量與剩餘。

    ⚠️ 只看我們的 Volume —— **不看「系統磁碟」也不看 CPU 負載**：
    那兩個在容器裡讀到的是 **Railway 主機**的數字（幾 TB、共用、不是我們的），
    顯示出來只會嚇到自己（小羅 2026-10-04 實際被 0.1%／39% 嚇到）。
    """
    path = settings.data_dir or db.data_dir()
    try:
        u = shutil.disk_usage(os.path.realpath(path))
    except Exception:  # noqa: BLE001
        return []
    return [{"label": "網站資料磁碟（資料庫／cookies／快取）",
             "total": u.total, "used": u.used, "free": u.free,
             "percent": round(u.used / u.total * 100, 1) if u.total else 0}]


def db_latency_ms(rounds: int = 3) -> float | None:
    """資料庫反應時間（毫秒）。儲存變慢時這個數字會直接變大。"""
    best: float | None = None
    for _ in range(max(1, rounds)):
        try:
            t0 = time.perf_counter()
            db.query("SELECT 1")
            ms = (time.perf_counter() - t0) * 1000
        except Exception:  # noqa: BLE001
            return None
        best = ms if best is None else min(best, ms)
    return round(best, 1) if best is not None else None


def region_info() -> dict:
    return {"id": OUR_REGION, "zh": _region_zh(OUR_REGION)}


# ── 綜合診斷（中文，一句話說「現在怎麼了」）──────────
def _diagnose(rail: dict, latency: float | None, disks: list[dict]) -> dict:
    if rail.get("incidents"):
        hit = [i for i in rail["incidents"] if i["affects_us"]]
        if hit:
            it = hit[0]
            return {"level": "warn", "short": "機房故障（本站受影響）",
                    "detail": f"Railway 機房：{it['title']}（{it['status']}）。"
                              f"這會讓本站變慢或連不上，等 Railway 修好即恢復。"}
        it = rail["incidents"][0]
        return {"level": "info", "short": "機房有狀況（本站可能不受影響）",
                "detail": f"Railway 機房：{it['title']}（{it['status']}）。"}
    if latency is not None and latency > 3000:
        return {"level": "warn", "short": "資料庫反應很慢",
                "detail": f"資料庫單次查詢 {latency:.0f} 毫秒（>3 秒）→ 儲存可能出問題。"}
    if latency is not None and latency > 800:
        return {"level": "info", "short": "資料庫反應偏慢",
                "detail": f"資料庫單次查詢 {latency:.0f} 毫秒，建議再觀察。"}
    for d in disks:
        if d["percent"] >= 85:
            return {"level": "warn", "short": "磁碟快滿了",
                    "detail": f"{d['label']} 已用 {d['percent']}%，請盡快清理。"}
    return {"level": "ok", "short": "一切正常", "detail": "目前沒有偵測到異常。"}


async def snapshot(force: bool = False) -> dict:
    """一次抓齊所有即時狀態（後台系統監控用）。"""
    from . import monitor

    rail = await railway_status(force=force)
    latency = db_latency_ms()
    disks = disk_info()
    proc = monitor.process_info()

    return {
        "ok": True,
        "at": time.time(),
        "region": region_info(),
        "railway": rail,
        "latency_ms": latency,
        "memory_mb": proc.get("memory_mb"),
        "uptime_seconds": proc.get("uptime_seconds"),
        "check_interval": proc.get("check_interval"),
        "disks": disks,
        "diagnosis": _diagnose(rail, latency, disks),
    }
