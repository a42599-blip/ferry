# -*- coding: utf-8 -*-
"""前後台 ＋ 資料庫「聯動」檢查。

小羅 2026-09-27：「你所有的修改都要去檢查後台跟前台或者是資料庫裡面的聯動，
有沒有聯動起來。」

為什麼需要這個工具：
    後台看得到、前台卻沒反應（或反過來）—— 這種「沒有聯動」的錯最難自己發現。
    這個工具會「從前台做一個動作 → 去後台查有沒有出現」，
    再把「後台做一個動作 → 去前台查有沒有出現」，兩邊都驗。

用法：
    python tools/check_linkage.py                     # 測本機 8800
    python tools/check_linkage.py --base https://ferry.v8i8.com \
        --admin-pass admin
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36")

ok_count = 0
bad_count = 0


def good(msg: str) -> None:
    global ok_count
    ok_count += 1
    print(f"   ✔ {msg}")


def bad(msg: str) -> None:
    global bad_count
    bad_count += 1
    print(f"   ✘ {msg}")


def http(base: str, path: str, *, method: str = "GET", body=None,
         headers: dict | None = None, timeout: int = 60):
    """送一個請求，回傳 (狀態碼, 解析後的內容)。"""
    h = {"User-Agent": UA, "Accept": "application/json", "Origin": base,
         "Referer": base + "/"}
    if body is not None:
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "ignore")
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, raw
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8800")
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--admin-pass", default="ferry-admin")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print("=" * 72)
    print(f"  前後台＋資料庫 聯動檢查 — {base}")
    print("=" * 72)

    # 登入後台
    code, j = http(base, "/admin/api/login", method="POST",
                   body={"user": args.admin_user, "password": args.admin_pass})
    tok = (j or {}).get("token") if isinstance(j, dict) else None
    if not tok:
        print(f"   ✘ 後台登入失敗（HTTP {code}）：{str(j)[:120]}")
        return 1
    A = {"Authorization": "Bearer " + tok}
    good("後台登入成功")

    dev = "dev_link_" + secrets.token_hex(4)
    D = {"X-Device-Id": dev, "X-Timezone": "Asia/Taipei", "cf-ipcountry": "TW"}
    stamp = str(int(time.time()))[-5:]

    # ── ① 前台註冊 → 後台會員清單 ──────────────────────────
    print("\n▶ ① 前台註冊會員 → 後台看得到？")
    email = f"link{stamp}@example.com"
    code, r = http(base, "/api/member/register", method="POST",
                   body={"email": email, "password": "linktest123"}, headers=D)
    if not (isinstance(r, dict) and r.get("ok")):
        bad(f"前台註冊失敗（HTTP {code}）：{str(r)[:110]}")
    else:
        good(f"前台註冊成功（{email}）")
        code, lst = http(base, "/admin/api/members/list?q=" + urllib.parse.quote(email), headers=A)
        rows = (lst or {}).get("rows") or []
        if rows:
            good("後台會員清單查得到這位新會員")
            m = rows[0]
            mid = m["id"]
            # 註冊就算第一次登入（小羅抓到的那個問題）
            if m.get("login_count"):
                good(f"登入次數正確（{m['login_count']} 次；註冊＝第一次登入）")
            else:
                bad("登入次數是 0（註冊應該算一次登入）")
            if m.get("last_login_at"):
                good("最後登入時間有記錄")
            else:
                bad("最後登入時間空白")
            if m.get("country"):
                good(f"地區有記錄（{m['country']}）")
            else:
                bad("地區沒記錄（註冊時應帶 cf-ipcountry）")
            # 資料庫層：直接看 plan_history 有沒有 signup
            code, card = http(base, f"/admin/api/members/{mid}/card", headers=A)
            hist = ((card or {}).get("card") or {}).get("history") or []
            if any(h.get("reason") == "signup" for h in hist):
                good("方案歷史有寫入「註冊」那一筆")
            else:
                bad("方案歷史沒有 signup 紀錄")
        else:
            bad("後台會員清單查不到剛註冊的會員（前後台沒聯動）")
            mid = None

    # ── ② 後台加天數／次數 → 前台該看到 ────────────────────
    if mid:
        print("\n▶ ② 後台手動加天數/次數 → 資料庫有寫入？")
        # 先讓這位會員「用掉一次」下載額度，才驗得出「加次數」有沒有生效
        http(base, "/api/resolve", method="POST",
             body={"url": "https://vt.tiktok.com/ZSb2uY5Wx/"},
             headers={**D, "X-Member-Token": ""}, timeout=120)
        code, r = http(base, f"/admin/api/members/{mid}/adjust", method="POST",
                       body={"days": 3, "quota_download": 7, "note": "聯動測試"}, headers=A)
        done = (r or {}).get("done") or []
        if done:
            good(f"後台調整成功（{ '、'.join(done) }）")
            card = ((r or {}).get("card") or {})
            if (card.get("remaining_days") or 0) > 0:
                good(f"剩餘天數已變成 {card['remaining_days']} 天")
            else:
                bad("加天數後剩餘天數沒有變化")
            # ⚠️ 加次數＝把「已用」減掉，可以變負數（贈送），所以剩餘會**超過**上限。
            #    小羅 2026-09-27：「我加 2 次他應該要有 7 次。」
            qd = (card.get("quota") or {}).get("download") or {}
            used, lim = qd.get("used") or 0, qd.get("limit") or 0
            if used < 0 or (qd.get("remaining") or 0) > lim:
                good(f"次數已補回並超過上限（已用 {used} / 上限 {lim}，剩 "
                     f"{qd.get('remaining')}）")
            else:
                bad(f"加次數後沒有補回（已用 {used} / 上限 {lim}）")
        else:
            bad(f"後台調整失敗：{str(r)[:110]}")

    # ── ③ 前台解析 → 後台統計 ──────────────────────────────
    print("\n▶ ③ 前台解析 → 後台統計/排行榜看得到？")
    code, before = http(base, "/admin/api/overview?days=1", headers=A)
    n0 = ((before or {}).get("summary") or {}).get("resolve_total", 0)
    code, r = http(base, "/api/resolve", method="POST",
                   body={"url": "https://vt.tiktok.com/ZSb2uY5Wx/"}, headers=D, timeout=120)
    if isinstance(r, dict) and r.get("ok"):
        good("前台解析成功")
        code, after = http(base, "/admin/api/overview?days=1", headers=A)
        n1 = ((after or {}).get("summary") or {}).get("resolve_total", 0)
        if n1 > n0:
            good(f"後台解析次數有增加（{n0} → {n1}）")
        else:
            bad(f"後台解析次數沒變（{n0} → {n1}）—— 事件沒有寫進資料庫")
    else:
        bad(f"前台解析失敗（可能平台風控）：{str(r)[:90]}")

    # ── ④ 前台回報 → 後台客戶回報頁 ────────────────────────
    print("\n▶ ④ 前台回報問題 → 後台「客戶回報」看得到？")
    msg = f"聯動測試回報 {stamp}：抖音不能下載"
    code, r = http(base, "/api/report", method="POST",
                   body={"message": msg, "contact": "link@test", "platform": "douyin"}, headers=D)
    fb_id = None
    if isinstance(r, dict) and r.get("ok"):
        code, fb = http(base, "/admin/api/feedback?days=7&only_new=false", headers=A)
        for x in ((fb or {}).get("rows") or []):
            if msg[:20] in (x.get("message") or ""):
                fb_id = x.get("id")
        found = any(msg[:20] in (x.get("message") or "") for x in ((fb or {}).get("rows") or []))
        if found:
            good("後台「客戶回報」看得到這則回報")
        else:
            bad("後台看不到剛送出的回報（前後台沒聯動）")
    else:
        bad(f"前台回報失敗：{str(r)[:90]}")

    # ── ⑤ 後台發布公告 → 前台看得到 ────────────────────────
    print("\n▶ ⑤ 後台發布公告 → 前台看得到？")
    title = f"聯動測試公告 {stamp}"
    code, r = http(base, "/admin/api/announcements", method="POST",
                   body={"title": title, "body": "這是聯動測試用的公告", "level": "warn"}, headers=A)
    if isinstance(r, dict) and r.get("ok"):
        code, pj = http(base, "/api/announcements")
        found = any(title in (x.get("title") or "") for x in ((pj or {}).get("items") or []))
        if found:
            good("前台公告 API 看得到這則公告（後台→前台 有聯動）")
        else:
            bad("前台看不到剛發布的公告（後台→前台 沒聯動）")
        # 收尾：刪掉測試公告
        items = (r.get("items") or [])
        if items:
            http(base, f"/admin/api/announcements/{items[0]['id']}", method="DELETE", headers=A)
    else:
        bad(f"後台發布公告失敗：{str(r)[:90]}")

    # ── ⑥ 後台開關 → 前台配置 ──────────────────────────────
    print("\n▶ ⑥ 後台功能開關 → 前台 /api/config 有聯動？")
    code, cfg = http(base, "/api/config")
    feats = (cfg or {}).get("features") or {}
    if "feature.free_limit_download" in feats and "feature.free_limit_transfer" in feats:
        good("前台拿得到兩個免費次數開關（下載／傳輸分開）")
    else:
        bad("前台拿不到免費次數開關（後台設定沒傳到前台）")

    # ── ⑦ 前台次數 → 後台次數管理 ──────────────────────────
    print("\n▶ ⑦ 前台用掉次數 → 後台「次數管理」看得到？")
    code, q = http(base, "/admin/api/quota", headers=A)
    rows = (q or {}).get("rows") or []
    if rows:
        good(f"後台次數管理有 {len(rows)} 筆今天的用量")
    else:
        bad("後台次數管理是空的（前台用量沒寫進資料庫）")

    print("\n" + "=" * 72)
    print(f"  聯動檢查結果：通過 {ok_count} 項，失敗 {bad_count} 項")
    print("=" * 72)
    return 0 if bad_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
