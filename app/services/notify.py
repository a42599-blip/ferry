"""通知（Email／Webhook）— 規格書 10-4-1。

支援三種寄送方式（有哪個用哪個，都沒有就只記錄在後台，不會遺失）：
  1. Resend  `RESEND_API_KEY`
  2. SMTP    `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS`
  3. Webhook `NOTIFY_WEBHOOK`（POST JSON，可接 Slack／Discord／n8n…）

規則（規格書 D 段）：
- 同一事件有**冷卻時間**（預設 30 分），避免轟炸
- 每個事件可**單獨開關**；嚴重事件**不可關閉**
- 所有通知都會寫進資料庫（後台「錯誤與告警」看得到歷史）
"""
from __future__ import annotations

import asyncio
import json
import os
import smtplib
import time
from email.message import EmailMessage
from typing import Any

from ..core import db
from ..core import timezone as tz_util

# ── 事件定義（key -> 標題 / 嚴重度 / 可否關閉）──────────
EVENTS: dict[str, dict[str, Any]] = {
    "site_down":        {"title": "網站掛掉／無回應", "severity": "critical", "can_disable": False},
    "http_5xx":         {"title": "5xx 比率飆升",     "severity": "critical", "can_disable": False},
    "memory_high":      {"title": "記憶體偏高／異常成長", "severity": "critical", "can_disable": False},
    "platform_fail":    {"title": "平台失敗率超標",   "severity": "warn",     "can_disable": True},
    "egress_ip_change": {"title": "出口 IP 改變",     "severity": "warn",     "can_disable": True},
    "pay_success":      {"title": "新付款成功",       "severity": "info",     "can_disable": True},
    "pay_failed":       {"title": "付款失敗／退款／爭議", "severity": "critical", "can_disable": False},
    "admin_login":      {"title": "後台登入（新裝置或新 IP）", "severity": "info", "can_disable": True},
    "admin_login_fail": {"title": "後台連續登入失敗", "severity": "warn",     "can_disable": False},
    "db_usage_high":    {"title": "資料庫／儲存用量偏高", "severity": "warn", "can_disable": True},
    "cloud_incident":   {"title": "雲端服務異常（Railway／Cloudflare）", "severity": "warn", "can_disable": True},
    "cert_expiring":    {"title": "SSL 憑證／網域即將到期", "severity": "critical", "can_disable": False},
    "copyright_notice": {"title": "版權檢舉／侵權投訴", "severity": "critical", "can_disable": False},
    "payout_request":   {"title": "提現申請",       "severity": "info",     "can_disable": True},
    "pay_amount_mismatch": {"title": "付款金額與訂單不符（未開通）", "severity": "critical", "can_disable": False},
    "pay_no_member":    {"title": "付款成功但找不到會員帳號", "severity": "critical", "can_disable": False},
    "user_report":      {"title": "使用者回報問題",   "severity": "info",     "can_disable": True},
    "deploy":           {"title": "部署完成／失敗",   "severity": "info",     "can_disable": True},
}

DEFAULT_COOLDOWN = 1800          # 30 分鐘


# ── 設定讀寫 ─────────────────────────────────────────
def _toggles() -> dict[str, bool]:
    saved = db.get_setting("notify_toggles") or {}
    out = {}
    for key, meta in EVENTS.items():
        if not meta["can_disable"]:
            out[key] = True
        else:
            out[key] = bool(saved.get(key, True))
    return out


def set_toggle(key: str, on: bool) -> None:
    if key not in EVENTS:
        return
    toggles = _toggles()
    if not EVENTS[key]["can_disable"]:
        return                       # 嚴重事件不准關
    toggles[key] = bool(on)
    db.set_setting("notify_toggles", toggles)


def event_enabled(key: str) -> bool:
    return _toggles().get(key, True)


def _last_sent(key: str) -> float:
    try:
        return float(db.get_setting(f"notify_last:{key}") or 0)
    except (TypeError, ValueError):
        return 0.0


def cooldown_left(key: str) -> int:
    left = DEFAULT_COOLDOWN - (time.time() - _last_sent(key))
    return max(0, int(left))


# ── 運送（三種選一）────────────────────────────────────
def mail_conf() -> dict[str, str]:
    """寄信設定：**後台設定（資料庫）優先**，其次環境變數。

    小羅 2026-10-04：「自動寄信這塊你把它做完」——
    做成後台可以自己填（不用碰 Railway）。
    """

    def g(key: str, env: str, default: str = "") -> str:
        v = db.get_setting("mail." + key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        return (os.getenv(env) or default).strip()

    return {
        "host": g("host", "SMTP_HOST"),
        "port": g("port", "SMTP_PORT", "587"),
        "user": g("user", "SMTP_USER"),
        "pass": g("pass", "SMTP_PASS"),
        "tls": g("tls", "SMTP_TLS", "1"),
        "from": g("from", "SMTP_FROM"),
        "resend": g("resend_key", "RESEND_API_KEY"),
        "webhook": (os.getenv("NOTIFY_WEBHOOK") or "").strip(),
    }


def transport() -> str:
    c = mail_conf()
    if c["resend"]:
        return "resend"
    if c["host"]:
        return "smtp"
    if c["webhook"]:
        return "webhook"
    return "none"


def _recipients() -> list[str]:
    from . import events as ev

    return ev.notify_recipients()


# ── 寄信額度（小羅 2026-10-04：「後台隨時可看還剩多少封」）────
#    Resend 免費方案：每天 100 封、每月 3,000 封。
#    ⚠️ Resend 沒有「額度」端點 → 用信件列表自己算（以台北時間為準）。
RESEND_DAILY_LIMIT = 100
RESEND_MONTHLY_LIMIT = 3000
_QUOTA_MAX_PAGES = 12                    # 最多掃 1,200 封，避免查太久


def _parse_dt(s: str):
    """Resend 的時間字串 → datetime（帶時區）。"""
    import datetime as _dt

    txt = str(s or "").strip().replace("Z", "+00:00")
    if not txt:
        return None
    try:
        d = _dt.datetime.fromisoformat(txt)
    except ValueError:
        try:
            d = _dt.datetime.strptime(txt[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def resend_quota() -> dict:
    """查這個月／今天寄了幾封、還剩多少（免費額度）。"""
    out: dict[str, Any] = {
        "ok": False, "transport": transport(),
        "daily_limit": RESEND_DAILY_LIMIT, "monthly_limit": RESEND_MONTHLY_LIMIT,
        "today": 0, "month": 0,
        "daily_left": RESEND_DAILY_LIMIT, "monthly_left": RESEND_MONTHLY_LIMIT,
        "error": "",
    }
    if transport() != "resend":
        out["error"] = "目前寄信方式不是 Resend"
        return out

    import datetime as _dt

    import httpx

    key = mail_conf()["resend"]
    tz8 = _dt.timezone(_dt.timedelta(hours=8))
    today = _dt.datetime.now(tz8).date()
    month_start = today.replace(day=1)
    after = None
    scanned = 0
    try:
        with httpx.Client(timeout=15) as c:
            for _ in range(_QUOTA_MAX_PAGES):
                params = {"limit": 100}
                if after:
                    params["after"] = after
                r = c.get("https://api.resend.com/emails", params=params,
                          headers={"Authorization": f"Bearer {key}"})
                if r.status_code != 200:
                    out["error"] = f"Resend 回應 {r.status_code}"
                    return out
                data = r.json()
                rows = data.get("data") or []
                if not rows:
                    break
                old = False
                for row in rows:
                    scanned += 1
                    ts = _parse_dt(row.get("created_at"))
                    if ts is None:
                        continue
                    day = ts.astimezone(tz8).date()
                    if day == today:
                        out["today"] += 1
                    if day >= month_start:
                        out["month"] += 1
                    else:
                        old = True
                after = rows[-1].get("id")
                if old or not data.get("has_more"):
                    break
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"查詢失敗：{str(exc)[:80]}"
    out["daily_left"] = max(0, RESEND_DAILY_LIMIT - out["today"])
    out["monthly_left"] = max(0, RESEND_MONTHLY_LIMIT - out["month"])
    out["scanned"] = scanned
    return out


def _send_sync(subject: str, body: str, to: list[str] | None = None) -> tuple[bool, str]:
    """實際寄送。回傳 (成功, 說明)。

    `to` 不給＝寄給後台設定的管理員收件人；
    給了＝寄給指定的人（會員忘記密碼的重設信就用這個）。
    """
    t = transport()
    c = mail_conf()
    to = to or _recipients()
    if not to:
        return False, "沒有設定收件人"

    if t == "resend":
        # 小羅 2026-10-04：改用 httpx —— urllib 的 User-Agent 會被 Resend 擋（403）
        import httpx

        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {c['resend']}"},
                json={"from": c["from"] or "onboarding@resend.dev",
                      "to": to, "subject": subject, "text": body},
                timeout=15,
            )
            return (200 <= resp.status_code < 300), f"resend {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            return False, f"resend 失敗：{str(exc)[:120]}"

    if t == "smtp":
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = c["from"] or c["user"] or "ferry@localhost"
        msg["To"] = ", ".join(to)
        msg.set_content(body)
        host = c["host"]
        port = int(c["port"] or 587)
        try:
            if c["tls"] == "2":          # 2 = SSL（465；有些機房會擋 587）
                with smtplib.SMTP_SSL(host, port, timeout=15) as s:
                    if c["user"]:
                        s.login(c["user"], c["pass"])
                    s.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=15) as s:
                    if c["tls"] == "1":
                        s.starttls()
                    if c["user"]:
                        s.login(c["user"], c["pass"])
                    s.send_message(msg)
            return True, "smtp ok"
        except Exception as exc:  # noqa: BLE001
            return False, f"smtp 失敗：{str(exc)[:120]}"

    if t == "webhook":
        import urllib.request

        payload = json.dumps({"subject": subject, "text": body, "to": to}).encode()
        req = urllib.request.Request(
            os.getenv("NOTIFY_WEBHOOK", ""), data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return (200 <= resp.status < 300), f"webhook {resp.status}"
        except Exception as exc:  # noqa: BLE001
            return False, f"webhook 失敗：{str(exc)[:120]}"

    return False, "尚未設定寄送方式（Resend／SMTP／Webhook）"


async def send_now(subject: str, body: str, to: list[str] | None = None) -> dict:
    """立刻寄（管理員測試按鈕／會員重設信都用這個）。"""
    ok, note = await asyncio.to_thread(_send_sync, subject, body, to)
    _log(subject, body, ok, note)
    return {"ok": ok, "transport": transport(), "note": note,
            "to": to or _recipients()}


def _log(subject: str, body: str, ok: bool, note: str, event: str | None = None) -> None:
    from . import events as ev

    ev.track("notify", result="ok" if ok else "fail",
             error_code=None if ok else note[:80],
             meta={"event": event, "subject": subject[:120], "body": body[:400],
                   "transport": transport(), "to": _recipients()})


# ── 對外主入口 ───────────────────────────────────────
async def notify(event: str, title: str, body: str, *, force: bool = False) -> dict:
    """發送一個事件通知（自動處理開關與冷卻）。"""
    meta = EVENTS.get(event, {"severity": "info", "can_disable": True})
    if not force:
        if not event_enabled(event):
            return {"sent": False, "reason": "事件已關閉"}
        left = cooldown_left(event)
        if left > 0:
            return {"sent": False, "reason": f"冷卻中（剩 {left} 秒）"}

    subject = f"[轉運站] {meta['severity'].upper()} · {title}"
    prefix = {"critical": "🔴", "warn": "🟠", "info": "🔵"}.get(meta["severity"], "🔵")
    text = (f"{prefix} {title}\n\n{body}\n\n"
            f"時間：{tz_util.fmt(time.time(), None, '%Y-%m-%d %H:%M:%S')}\n事件：{event}")

    if transport() == "none":
        _log(subject, text, False, "未設定寄送方式", event)
        db.set_setting(f"notify_last:{event}", time.time())
        return {"sent": False, "reason": "未設定寄送方式（已記錄在後台）"}

    ok, note = await asyncio.to_thread(_send_sync, subject, text)
    _log(subject, text, ok, note, event)
    if ok:
        db.set_setting(f"notify_last:{event}", time.time())
    return {"sent": ok, "note": note}


def recent(limit: int = 50) -> list[dict]:
    from . import events as ev

    rows = db.query(
        "SELECT ts, result, error_code, meta FROM events WHERE kind='notify'"
        " ORDER BY ts DESC LIMIT ?", (limit,),
    )
    out = []
    for r in rows:
        try:
            meta = json.loads(r["meta"]) if r["meta"] else {}
        except Exception:  # noqa: BLE001
            meta = {}
        out.append({"ts": r["ts"], "ok": r["result"] == "ok",
                    "error": r["error_code"], "subject": meta.get("subject"),
                    "event": meta.get("event"), "transport": meta.get("transport")})
    return out


# ── 每日摘要 ─────────────────────────────────────────
def build_digest(days: int = 1) -> tuple[str, str]:
    from . import events as ev

    s = ev.overview(days)
    plats = ev.by_platform(days)
    errs = ev.top_errors(days)
    t = ev.transfer_stats(days)

    from . import members as _mem

    ms = _mem.plan_stats()
    lines = [
        f"【轉運站】每日摘要（近 {days} 天）",
        "",
        f"進站瀏覽：{s['page_views']}    不重複訪客：{s['visitors']}（新 {s['new_visitors']}／回訪 {s['returning_visitors']}）",
        f"解析：{s['resolve_total']}（成功 {s['resolve_ok']}／失敗 {s['resolve_fail']}，成功率 "
        f"{s['success_rate'] if s['success_rate'] is not None else '–'}%）",
        f"下載：{s['downloads']} 次    傳輸：{s['transfers']} 次",
        f"收益：US$ {s['revenue']}",
        "",
        f"會員：今天新註冊 {ms['free_today']} 位（免費）　付費會員 {ms['paid']} 位"
        f"（月會員 {ms['monthly']}／終身 {ms['lifetime']}；其中手動開通 0 元 {ms['gift']} 位）",
        "",
        f"傳輸成功率：{t['success_rate'] if t['success_rate'] is not None else '–'}%",
        "",
        "各平台成功率：",
    ]
    for p in plats[:13]:
        rate = f"{p['success_rate']}%" if p["success_rate"] is not None else "–"
        lines.append(f"  {p['platform'] or '(未知)':<16} {rate:>7}  （成功 {p['ok']}／失敗 {p['fail']}）")
    if errs:
        lines += ["", "失敗 Top 3："]
        for e in errs[:3]:
            lines.append(f"  {e['platform']} · {e['error_code']} × {e['count']}")

    # 系統狀態（小羅 2026-10-06：每日摘要要有「正常／異常」）
    try:
        from . import monitor as _mon

        m = _mon.last_state() or {}
        cs = m.get("cloud_status") or {}
        cloud = "⚠️ 有異常" if cs.get("abnormal") else ("✅ 正常" if cs.get("ok") else "❓ 查不到")
        mem, dbm = m.get("memory_mb"), m.get("db_mb")
        lines += [
            "",
            "系統狀態：",
            f"  記憶體：{mem if mem is not None else '–'} MB　資料庫：{dbm if dbm is not None else '–'} MB",
            f"  出口 IP：{m.get('egress_ip') or '–'}",
            f"  雲端服務（Railway／Cloudflare）：{cloud}",
        ]
        if cs.get("abnormal"):
            for i in cs.get("items", [])[:4]:
                lines.append(f"    · 【{i.get('vendor')}】{i.get('name')}（{i.get('status')}）")
    except Exception:  # noqa: BLE001
        pass
    return "【轉運站】每日摘要", "\n".join(lines)


async def send_digest(days: int = 1) -> dict:
    subject, body = build_digest(days)
    # 附上寄信額度（小羅 2026-10-04：「附在每日摘要信裡」）
    q = await asyncio.to_thread(resend_quota)
    if q.get("ok"):
        body += (f"\n\n寄信額度：今天 {q['today']}/{q['daily_limit']} 封（剩 {q['daily_left']}）"
                 f"　本月 {q['month']}/{q['monthly_limit']} 封（剩 {q['monthly_left']}）")
    ok, note = await asyncio.to_thread(_send_sync, subject, body)
    _log(subject, body, ok, note, "digest")
    return {"ok": ok, "note": note, "to": _recipients(), "transport": transport()}


# ══════════════════════════════════════════════════════════════════
#  對「全體會員」發通知（小羅 2026-09-27 要求）
#  「如果平台有問題、要關掉、要更新，我們可以用郵件通知會員。」
#
#  ⚠️ 一次一封一封寄（不是同一封密件多收件人）：
#     避免收件人彼此看到對方 Email（隱私），也避免被判垃圾信。
#     每位收件人之間間隔 0.4 秒，避免被金流／郵件商限流。
# ══════════════════════════════════════════════════════════════════

def _send_one(to: str, subject: str, body: str) -> tuple[bool, str]:
    """寄給單一收件人。

    ⚠️ 設定一律以**後台（`mail_conf()`）為準**（小羅 2026-10-04：一對一信以前只讀環境變數，
    會跟後台設的 Resend 不一致，且 urllib 會被 Resend 擋 403 → 統一改用 httpx）。
    """
    c = mail_conf()
    t = transport()
    if t == "none":
        return False, "未設定寄送方式"

    if t == "resend":
        import httpx

        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {c['resend']}"},
                json={"from": c["from"] or "onboarding@resend.dev",
                      "to": [to], "subject": subject, "text": body},
                timeout=25,
            )
            return (200 <= resp.status_code < 300), f"resend {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            return False, f"resend 失敗：{str(exc)[:120]}"

    if t == "smtp":
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = c["from"] or c["user"] or "ferry@localhost"
        msg["To"] = to
        msg.set_content(body)
        host = c["host"]
        port = int(c["port"] or 587)
        try:
            if c["tls"] == "2":
                with smtplib.SMTP_SSL(host, port, timeout=25) as sv:
                    if c["user"]:
                        sv.login(c["user"], c["pass"])
                    sv.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=25) as sv:
                    if c["tls"] == "1":
                        sv.starttls()
                    if c["user"]:
                        sv.login(c["user"], c["pass"])
                    sv.send_message(msg)
            return True, "smtp ok"
        except Exception as exc:  # noqa: BLE001
            return False, f"smtp 失敗：{str(exc)[:120]}"

    return False, "這種寄送方式不支援一對一寄信"


async def send_to(to: str, subject: str, body: str) -> dict:
    """寄給單一收件人（會員到期提醒這類一對一通知用）。"""
    if not to:
        return {"ok": False, "reason": "沒有收件人"}
    ok, note = await asyncio.to_thread(_send_one, to, subject, body)
    _log(subject, f"→ {to}", ok, note, "one")
    return {"ok": ok, "note": note}


async def broadcast(subject: str, body: str, *, only: str = "all") -> dict:
    """對會員發送通知（背景執行，回傳預估結果）。"""
    from . import members

    targets = members.emails(only)
    if not targets:
        return {"ok": False, "reason": "沒有會員 Email", "sent": 0, "total": 0}
    sent = failed = 0
    failures: list[str] = []
    for i, to in enumerate(targets):
        ok, note = await asyncio.to_thread(_send_one, to, subject, body)
        if ok:
            sent += 1
        else:
            failed += 1
            if len(failures) < 5:
                failures.append(f"{to}: {note}")
        if i < len(targets) - 1:
            await asyncio.sleep(0.4)      # 避免被限流
    _log(subject, f"廣播給 {len(targets)} 人", sent > 0, f"成功 {sent}／失敗 {failed}", "broadcast")
    return {"ok": True, "sent": sent, "failed": failed, "total": len(targets),
            "transport": transport(), "failures": failures}
