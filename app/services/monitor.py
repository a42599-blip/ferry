"""背景監控（規格書 10-4-1 的通知來源）。

每 N 分鐘檢查一次，出事就發通知：
  - 網站／服務是否活著
  - 解析 5xx／失敗率
  - 記憶體用量（v8i8 就是被 OOM 下架）
  - 出口 IP 有沒有變（換 IP 會影響平台解析）
  - 資料庫用量
並在每天早上寄一封「每日摘要」。

⚠️ 全部包在 try/except：監控本身壞掉絕不能影響網站。
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

from ..core import db
from . import notify

# 我們「真的有用到」的雲端元件（只用這些才告警；小羅 2026-10-06：跟本站無關的不要吵）
# 例：只用 Cloudflare 的 CDN／DNS；不用 API Shield／Cloudflare One／WARP／Durable Objects
_USED_COMPONENTS = ("cdn", "dns", "cloudflare network", "authoritative dns",
                    "ssl", "waf", "cache", "cloudflare sites", "website")


def _relevant(component_names: list[str]) -> bool:
    """事件影響的元件是否包含我們用到的（判斷『跟本站有沒有關』）。"""
    return any(any(u in (n or "").lower() for u in _USED_COMPONENTS) for n in component_names)


# 常見雲端事件的中文對照（英文原名 → 中文）；沒有對照就只留原文＋中文説明
_INCIDENT_ZH = {
    "api shield jwt validation errors": "API Shield 的 JWT 驗證發生錯誤",
    "cloudflare one clients are incorrectly challenged on some sites":
        "Cloudflare One 用戶端在某些網站被錯誤要求驗證",
    "incorrect geo location for some cloudflare warp users":
        "部分 Cloudflare WARP 使用者的地理位置顯示錯誤",
}


def _zh(name: str | None) -> str:
    return _INCIDENT_ZH.get((name or "").strip().lower(), "")


# 內容限制類錯誤碼（不算「我們的失敗」；不影響平台成功率告警）
# 小羅 2026-10-06：內容未公開／付費／地區…是「正常」，不該一直告警
_CONTENT_CODES = {"NOT_PUBLIC", "RESTRICTED", "PAID", "REGION", "COPYRIGHT",
                  "DELETED", "NO_VIDEO", "LIVE"}


def _is_content_issue(r: dict) -> bool:
    """True＝這筆失敗是「內容本身」問題（不是我們平台壞）。"""
    return (r.get("result") == "fail") and ((r.get("error_code") or "").upper() in _CONTENT_CODES)


# 測試裝置（不列入告警計算）：聯動測試、明顯測試／探測用
_TEST_DEV_PAT = ("linkage-test", "dev_link_test", "probe", "audit", "example.com", "ferry.local")


def _is_test_device(device_id: str | None) -> bool:
    """回傳 True＝這是測試裝置（告警時排除；小羅 2026-10-06）。"""
    d = (device_id or "").lower()
    if not d:
        return False
    return any(t in d for t in _TEST_DEV_PAT)

CHECK_INTERVAL = 300          # 5 分鐘檢查一次
DIGEST_HOUR = 22              # 每天 22:00（台北時間，小羅 2026-10-04 指定）寄摘要
_started_at = time.time()


def memory_mb() -> Optional[float]:
    """本行程記憶體用量（MB）。"""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:  # noqa: BLE001
        return None


async def egress_ip() -> Optional[str]:
    from ..core.http import HttpClient

    try:
        async with HttpClient(timeout=8) as http:
            return (await http.get_json("https://api.ipify.org?format=json")).get("ip")
    except Exception:  # noqa: BLE001
        return None


async def cloud_status() -> dict[str, Any]:
    """查 Railway／Cloudflare 官方狀態頁（**真實資料**，非模擬）。

    小羅 2026-10-06：「雲端機房異常這些你去自己改，而且要能真的偵測到數據。」
    → Railway 用 Instatus（`railway.instatus.com/summary.json`）；
      Cloudflare 用 Statuspage（`cloudflarestatus.com/api/v2/summary.json`）。
    回傳 {ok, abnormal, items:[{vendor,status,name,impact}]}
    """
    from ..core.http import HttpClient

    items: list[dict[str, Any]] = []
    abnormal = False
    try:
        async with HttpClient(timeout=12) as http:
            # Railway（Instatus）：page.status = UP / HASISSUES / UNDERMAINTENANCE / DOWN
            try:
                d = await http.get_json("https://railway.instatus.com/summary.json")
                st = ((d.get("page") or {}).get("status") or "UP").upper()
                if st not in ("UP", ""):
                    abnormal = True
                    items.append({"vendor": "Railway", "status": st, "impact": "major",
                                  "name": "Railway 平台服務異常", "zh": "Railway 平台服務異常"})
            except Exception as exc:  # noqa: BLE001
                items.append({"vendor": "Railway", "status": "unknown",
                              "name": f"狀態頁查詢失敗：{exc}", "impact": "unknown"})
            # Cloudflare（Statuspage）：status.indicator = none / minor / major / critical
            try:
                d = await http.get_json("https://www.cloudflarestatus.com/api/v2/summary.json")
                ind = ((d.get("status") or {}).get("indicator") or "none").lower()
                if ind != "none":
                    abnormal = True
                comps = {c.get("id"): c.get("name") for c in (d.get("components") or [])}
                for inc in (d.get("incidents") or []):
                    if (inc.get("status") or "") in ("resolved", "postmortem"):
                        continue
                    names = [comps.get(c.get("id") if isinstance(c, dict) else c, "")
                             for c in (inc.get("components") or [])]
                    items.append({"vendor": "Cloudflare", "status": inc.get("status"),
                                  "name": inc.get("name"), "impact": inc.get("impact"),
                                  "zh": _zh(inc.get("name")), "components": names,
                                  "relevant": _relevant(names)})
            except Exception as exc:  # noqa: BLE001
                items.append({"vendor": "Cloudflare", "status": "unknown",
                              "name": f"狀態頁查詢失敗：{exc}", "impact": "unknown"})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "abnormal": False, "items": [], "error": str(exc)}
    return {"ok": True, "abnormal": abnormal, "items": items}


async def check_once(*, notify_on_start: bool = False) -> dict:
    """跑一輪檢查，回傳狀態（後台系統頁也會顯示）。"""
    out: dict[str, Any] = {"at": time.time()}

    # ① 記憶體
    mem = memory_mb()
    out["memory_mb"] = mem
    if mem and mem > 400:
        await notify.notify("memory_high", "記憶體用量偏高",
                            f"目前 RSS = {mem} MB。若持續成長可能被平台 OOM 下架。")

    # ② 出口 IP
    ip = await egress_ip()
    out["egress_ip"] = ip
    if ip:
        last = db.get_setting("last_egress_ip")
        if last and last != ip and not notify_on_start:
            await notify.notify("egress_ip_change", "出口 IP 改變",
                                f"原本：{last}\n現在：{ip}\n平台解析可能受影響。")
        db.set_setting("last_egress_ip", ip)

    # ③ 解析失敗率（近 30 分）— 小羅 2026-10-06：平台要寫清楚、測試要標明
    since = time.time() - 1800
    rows = [dict(r) for r in db.query(
        "SELECT device_id, platform, error_code, result FROM events"
        " WHERE kind='resolve' AND ts>=?", (since,))]
    live = [r for r in rows if not _is_test_device(r.get("device_id"))]
    tests = [r for r in rows if _is_test_device(r.get("device_id"))]
    live = [r for r in live if not _is_content_issue(r)]      # 內容限制不算我們的失敗
    total = len(live)
    fails = [r for r in live if r.get("result") == "fail"]
    out["resolve_30m"] = {"total": total, "fail": len(fails), "test": len(tests)}
    if total >= 10 and len(fails) >= 5:
        rate = len(fails) / total * 100
        out["resolve_fail_rate"] = round(rate, 1)
        if rate > 50:
            # 平台（含「未標示平台」— 前端上報的沒有平台）
            pl: dict[str, int] = {}
            for r in fails:
                k = r.get("platform") or "（未標示平台／前端上報）"
                pl[k] = pl.get(k, 0) + 1
            who = "、".join(f"{k}（{v} 次）" for k, v in sorted(pl.items(), key=lambda x: -x[1])[:5])
            # 失敗原因（錯誤碼）— 讓小羅知道是「內容未公開」還是「平台擋人」
            ec: dict[str, int] = {}
            for r in fails:
                k = r.get("error_code") or "-"
                ec[k] = ec.get(k, 0) + 1
            why = "、".join(f"{k}（{v}）" for k, v in sorted(ec.items(), key=lambda x: -x[1])[:5])
            tnote = f"\n（另有 {len(tests)} 次為測試，已排除）" if tests else ""
            await notify.notify("platform_fail", "解析失敗率過高",
                                f"近 30 分鐘：解析失敗 {len(fails)} 次／共 {total} 次（{rate:.0f}%）。{tnote}\n"
                                f"失敗平台：{who}\n"
                                f"失敗原因：{why}\n"
                                f"→ 可在後台「功能與平台」關閉問題平台；若為「內容未公開」屬正常。")

    # ④ 各平台失敗率（近 24 小時；排除測試裝置）
    rows24 = [dict(r) for r in db.query(
        "SELECT device_id, platform, result FROM events"
        " WHERE kind='resolve' AND ts>=?", (time.time() - 86400,))]
    agg: dict[str, list[int]] = {}
    for r in rows24:
        if _is_test_device(r.get("device_id")):
            continue
        if _is_content_issue(r):      # 內容限制不算失敗（小羅 2026-10-06）
            continue
        p = r.get("platform") or ""
        a = agg.setdefault(p, [0, 0])          # [ok, total]
        a[1] += 1
        if r.get("result") == "ok":
            a[0] += 1
    bad = [{"platform": p, "ok": a[0], "total": a[1],
            "success_rate": round(a[0] / a[1] * 100, 1) if a[1] else None}
           for p, a in agg.items() if p and a[1] >= 10 and a[0] / a[1] < 0.6]
    out["weak_platforms"] = [b["platform"] for b in bad]
    if bad:
        detail = "\n".join(f"  {b['platform']}：{b['success_rate']}%（成功 {b['ok']}／共 {b['total']}）" for b in bad)
        await notify.notify("platform_fail", "部分平台成功率偏低",
                            f"以下平台近 24 小時成功率 < 60%：\n{detail}\n"
                            f"（已排除測試裝置；請確認是否為「內容未公開／平台擋人」造成）")

    # ⑤ 資料庫用量
    size_mb = db.db_size_bytes() / 1024 / 1024
    out["db_mb"] = round(size_mb, 1)
    if size_mb > 400:
        await notify.notify("db_usage_high", "資料庫用量偏高",
                            f"目前 {size_mb:.0f} MB。可在後台「資料管理」刪除舊事件。")

    # ⑥ 雲端/機房狀態（Railway／Cloudflare）— 小羅 2026-10-06（官方狀態頁真實資料）
    #   只在「嚴重（major／critical）或 Railway 異常」才告警；minor 不吵（只進每日摘要）
    cs = await cloud_status()
    out["cloud_status"] = cs
    try:
        items = cs.get("items", []) if cs.get("ok") else []
        # 只告警「跟本站有關」的：Railway 異常，或 Cloudflare 事件影響到我們用到的元件（CDN／DNS…）
        major = [i for i in items if i.get("vendor") == "Railway" or i.get("relevant")]
        if major:
            sig = "|".join(sorted(f"{i['vendor']}:{i['name']}" for i in major))[:500]
            if db.get_setting("cloud_alert_sig") != sig:      # 同一事件不重複寄
                db.set_setting("cloud_alert_sig", sig)
                lines = []
                for i in major:
                    zh = i.get("zh") or ""
                    lines.append(f"  · 【{i['vendor']}】{zh or i.get('name')}"
                                 + (f"（英文原文：{i.get('name')}）" if zh else ""))
                await notify.notify("cloud_incident", "雲端服務異常",
                                    "偵測到雲端服務（Railway／Cloudflare）官方狀態頁有**較嚴重**異常：\n"
                                    + "\n".join(lines) +
                                    "\n\n【白話說明】這是指『雲端服務商本身』公告的異常，**不一定影響本站**；"
                                    "你能收到這封信，代表本站目前仍在運作。若你發現本站變慢或解析失敗變多，再告訴我們。")
        elif cs.get("ok"):
            db.set_setting("cloud_alert_sig", "")
    except Exception:  # noqa: BLE001
        pass

    db.set_setting("monitor_last", out)
    return out


def last_state() -> dict:
    return db.get_setting("monitor_last") or {}


def _local_hour() -> int:
    """台北時間的小時。"""
    return int(time.strftime("%H", time.localtime(time.time() + 8 * 3600)))


async def loop() -> None:
    """背景迴圈（FastAPI startup 時啟動）。"""
    await asyncio.sleep(20)                 # 等服務穩定再開始
    while True:
        try:
            await check_once(notify_on_start=False)
        except Exception:  # noqa: BLE001
            pass

        # 每日摘要
        try:
            today = time.strftime("%Y-%m-%d", time.localtime(time.time() + 8 * 3600))
            # 小羅 2026-10-04：改讀資料庫 —— 原本用記憶體變數，每次重新部署就會重寄一次摘要
            if _local_hour() >= DIGEST_HOUR and db.get_setting("last_digest_date") != today:
                db.set_setting("last_digest_date", today)
                await notify.send_digest(days=1)
                # 會員到期提醒 ＋ 到期後自動降回免費（每天一次）
                from . import members as _mem

                out = await _mem.run_expiry_tasks()
                if out.get("reminded") or out.get("downgraded"):
                    db.set_setting("last_expiry_run", str(out))
        except Exception:  # noqa: BLE001
            pass

        await asyncio.sleep(CHECK_INTERVAL)


def uptime_seconds() -> int:
    return int(time.time() - _started_at)


def process_info() -> dict:
    return {
        "uptime_seconds": uptime_seconds(),
        "memory_mb": memory_mb(),
        "pid": os.getpid(),
        "check_interval": CHECK_INTERVAL,
        "digest_hour": DIGEST_HOUR,
    }
