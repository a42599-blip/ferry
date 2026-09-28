"""前台／後台自動審核員。

小羅 2026-09-27 要求：
> 「從每一頁面的第一個選項、第一個字母去看它翻譯成功了沒，
>   然後從左到右、從上到下一排一排的檢查它⋯去一個一個去看、去按、去用，
>   看看它是不是有這個功能，還是只是一個虛的；
>   那翻譯有沒有成功、圖標對不對 —— 這才是審查員該做的事。」

用法：
    python tools/audit_frontend.py                      # 前台 × 3 語言 × 5 頁 ＋ 功能實測
    python tools/audit_frontend.py --admin              # 連後台一起審
    python tools/audit_frontend.py --base https://scefo.com
    python tools/audit_frontend.py --json report.json   # 輸出機器可讀結果

檢查項目：
  A. 版面：破圖、翻譯殘留、空按鈕、空連結（依左→右、上→下排序）
  B. 功能：真的按下去，看是不是「虛的」
  C. 後台：每頁有資料、開關真的能切
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CJK = re.compile(r"[\u4e00-\u9fff]")
TRAD_ONLY = set("這個們來時間錄轉運臺設計對為與開關點擊資訊檢語顯選檔傳輸連線網頁應該裡發錯誤載體驗證說數據總覽監統報員裝訪國瀏學習幫嗎麼樣種樂愛買賣價錢銀隊舊藝術產業務動態圖標額隨機專導頻廣週邊幾讓認記憶實現環節複雜簡確準備測試結論續斷緣車輛條處帳號燈碼聯絡繫歷紀類稱讀寫刪儲壓縮畫質項暫餘將費會終訂閱敗趨勢長區組維護庫匯機")
SIMP_ONLY = set("这个们来时间录转运设计对为与开关点击资询检语显选档传输连线网应里发错误载体验证说数据总览监统报员装访国浏学习帮吗么样种乐爱买卖价钱银队旧艺产务动态图标额随机专导频广周边几让认记忆实现环节复杂简确准准备测试结论续断缘车辆条处帐号灯码联络历纪录类称读写删储压缩项暂余将费会终订阅败趋势长区组维护库汇机显")

LANG_RULES = {
    "zh-Hant": ("繁中", lambda s: sorted({c for c in s if c in SIMP_ONLY})),
    "zh-Hans": ("簡中", lambda s: sorted({c for c in s if c in TRAD_ONLY})),
    "en": ("英文", lambda s: sorted({c for c in s if CJK.search(c)})),
}

# 從頁面抓出「可見元素」，依 y（上→下）再 x（左→右）排序
COLLECT_JS = """
() => {
  const out = [];
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && st.visibility !== 'hidden'
           && st.display !== 'none' && Number(st.opacity) > 0.05;
  };
  const txt = (el) => (el.getAttribute('placeholder') || el.textContent
      || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 90);
  const sel = 'button,input,select,img,a,textarea,h1,h2,h3,.lbl,.pi i,.metric span,.hrow .t';
  document.querySelectorAll(sel).forEach((el) => {
    if (!visible(el)) return;
    const r = el.getBoundingClientRect();
    const tag = el.tagName.toLowerCase();
    const it = {
      tag, text: txt(el),
      x: Math.round(r.left), y: Math.round(r.top + window.scrollY),
      i18n: el.dataset ? (el.dataset.i18n || '') : '',
      id: el.id || '', cls: String(el.className || '').slice(0, 40),
    };
    if (tag === 'img') {
      it.src = el.getAttribute('src') || '';
      it.loaded = el.complete && el.naturalWidth > 0;
      it.natural = el.naturalWidth + 'x' + el.naturalHeight;
    }
    if (tag === 'a') it.href = el.getAttribute('href') || '';
    if (tag === 'input') { it.type = el.type; it.placeholder = el.placeholder || ''; }
    if (tag === 'button' || tag === 'input') it.disabled = el.disabled;
    out.push(it);
  });
  out.sort((a, b) => (Math.abs(a.y - b.y) > 4 ? a.y - b.y : a.x - b.x));
  return out;
}
"""


# ══ A. 版面審核 ══════════════════════════════════════
async def audit_page(page, lang: str, tab: str) -> list[dict]:
    issues: list[dict] = []
    lang_name, bad_chars = LANG_RULES[lang]

    try:
        await page.locator(f'#lang button[data-lang="{lang}"]:visible').first.click(timeout=2500)
        await page.wait_for_timeout(600)
    except Exception:  # noqa: BLE001
        pass
    try:
        await page.locator(f'[data-tab="{tab}"]:visible').first.click(timeout=2500)
        await page.wait_for_timeout(600)
    except Exception:  # noqa: BLE001
        pass

    for it in await page.evaluate(COLLECT_JS):
        pos = f"y{it['y']:>4} x{it['x']:>4}"
        if it["tag"] == "img" and not it.get("loaded"):
            issues.append({"lang": lang_name, "tab": tab, "pos": pos, "kind": "破圖",
                           "detail": f"{it.get('src', '')[:60]} ({it.get('natural')})"})
        bad = bad_chars(it.get("text") or "")
        if bad:
            is_ui = (it["tag"] in ("button", "input", "select", "h1", "h2", "h3", "textarea")
                     or ".lbl" in it["cls"] or "lbl" in it["cls"])
            if lang == "en" or is_ui:
                issues.append({"lang": lang_name, "tab": tab, "pos": pos, "kind": "翻譯問題",
                               "detail": f"{''.join(bad)} ← {it['text'][:60]}"})
        if it["tag"] == "button" and not it["text"] and not it.get("i18n"):
            issues.append({"lang": lang_name, "tab": tab, "pos": pos, "kind": "空按鈕",
                           "detail": f"id={it['id']} class={it['cls']}"})
        if it["tag"] == "a" and it.get("href") in ("#", ""):
            issues.append({"lang": lang_name, "tab": tab, "pos": pos, "kind": "空連結",
                           "detail": it["text"][:40]})
    return issues


async def audit_layout(pg, base: str) -> list[dict]:
    out: list[dict] = []
    await pg.goto(base, wait_until="domcontentloaded", timeout=45000)
    await pg.wait_for_timeout(1800)
    for lang in ("zh-Hant", "zh-Hans", "en"):
        for tab in ("download", "transfer", "teach", "plans", "member"):
            out.extend(await audit_page(pg, lang, tab))
    # 解析後再看一次結果區
    await pg.locator('#lang button[data-lang="zh-Hant"]:visible').first.click()
    await pg.locator('[data-tab="download"]:visible').first.click()
    try:
        await pg.fill("#url", "https://www.bilibili.com/video/BV1xx411c7mD")
        await pg.click("#go")
        await pg.wait_for_timeout(9000)
    except Exception:  # noqa: BLE001
        pass
    for lang in ("zh-Hant", "zh-Hans", "en"):
        iss = await audit_page(pg, lang, "download")
        out.extend([{**i, "tab": "download(已解析)"} for i in iss])
    return out


# ══ B. 功能審核（真的按下去）══════════════════════════
async def audit_functional(pg, base: str) -> list[dict]:
    out: list[dict] = []

    def bad(name, detail):
        out.append({"lang": "功能", "tab": "smoke", "pos": "——",
                    "kind": "功能無效", "detail": f"{name}：{detail}"})

    def good(name):
        print(f"   ✔ {name}")

    await pg.goto(base, wait_until="domcontentloaded", timeout=45000)
    await pg.wait_for_timeout(1800)

    # 1) 語言切換真的換字
    #    ⚠️ 2026-09-27 起改成兩顆固定按鈕（照 v8i8）：
    #       「📋 貼上」＋「🔍 開始解析」→ 兩顆都要翻譯到、且英文版不得有中文
    #    按鈕（小羅 2026-09-27 定案）：
    #      點網址欄 → 出現「📋 貼上」；「🔍 解析」常駐（沒改連結時可再按）
    ALLOW = {
        "en": {"🔍 Parse", "Parse"},
        "zh-Hans": {"🔍 解析", "解析"},
        "zh-Hant": {"🔍 解析", "解析"},
    }
    for lang, expects in ALLOW.items():
        await pg.locator(f'#lang button[data-lang="{lang}"]:visible').first.click()
        await pg.wait_for_timeout(600)
        got = (await pg.inner_text("#go")).strip()
        if got not in expects:
            bad("語言切換", f"{lang} 期望 {sorted(expects)} 得到「{got}」")
        elif lang == "en" and re.search(r"[一-鿿]", got):
            bad("語言切換", f"英文版不該出現中文：「{got}」")
        else:
            good(f"語言切換 → {lang}（{got}）")

    # 2) 解析 ＋ 3) 選畫質後下載鈕可用
    await pg.locator('[data-tab="download"]:visible').first.click()
    await pg.fill("#url", "https://www.bilibili.com/video/BV1xx411c7mD")
    await pg.click("#go")
    await pg.wait_for_timeout(9000)
    n = await pg.locator("#qs .q").count()
    if n == 0:
        bad("解析", "沒有產生任何畫質選項")
    else:
        good(f"解析 → {n} 個畫質")
        await pg.locator("#qs .q").first.click()
        await pg.wait_for_timeout(400)
        if await pg.get_attribute("#download", "disabled") is not None:
            bad("選擇畫質後下載鈕", "仍然是 disabled")
        else:
            good("選畫質 → 下載鈕可用")

    # 4) 產生配對碼
    await pg.locator('[data-tab="transfer"]:visible').first.click()
    await pg.wait_for_timeout(600)
    await pg.click("#tr-gen")
    await pg.wait_for_timeout(2500)
    code = (await pg.inner_text("#mycode")).strip()
    if not re.fullmatch(r"\d{6}", code):
        bad("產生配對碼", f"拿到「{code}」不是 6 位數字")
    else:
        good(f"產生配對碼 → {code}")

    # 5) 會員註冊
    await pg.locator('[data-tab="member"]:visible').first.click()
    await pg.wait_for_timeout(600)
    mail = f"audit{int(time.time())}@ferry.local"
    await pg.fill("#m-email", mail)
    await pg.fill("#m-pass", "secret123")
    await pg.click("#m-register")
    await pg.wait_for_timeout(2500)
    if await pg.is_visible("#m-info"):
        good(f"會員註冊 → {mail}")
    else:
        bad("會員註冊", (await pg.inner_text("#m-msg"))[:50])

    # 6) 方案價格
    await pg.locator('[data-tab="plans"]:visible').first.click()
    await pg.wait_for_timeout(1500)
    price = (await pg.inner_text('[data-price="monthly"]')).strip()
    if not re.match(r"^[\d.]+$", price):
        bad("方案價格", f"拿到「{price}」")
    else:
        good(f"方案價格 → US$ {price}")

    # 7) 教學頁內容
    await pg.locator('[data-tab="teach"]:visible').first.click()
    await pg.wait_for_timeout(800)
    steps = await pg.locator("#teach-steps-dl li").count()
    faq = await pg.locator("#teach-faq .r").count()
    save = await pg.locator("#teach-save-list .r").count()
    if steps < 2 or faq < 3 or save < 3:
        bad("教學頁內容", f"步驟 {steps}、FAQ {faq}、存檔說明 {save}")
    else:
        good(f"教學頁 → 步驟 {steps}、FAQ {faq}、存檔說明 {save}")

    # 8) 平台圖示
    imgs = await pg.evaluate("""() => [...document.querySelectorAll('#plats img')]
        .map(i => ({src: i.getAttribute('src'), ok: i.complete && i.naturalWidth > 0}))""")
    broken = [i["src"] for i in imgs if not i["ok"]]
    if broken:
        bad("平台圖示", f"{len(broken)} 個破圖：{broken[:3]}")
    else:
        good(f"平台圖示 → {len(imgs)} 個全部載入")
    return out


# ══ C. 後台審核 ══════════════════════════════════════
async def audit_admin(base: str, user: str, password: str) -> list[dict]:
    from playwright.async_api import async_playwright

    out: list[dict] = []

    def bad(name, detail):
        out.append({"lang": "後台", "tab": "admin", "pos": "——",
                    "kind": "後台問題", "detail": f"{name}：{detail}"})

    async with async_playwright() as p:
        br = await p.chromium.launch(headless=True, channel="chromium", args=["--no-sandbox"])
        ctx = await br.new_context(viewport={"width": 1440, "height": 900}, locale="zh-TW")
        pg = await ctx.new_page()
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(base.rstrip("/") + "/admin/", wait_until="domcontentloaded", timeout=45000)
        await pg.wait_for_timeout(1500)
        await pg.fill("#l-user", user)
        await pg.fill("#l-pass", password)
        await pg.click("#login-form button[type=submit]")
        await pg.wait_for_timeout(2500)

        for name in ("overview", "growth", "flags", "devices", "revenue", "errors", "system"):
            try:
                await pg.locator(f'#side button[data-p="{name}"]:visible').first.click(timeout=4000)
                await pg.wait_for_timeout(1400)
                txt = await pg.inner_text(f"#pg-{name}")
                if len(txt.strip()) < 20:
                    bad(f"後台 {name} 頁", f"內容只有 {len(txt.strip())} 字（可能是空的）")
                else:
                    print(f"   ✔ 後台 {name} 頁（{len(txt)} 字）")
            except Exception as e:  # noqa: BLE001
                bad(f"後台 {name} 頁", str(e)[:60])

        await pg.locator('#side button[data-p="flags"]:visible').first.click()
        await pg.wait_for_timeout(1500)
        for label, sel in (("平台開關", '#platform-list .toggle[data-platform="tiktok"]'),
                           ("功能開關", '#feature-list .toggle[data-feature="feature.quality"]')):
            try:
                a = await pg.evaluate(f"() => document.querySelector('{sel}').classList.contains('on')")
                await pg.click(sel)
                await pg.wait_for_timeout(1200)
                b = await pg.evaluate(f"() => document.querySelector('{sel}').classList.contains('on')")
                await pg.click(sel)
                await pg.wait_for_timeout(1200)
                c = await pg.evaluate(f"() => document.querySelector('{sel}').classList.contains('on')")
                if a == b or c != a:
                    bad(label, f"{a} → {b} → {c}")
                else:
                    print(f"   ✔ {label}（{a} → {b} → {c}）")
            except Exception as e:  # noqa: BLE001
                bad(label, str(e)[:60])

        # 清掉審核過程建立的測試帳號
        try:
            n = await pg.evaluate("""async () => {
              const t = localStorage.getItem('fy_admin_token');
              const r = await fetch('/admin/api/members/cleanup-test', {method:'POST',
                headers:{Authorization:'Bearer '+t}});
              return (await r.json()).deleted;
            }""")
            if n:
                print(f"   ✔ 已清除 {n} 個審核用測試帳號")
        except Exception:  # noqa: BLE001
            pass

        if errs:
            bad("後台 JS 錯誤", "；".join(errs[:2])[:100])
        await br.close()
    return out


# ══ D. 網址路由審核（哪個連結該由哪個平台處理）══════════
ROUTING_CASES = [
    ("https://www.douyin.com/video/1234567890123456", "douyin"),
    ("https://v.douyin.com/abc123/", "douyin"),
    ("https://www.iesdouyin.com/share/video/1234567890123456/", "douyin"),
    ("https://www.iesdouyin.com/xg/video/1234567890123456", "xigua"),
    ("https://www.ixigua.com/1234567890123456", "xigua"),
    ("https://www.tiktok.com/@a/video/123", "tiktok"),
    ("https://vm.tiktok.com/ZMabc/", "tiktok"),
    ("https://www.bilibili.com/video/BV1xx411c7mD", "bilibili"),
    ("https://b23.tv/abc", "bilibili"),
    ("https://weibo.com/detail/5347454541103635", "weibo"),
    ("https://m.weibo.cn/detail/5347454541103635", "weibo"),
    ("https://x.com/a/status/123", "x"),
    ("https://twitter.com/a/status/123", "x"),
    ("https://www.youtube.com/watch?v=abc", "youtube"),
    ("https://youtu.be/abc", "youtube"),
    ("https://www.instagram.com/reel/abc/", "instagram"),
    ("https://www.facebook.com/watch/?v=1", "facebook"),
    ("https://www.threads.com/@a/post/abc", "threads"),
    ("https://www.xiaohongshu.com/explore/abc", "xiaohongshu"),
    ("https://xhslink.com/abc", "xiaohongshu"),
    ("https://shopee.tw/product/1/2", "shopee"),
    ("https://www.toutiao.com/video/123/", "toutiao"),
]


async def audit_routing() -> list[dict]:
    """確認每個網址都交給正確的平台（避免 A 平台的連結被 B 平台吃掉）。"""
    from app.core import registry

    out: list[dict] = []
    for url, want in ROUTING_CASES:
        r = await registry.detect(url)
        got = r.name if r else None
        if got != want:
            out.append({"lang": "路由", "tab": "registry", "pos": "——",
                        "kind": "平台誤判", "detail": f"{url[:56]} → {got}（期望 {want}）"})
    if not out:
        print(f"   ✔ 網址路由 {len(ROUTING_CASES)} 項全部正確")
    return out


# ══ 報告 ════════════════════════════════════════════
def print_report(rep: dict) -> None:
    print("\n" + "=" * 78)
    print(f"📋 審核報告 — {rep['base']}")
    print("=" * 78)
    print(f"版面檢查 {rep['checked']} 個頁面組合\n")
    if rep.get("js_errors"):
        print("🔴 前台 JS 錯誤：")
        for e in rep["js_errors"]:
            print("   ", e[:110])
        print()
    by_kind: dict[str, list] = {}
    for i in rep["issues"]:
        by_kind.setdefault(i["kind"], []).append(i)
    if not by_kind:
        print("✅ 沒有發現問題")
    for kind, items in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        print(f"── {kind}（{len(items)} 項）")
        for i in items[:25]:
            print(f"   [{i['lang']}/{i['tab']}] {i['pos']}  {i['detail'][:78]}")
        if len(items) > 25:
            print(f"   …還有 {len(items) - 25} 項")
        print()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8800")
    ap.add_argument("--admin", action="store_true")
    ap.add_argument("--json", default="")
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--admin-pass", default="ferry-admin")
    ap.add_argument("--skip-functional", action="store_true")
    args = ap.parse_args()

    from playwright.async_api import async_playwright

    rep: dict = {"base": args.base, "checked": 0, "issues": [], "js_errors": []}
    async with async_playwright() as p:
        br = await p.chromium.launch(headless=True, channel="chromium",
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await br.new_context(viewport={"width": 430, "height": 932}, locale="zh-TW")
        pg = await ctx.new_page()
        pg.on("pageerror", lambda e: rep["js_errors"].append(str(e)))

        print("▶ A. 版面審核（破圖／翻譯／空按鈕／空連結）")
        rep["issues"] += await audit_layout(pg, args.base)
        rep["checked"] = 15

        if not args.skip_functional:
            print("\n▶ B. 功能審核（真的按下去看是不是虛的）")
            rep["issues"] += await audit_functional(pg, args.base)
        await br.close()

    if args.admin:
        print("\n▶ C. 後台審核")
        rep["issues"] += await audit_admin(args.base, args.admin_user, args.admin_pass)

    print("\n▶ D. 網址路由審核（哪個連結該由哪個平台處理）")
    rep["issues"] += await audit_routing()

    # E. 電腦版 vs 手機版「功能對等」（小羅 2026-09-27：
    #    「電腦版有的所有功能，手機版都要有，兩邊是同步，只是顯示適配設備」）
    print("\n▶ E. 功能對等審核（電腦版 vs 手機版）")
    rep["issues"] += await audit_parity(args.base)

    print_report(rep)
    if args.json:
        Path(args.json).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"（結果已寫入 {args.json}）")


# ── E. 電腦版 vs 手機版「功能對等」檢查 ─────────────────────
#  小羅 2026-09-27：「你要確保電腦版有的所有功能，手機版都要有，
#                    兩邊是同步，只是顯示方式適配設備。」
async def audit_parity(base: str) -> list[str]:
    """回傳問題清單（給主流程加總）。"""
    issues: list[str] = []

    def bad_(_m): issues.append(f"[功能對等] {_m}")
    def good_(_m): pass

    from playwright.async_api import async_playwright

    CHECKS = [
        ("無水印下載", "#url"), ("貼上鈕", "#paste"), ("解析鈕", "#go"),
        ("下載鈕", "#download"), ("畫質欄", "#qs"), ("平台圖示", "#plats"),
        ("今日次數", "#q-left"), ("歷史記錄", "#history-box"),
        ("回報問題", "#report-box"), ("回報送出", "#rp-send"),
        ("無損傳輸頁", "#p-transfer"), ("傳輸配對鈕", "#tr-gen"),
        ("選檔鈕", "#pick-files"), ("傳送鈕", "#tr-send"),
        ("教學頁", "#p-teach"), ("方案頁", "#p-plans"), ("會員頁", "#p-member"),
        ("語言切換", "#lang"), ("五個分頁", "#tabs"),
    ]
    async with async_playwright() as p:
        br = await p.chromium.launch(headless=True, channel="chromium",
                                     args=["--no-sandbox"])
        results: dict[str, set] = {}
        for label, vp, mobile in (("desktop", {"width": 1440, "height": 900}, False),
                                  ("mobile", {"width": 390, "height": 844}, True)):
            ctx = await br.new_context(viewport=vp, is_mobile=mobile, has_touch=mobile,
                                       locale="zh-TW")
            pg = await ctx.new_page()
            await pg.goto(base, wait_until="domcontentloaded", timeout=45000)
            await pg.wait_for_timeout(2500)
            present = set()
            for name, sel in CHECKS:
                if await pg.locator(sel).count():
                    present.add(name)
            results[label] = present
            await ctx.close()
        await br.close()

    only_desktop = results["desktop"] - results["mobile"]
    only_mobile = results["mobile"] - results["desktop"]
    if only_desktop:
        issues.append(f"[功能對等] 手機版缺少：{sorted(only_desktop)}")
    if only_mobile:
        issues.append(f"[功能對等] 電腦版缺少：{sorted(only_mobile)}")
    if not only_desktop and not only_mobile:
        print(f"   ✔ 電腦版＝手機版（{len(results['desktop'])} 項功能兩邊都在）")
    return issues


if __name__ == "__main__":
    asyncio.run(main())
