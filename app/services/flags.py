"""功能／平台開關（唯一讀寫點）。

後台設定 → flags（這裡）→ /api/config 給前端；後端各服務動作前也檢查這裡。
改開關**不需要重新部署**（規格書第 22-4 章）→ 所以設定存在資料庫。

快取：記憶體快取 5 秒（讀取頻繁），寫入立刻回寫 DB 並清快取。
"""
from __future__ import annotations

import time

from ..core import db, registry
from ..core.config import settings

# ── 功能模組開關（規格書第 22-3 章；小羅要求「全功能都能分開開關」）──
_DEFAULT_FEATURES: dict[str, bool] = {
    # 主要功能
    "feature.download": True,      # 無水印下載
    "feature.transfer": True,      # 無損傳輸
    "feature.quality": True,       # 畫質選擇（關掉＝只給最高畫質）
    "feature.audio_only": True,    # 純音訊輸出
    "feature.history": True,       # 歷史記錄
    "feature.free_limit": settings.free_limit_enabled,  # 免費次數限制
    # 頁面（關掉 → 前台導航與內容一併消失）
    "feature.teach": True,         # 教學頁
    "feature.plans": True,         # 方案頁
    "feature.member": True,        # 會員頁
    "feature.report": True,        # 回報問題
    # 全站
    "feature.maintenance": False,  # 全站維護模式
    # ── 以下為「預留」模組：接口先留好 ──
    "feature.auth": False,         # 會員登入（開發期關閉，畫面預留）
    "feature.billing": False,      # 付費／訂閱（P6 才做，接口先留）
}

#: 平台預設關閉（全部預設開啟）
_PLATFORM_OFF_BY_DEFAULT: set[str] = set()

#: 成功率低於這個門檻 → 後台可選擇自動關閉（規格書 10-1）
AUTO_OFF_THRESHOLD = 60.0
AUTO_OFF_MIN_SAMPLES = 20

_cache: dict | None = None
_cache_at: float = 0.0
_TTL = 5.0


def _defaults() -> dict:
    return {
        "features": dict(_DEFAULT_FEATURES),
        "platforms": {name: name not in _PLATFORM_OFF_BY_DEFAULT
                      for name in registry.platform_names()},
        "auto_off": False,
    }


def _load() -> dict:
    global _cache, _cache_at
    now = time.time()
    if _cache is not None and (now - _cache_at) < _TTL:
        return _cache

    state = _defaults()
    try:
        saved = db.get_setting("flags")
        if isinstance(saved, dict):
            state["features"].update(
                {k: bool(v) for k, v in (saved.get("features") or {}).items()
                 if k in _DEFAULT_FEATURES}
            )
            for name in registry.platform_names():      # 新註冊的平台自動加入
                state["platforms"].setdefault(name, True)
            state["platforms"].update(
                {k: bool(v) for k, v in (saved.get("platforms") or {}).items()
                 if k in state["platforms"]}
            )
            state["auto_off"] = bool(saved.get("auto_off", False))
    except Exception:  # noqa: BLE001 — DB 壞掉也要能動
        pass

    _cache, _cache_at = state, now
    return state


def _save() -> None:
    global _cache_at
    try:
        db.set_setting("flags", _load())
    except Exception:  # noqa: BLE001
        pass
    _cache_at = 0.0        # 立刻讓下一個請求重讀


def refresh() -> None:
    global _cache_at
    _cache_at = 0.0


# ── 功能 ────────────────────────────────────────────
def feature_enabled(key: str) -> bool:
    return bool(_load()["features"].get(key, False))


def set_feature(key: str, on: bool) -> None:
    _load()["features"][key] = bool(on)
    _save()


def all_features() -> dict[str, bool]:
    return dict(_load()["features"])


# ── 平台 ────────────────────────────────────────────
def platform_enabled(name: str) -> bool:
    st = _load()["platforms"]
    if name not in st:                      # 新註冊的平台自動加入
        st[name] = name not in _PLATFORM_OFF_BY_DEFAULT
    return bool(st[name])


def set_platform(name: str, on: bool) -> None:
    _load()["platforms"][name] = bool(on)
    _save()


def set_all(features: dict | None = None, platforms: dict | None = None) -> None:
    """後台「全部開啟／全部關閉」用。"""
    st = _load()
    if features:
        for k, v in features.items():
            if k in _DEFAULT_FEATURES:
                st["features"][k] = bool(v)
    if platforms:
        for k, v in platforms.items():
            if k in st["platforms"]:
                st["platforms"][k] = bool(v)
    _save()


def enabled_platforms() -> list[str]:
    return [n for n in registry.platform_names() if platform_enabled(n)]


def auto_off_enabled() -> bool:
    return bool(_load().get("auto_off"))


def set_auto_off(on: bool) -> None:
    _load()["auto_off"] = bool(on)
    _save()


# ── 給前端（/api/config）─────────────────────────────
def snapshot() -> dict:
    feats = all_features()
    plats = {name: platform_enabled(name) for name in registry.platform_names()}
    # 只回「前端要用到的」：關掉的平台不送（前端自然就不顯示）
    return {
        "features": feats,
        "platforms": {
            name: {"enabled": on, "label": registry.REGISTRY[name].label}
            for name, on in plats.items()
        },
        "enabled_platform_count": sum(1 for v in plats.values() if v),
        "quota": {
            "download_per_day": settings.free_download_per_day,
            "transfer_per_day": settings.free_transfer_per_day,
        },
        "history_limit": settings.history_limit,
        "maintenance": feats.get("feature.maintenance", False),
    }
