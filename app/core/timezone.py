"""時區判定（免費次數「每日歸零」的依據）。

小羅 2026-09-26 定案：
    以「設備所在位置」的當地時間為準 → 美國用美國時間、台灣用台灣時間、
    北京用北京時間、日本用日本時間。

來源優先順序（後端為準，前端值不可信）：
1. Cloudflare 提供的 `cf-timezone`（免費、最準）
2. Cloudflare 的 `cf-ipcountry` → 對應代表城市（後備）
3. 前端送來的瀏覽器時區（只當「提示」）
4. 取不到 → Asia/Taipei
"""
from __future__ import annotations

# Cloudflare 會加的標頭
HDR_TZ = "cf-timezone"
HDR_COUNTRY = "cf-ipcountry"

# 國家 → 代表時區（只列常見；涵蓋我們主要客群）
COUNTRY_TZ: dict[str, str] = {
    "TW": "Asia/Taipei",
    "HK": "Asia/Hong_Kong",
    "MO": "Asia/Macau",
    "CN": "Asia/Shanghai",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "SG": "Asia/Singapore",
    "MY": "Asia/Kuala_Lumpur",
    "TH": "Asia/Bangkok",
    "VN": "Asia/Ho_Chi_Minh",
    "PH": "Asia/Manila",
    "ID": "Asia/Jakarta",
    "IN": "Asia/Kolkata",
    "US": "America/New_York",
    "CA": "America/Toronto",
    "MX": "America/Mexico_City",
    "BR": "America/Sao_Paulo",
    "GB": "Europe/London",
    "DE": "Europe/Berlin",
    "FR": "Europe/Paris",
    "ES": "Europe/Madrid",
    "IT": "Europe/Rome",
    "NL": "Europe/Amsterdam",
    "AU": "Australia/Sydney",
    "NZ": "Pacific/Auckland",
    "AE": "Asia/Dubai",
    "SA": "Asia/Riyadh",
    "RU": "Europe/Moscow",
    "TR": "Europe/Istanbul",
}

DEFAULT_TZ = "Asia/Taipei"


def from_request(request=None) -> str:
    """從請求判斷使用者時區（後端為準）。"""
    if request is None:
        return DEFAULT_TZ
    headers = getattr(request, "headers", {}) or {}

    # 1) Cloudflare 時區（最準）
    tz = (headers.get(HDR_TZ) or headers.get(HDR_TZ.title()) or "").strip()
    if tz and _is_valid(tz):
        return tz

    # 2) Cloudflare 國家碼 → 代表時區
    cc = (headers.get(HDR_COUNTRY) or headers.get(HDR_COUNTRY.title()) or "").strip().upper()
    if cc in COUNTRY_TZ:
        return COUNTRY_TZ[cc]

    # 3) 前端提示（僅後備）
    hinted = (headers.get("x-timezone") or "").strip()
    if hinted and _is_valid(hinted):
        return hinted

    # 4) 預設
    return DEFAULT_TZ


def fmt(ts: float, tz_name: str | None = None, pattern: str = "%Y-%m-%d") -> str:
    """把時間戳格式化成「指定時區」的字串（預設 Asia/Taipei）。

    信件裡所有日期都要走這裡 —— 不要用伺服器的 `time.localtime`
    （Railway 是 UTC，會跟會員的當地時間差一天）。
    """
    import datetime as _dt

    try:
        from zoneinfo import ZoneInfo

        return _dt.datetime.fromtimestamp(ts, ZoneInfo(tz_name or DEFAULT_TZ)).strftime(pattern)
    except Exception:  # noqa: BLE001 — 時區查不到就退回系統時間
        import time as _t

        return _t.strftime(pattern, _t.localtime(ts))


def _is_valid(tz: str) -> bool:
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
        return True
    except Exception:
        return False
