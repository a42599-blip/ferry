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
    "cert_expiring":    {"title": "SSL 憑證／網域即將到期", "severity": "critical", "can_disable": False},
    "copyright_notice": {"title": "版權檢舉／侵權投訴", "severity": "critical", "can_disable": False},
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
def transport() -> str:
    if os.getenv("RESEND_API_KEY"):
        return "resend"
    if os.getenv("SMTP_HOST"):
        return "smtp"
    if os.getenv("NOTIFY_WEBHOOK"):
        return "webhook"
    return "none"


def _recipients() -> list[str]:
    from . import events as ev

    return ev.notify_recipients()


def _send_sync(subject: str, body: str) -> tuple[bool, str]:
    """實際寄送。回傳 (成功, 說明)。"""
    t = transport()
    to = _recipients()
    if not to:
        return False, "沒有設定收件人"

    if t == "resend":
        import urllib.request

        payload = json.dumps({
            "from": os.getenv("NOTIFY_FROM", "ferry <onboarding@resend.dev>"),
            "to": to, "subject": subject, "text": body,
        }).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails", data=payload,
            headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return (200 <= resp.status < 300), f"resend {resp.status}"
        except Exception as exc:  # noqa: BLE001
            return False, f"resend 失敗：{str(exc)[:120]}"

    if t == "smtp":
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "ferry@localhost"))
        msg["To"] = ", ".join(to)
        msg.set_content(body)
        host = os.getenv("SMTP_HOST", "")
        port = int(os.getenv("SMTP_PORT", "587") or 587)
        try:
            with smtplib.SMTP(host, port, timeout=15) as s:
                if os.getenv("SMTP_TLS", "1") == "1":
                    s.starttls()
                if os.getenv("SMTP_USER"):
                    s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS", ""))
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


async def send_now(subject: str, body: str) -> dict:
    """立刻寄（管理員測試按鈕用）。"""
    ok, note = await asyncio.to_thread(_send_sync, subject, body)
    _log(subject, body, ok, note)
    return {"ok": ok, "transport": transport(), "note": note, "to": _recipients()}


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
    text = f"{prefix} {title}\n\n{body}\n\n時間：{time.strftime('%Y-%m-%d %H:%M:%S')}\n事件：{event}"

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

    lines = [
        f"【轉運站】每日摘要（近 {days} 天）",
        "",
        f"進站瀏覽：{s['page_views']}    不重複訪客：{s['visitors']}（新 {s['new_visitors']}／回訪 {s['returning_visitors']}）",
        f"解析：{s['resolve_total']}（成功 {s['resolve_ok']}／失敗 {s['resolve_fail']}，成功率 "
        f"{s['success_rate'] if s['success_rate'] is not None else '–'}%）",
        f"下載：{s['downloads']} 次    傳輸：{s['transfers']} 次",
        f"收益：US$ {s['revenue']}",
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
    return "【轉運站】每日摘要", "\n".join(lines)


async def send_digest(days: int = 1) -> dict:
    subject, body = build_digest(days)
    ok, note = await asyncio.to_thread(_send_sync, subject, body)
    _log(subject, body, ok, note, "digest")
    return {"ok": ok, "note": note, "to": _recipients(), "transport": transport()}
