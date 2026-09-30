"""『關於我們』的聯絡資料（小羅 2026-09-30）。

小羅：「關於我們要能在**後台改資料** —— 我換手機、換 Email 時可以自己改；
預設值就先放現在的資料（a42599@gmail.com／0980-222196）。」

存法：`settings` 表（key = `about.email` / `about.phone` / `about.hours`）
      → 前台由 `/api/config` 帶出去；後台「關於我們」可改，改完前台立即生效。

⚠️ `hours` 預設留空 → 前台會用**該語言**的內建值（t('about_hours_val')），
   這樣繁／簡／英都顯示得對；小羅若自己填了，就照他填的顯示。
"""
from __future__ import annotations

from ..core import db

#: 預設值（小羅 2026-09-30：先用現在的資料）
DEFAULTS: dict[str, str] = {
    "email": "a42599@gmail.com",
    "phone": "0980-222196",
    "hours": "",
}

#: 欄位 → settings 表的 key
_KEYS = {"email": "about.email", "phone": "about.phone", "hours": "about.hours"}

#: 單一欄位最長字數（後台是單行輸入框，避免塞進一整篇文章）
_MAX = 120


def get() -> dict:
    """目前前台要顯示的聯絡資料（DB 沒設 → 用預設值）。"""
    out = dict(DEFAULTS)
    try:
        for field, key in _KEYS.items():
            v = db.get_setting(key)
            if v is not None and str(v).strip():
                out[field] = str(v)
    except Exception:  # noqa: BLE001
        pass
    return out


def save(data: dict) -> dict:
    """儲存後台填的聯絡資料（只存有帶到的欄位；空白＝清掉、回預設）。"""
    for field, key in _KEYS.items():
        if field in (data or {}):
            db.set_setting(key, str(data[field] or "").strip()[:_MAX])
    return get()
