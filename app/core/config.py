"""設定集中管理（唯一讀環境變數的地方）。

（規格書第 12 章／第 21-4 章：不准魔術字串散落各處）
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # 基本
    app_name: str = "ferry"
    debug: bool = field(default_factory=lambda: _env_bool("DEBUG", True))

    # 出口 / 網路
    http_timeout: int = field(default_factory=lambda: _env_int("HTTP_TIMEOUT", 20))
    http_retries: int = field(default_factory=lambda: _env_int("HTTP_RETRIES", 2))
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "USER_AGENT",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        )
    )

    # 功能開關的預設值（真正的值由後台設定決定，見 services/flags.py）
    free_limit_enabled: bool = field(default_factory=lambda: _env_bool("FREE_LIMIT_ENABLED", False))
    # 🆕 2026-09-26 小羅定案：免費次數改為每日 5 次（原 3 次）
    free_download_per_day: int = field(default_factory=lambda: _env_int("FREE_DOWNLOAD_PER_DAY", 5))
    free_transfer_per_day: int = field(default_factory=lambda: _env_int("FREE_TRANSFER_PER_DAY", 5))

    # 歷史記錄（存使用者瀏覽器，我們零儲存）
    history_limit: int = field(default_factory=lambda: _env_int("HISTORY_LIMIT", 50))

    # 平台
    enabled_platforms: str = field(
        default_factory=lambda: os.getenv("ENABLED_PLATFORMS", "")  # 空＝全部啟用（依 registry）
    )

    # 平台 cookies（IG／FB／X／微博／頭條／西瓜… 需要）
    # 目錄內放 `<平台識別碼>.txt`（Netscape 格式），該平台就會自動帶上。
    cookies_dir: str = field(default_factory=lambda: os.getenv("COOKIES_DIR", ""))

    # 伺服器代理下載的逾時（秒）
    proxy_download_timeout: int = field(
        default_factory=lambda: _env_int("PROXY_DOWNLOAD_TIMEOUT", 300)
    )

    # 單次解析的「硬性」總逾時（秒）——保証不會卡死使用者
    # （抖音要走真瀏覽器，約 10～25 秒，所以給到 60）
    resolve_timeout: int = field(default_factory=lambda: _env_int("RESOLVE_TIMEOUT", 60))

    # 資料（SQLite）。Railway 正式站請掛 Volume 並指向它（例：/data）
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", ""))

    # 後台
    admin_user: str = field(default_factory=lambda: os.getenv("ADMIN_USER", "admin"))
    admin_password: str = field(
        default_factory=lambda: os.getenv("ADMIN_PASSWORD", "ferry-admin")
    )
    admin_token_ttl: int = field(default_factory=lambda: _env_int("ADMIN_TOKEN_TTL", 43200))
    # 通知收件人（逗號分隔；可在後台改）
    notify_emails: str = field(
        default_factory=lambda: os.getenv("NOTIFY_EMAILS", "a42599@gmail.com")
    )


settings = Settings()
