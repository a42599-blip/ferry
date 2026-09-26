"""功能／平台開關（唯一讀寫點）。

後台設定 → flags（這裡）→ /api/config 給前端；後端各服務動作前也檢查這裡。
改開關「不需要重新部署」（規格書第 22-4 章）。

⚠️ 現在是記憶體暫存；接資料庫後只需改 _load/_save，呼叫端不用動。
"""
from __future__ import annotations

from ..core.config import settings
from ..core import registry

# ── 功能模組開關（規格書第 22-3 章）──────────────────
_DEFAULT_FEATURES: dict[str, bool] = {
    "feature.download": True,      # 無水印下載
    "feature.transfer": True,      # 無損傳輸
    "feature.quality": True,       # 畫質選擇（關掉＝只給原畫）
    "feature.audio_only": True,    # 純音訊輸出
    "feature.history": True,       # 歷史記錄（可看、不可點擊重新下載）
    "feature.free_limit": settings.free_limit_enabled,  # 免費次數限制（開發期關閉）
    "feature.maintenance": False,  # 全站維護模式
    # ── 以下為「預留」模組：現在不實作，但接口先留好 ──
    "feature.auth": False,         # 會員登入（開發期關閉，畫面預留）
    "feature.billing": False,      # 付費／訂閱（P6 才做，接口先留）
}

# 平台開關（依 registry 動態產生，預設全開；YouTube 預設可關）
_PLATFORM_OFF_BY_DEFAULT: set[str] = set()

_state: dict | None = None


def _load() -> dict:
    global _state
    if _state is None:
        _state = {
            "features": dict(_DEFAULT_FEATURES),
            "platforms": {name: True for name in registry.platform_names()},
        }
    return _state


def _save() -> None:
    # TODO(資料庫)：改寫入 DB；目前為記憶體
    return None


# ── 功能 ────────────────────────────────────────────
def feature_enabled(key: str) -> bool:
    return bool(_load()["features"].get(key, False))


def set_feature(key: str, on: bool) -> None:
    _load()["features"][key] = bool(on)
    _save()


# ── 平台 ────────────────────────────────────────────
def platform_enabled(name: str) -> bool:
    st = _load()["platforms"]
    if name not in st:                      # 新註冊的平台自動加入
        st[name] = name not in _PLATFORM_OFF_BY_DEFAULT
    return bool(st[name])


def set_platform(name: str, on: bool) -> None:
    _load()["platforms"][name] = bool(on)
    _save()


def enabled_platforms() -> list[str]:
    return [n for n in registry.platform_names() if platform_enabled(n)]


# ── 給前端（/api/config）─────────────────────────────
def snapshot() -> dict:
    st = _load()
    return {
        "features": dict(st["features"]),
        "platforms": {
            name: {"enabled": platform_enabled(name)}
            for name in registry.platform_names()
        },
        "quota": {
            "download_per_day": settings.free_download_per_day,
            "transfer_per_day": settings.free_transfer_per_day,
        },
        "history_limit": settings.history_limit,
    }
