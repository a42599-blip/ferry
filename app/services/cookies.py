"""平台 cookies 管理。

有些平台（Instagram／Facebook／X／微博／今日頭條／西瓜視頻…）沒有 cookies
就一律被擋。我們不寫死私人 cookies，而是：

    COOKIES_DIR/
        instagram.txt        ← Netscape 格式（瀏覽器外掛可匯出）
        facebook.txt
        x.txt
        weibo.txt
        ...

模組存在時 → yt-dlp 自動帶上；不存在 → 該平台會回「需要 cookies」的明確訊息。

（規格書：不寫死 cookies；後台可更新 = P5 之後接）
"""
from __future__ import annotations

import os

from ..core.config import settings


def cookiefile_for(platform: str) -> str | None:
    """回傳該平台的 cookie 檔路徑；沒有就回 None。

    ⚠️ 與 `target_dir()` 用同一個目錄，否則「存了卻讀不到」。
    """
    root = settings.cookies_dir or _default_dir()
    if not os.path.isdir(root):
        return None
    for name in (f"{platform}.txt", f"{platform}_cookies.txt"):
        path = os.path.join(root, name)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    return None


def available() -> dict[str, bool]:
    """哪些平台目前有 cookies（給後台／健康檢查用）。"""
    from ..core import registry

    return {name: cookiefile_for(name) is not None for name in registry.platform_names()}


def _default_dir() -> str:
    return os.path.join(os.environ.get("DATA_DIR") or os.path.join(os.getcwd(), "data"),
                        "cookies")


def target_dir() -> str:
    """cookies 存放目錄（Railway 上是 Volume，重啟不會消失）。"""
    root = settings.cookies_dir or _default_dir()
    os.makedirs(root, exist_ok=True)
    return root


def save(platform: str, content: str) -> dict:
    """寫入某平台的 cookie 檔（Netscape 格式）。"""
    if not content or len(content.strip()) < 10:
        raise ValueError("cookie 內容太短")
    path = os.path.join(target_dir(), f"{platform}.txt")
    # 保險：只接受 Netscape cookie 或 header 字串，拒絕奇怪的東西
    text = content.strip()
    if "=" not in text:
        raise ValueError("看起來不是 cookie 內容（找不到 = ）")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        if not text.startswith("#") and "\t" not in text:
            # 允許貼 "name=value; name2=value2" 形式 → 轉成 Netscape
            f.write("# Netscape HTTP Cookie File\n")
            for pair in text.split(";"):
                pair = pair.strip()
                if not pair or "=" not in pair:
                    continue
                name, _, value = pair.partition("=")
                f.write(f".{platform}.com\tTRUE\t/\tFALSE\t0\t{name.strip()}\t{value.strip()}\n")
        else:
            f.write(text if text.endswith("\n") else text + "\n")
    return {"platform": platform, "path": path, "bytes": os.path.getsize(path)}


def remove(platform: str) -> bool:
    path = os.path.join(target_dir(), f"{platform}.txt")
    if not os.path.isfile(path):
        return False
    # 刪除一律走資源回收筒（小羅鐵律）
    try:
        import subprocess

        script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "_工具", "回收筒.sh")
        if os.path.isfile(script):
            subprocess.run(["bash", script, path], check=False, timeout=20)
        else:
            os.replace(path, path + ".deleted")
    except Exception:  # noqa: BLE001
        return False
    return True


def detail(platform: str) -> dict:
    """cookie 檔的摘要（不顯示完整內容，只顯示筆數與時間）。"""
    path = cookiefile_for(platform)
    if not path:
        return {"platform": platform, "present": False}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [l for l in f if l.strip() and not l.startswith("#")]
        st = os.stat(path)
        import time as _t

        return {"platform": platform, "present": True,
                "cookies": len(lines), "updated_at": _t.strftime("%Y-%m-%d %H:%M", _t.localtime(st.st_mtime))}
    except OSError:
        return {"platform": platform, "present": False}


def overview() -> list[dict]:
    from ..core import registry

    return [detail(n) for n in registry.platform_names()]

