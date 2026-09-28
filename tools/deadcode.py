"""廢碼掃描器（清乾淨、保持優質代碼）。

小羅 2026-09-27 強調：
> 「每次修一個 bug 或改一段代碼，廢掉、壞掉、沒用的代碼一定要刪掉，
>   不要殘留 —— 否則最後代碼會亂七八糟，動一個壞另一個，查也查不到。」

這支工具就是用來**當下檢查有沒有殘留**。檢查 5 類：

  ① Python：未使用的 import
  ② Python：定義了但沒人呼叫的函式
  ③ JS：`$('#id')` / `getElementById('id')` 引用了**不存在的 HTML id**
     ⚠️ 這一類最致命 —— 會讓整支 JS 拋錯、功能全掛（實際發生過）
  ④ JS：定義了但沒用到的函式
  ⑤ CSS：沒有對應 HTML/JS 的 class

用法：
    python tools/deadcode.py            # 只報告
    python tools/deadcode.py --strict   # 有問題就 exit 1（可掛進流程）
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "data", "_封存"}


def _walk(exts: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            if f.endswith(exts):
                out.append(Path(dirpath) / f)
    return out


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ── ① ② Python ────────────────────────────────────────
def check_python() -> list[str]:
    issues: list[str] = []
    all_src = "\n".join(read(p) for p in _walk((".py",)))
    # 前端／測試也會呼叫後端函式 → 一起算進去
    all_src += "\n".join(read(p) for p in _walk((".js", ".py")) if "tools" in str(p))

    for p in _walk((".py",)):
        if "tools" in str(p) or p.name == "deadcode.py":
            continue
        src = read(p)
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            issues.append(f"[語法] {p.relative_to(ROOT)}:{e.lineno} {e.msg}")
            continue

        # ① 未使用 import
        imported: dict[str, int] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    imported[(a.asname or a.name).split(".")[0]] = n.lineno
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    if a.name not in ("*", "annotations"):
                        imported[a.asname or a.name] = n.lineno
        used: set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                used.add(n.id)
            elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                used.add(n.value.id)
        for name, line in imported.items():
            if name not in used:
                issues.append(f"[未使用 import] {p.relative_to(ROOT)}:{line} → {name}")

        # ② 沒人呼叫的函式（FastAPI 路由／魔法方法不算）
        for n in tree.body:
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if n.name.startswith("_") or n.name in ("main",):
                continue
            # 有 decorator（FastAPI 路由）→ 跳過
            if n.decorator_list:
                continue
            cnt = len(re.findall(r"\b" + re.escape(n.name) + r"\b", all_src))
            if cnt <= 1:
                issues.append(f"[死函式] {p.relative_to(ROOT)}:{n.lineno} → {n.name}()")
    return issues


# ── ③ ④ JavaScript ────────────────────────────────────
ID_RE = re.compile(r"""\$\(\s*['"]#([A-Za-z0-9_-]+)['"]\s*\)|getElementById\(\s*['"]([A-Za-z0-9_-]+)['"]\s*\)""")


def check_js(html_ids: set[str]) -> list[str]:
    issues: list[str] = []
    js_files = [p for p in _walk((".js",)) if "sw.js" not in p.name]
    all_js = "\n".join(read(p) for p in js_files)

    # JS 用樣板字串「動態產生」的 id 也要算
    # （例：innerHTML = `<input id="adj-days">`）—— 否則會誤判成「DOM id 不存在」
    js_defined_ids = set(re.findall(r'''id=["']([A-Za-z0-9_-]+)["']''', all_js))
    known = set(html_ids) | js_defined_ids

    for p in js_files:
        src = read(p)
        # ③ 引用了不存在的 id（最致命）
        for m in ID_RE.finditer(src):
            dom_id = m.group(1) or m.group(2)
            if dom_id in ("lang",):          # 動態建立的不算
                continue
            if dom_id not in known:
                line = src[:m.start()].count("\n") + 1
                issues.append(f"[DOM id 不存在] {p.relative_to(ROOT)}:{line} → #{dom_id}（HTML 裡沒有，會讓 JS 掛掉）")

        # ④ 沒用到的函式
        for fn in re.findall(r"^(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)", src, re.M):
            if fn in ("init", "main"):
                continue
            if len(re.findall(r"\b" + re.escape(fn) + r"\b", all_js)) <= 1:
                issues.append(f"[未使用函式] {p.relative_to(ROOT)} → {fn}()")
    return issues


def _walk_locales() -> list[Path]:
    """static/locales/*.json（三語翻譯檔）。"""
    return sorted(Path(ROOT, "static", "locales").glob("*.json"))


def check_i18n(html_text: str, js_text: str) -> list[str]:
    """三語翻譯檢查（小羅 2026-09-29：新文字一定要有繁／簡／EN）。

    ① 三份語言檔的 key 必須一致（缺漏／多出都要報）
    ② 程式裡用到、語言檔卻沒有的 key 也要報
       ⚠️ `t('tier_' + x)` 這種動態組合會留下 `tier_` 尾巴 → 忽略以 `_` 結尾的
    """
    import json as _json

    issues: list[str] = []
    files = _walk_locales()
    if not files:
        return issues
    base_name = "zh-Hant.json"
    data: dict[str, dict] = {}
    for q in files:
        try:
            data[q.name] = _json.loads(read(q))
        except Exception as exc:  # noqa: BLE001
            issues.append(f"[翻譯] {q.name} 讀取失敗：{str(exc)[:60]}")
    base = data.get(base_name)
    if base is None:
        issues.append(f"[翻譯] 找不到基準語言檔 {base_name}")
        return issues
    for name, d in data.items():
        if name == base_name:
            continue
        for k in sorted(base):
            if k not in d:
                issues.append(f"[翻譯] {name} 缺少 key：{k}")
        for k in sorted(d):
            if k not in base:
                issues.append(f"[翻譯] {name} 多出 key（基準檔沒有）：{k}")

    used = set(re.findall(r'data-i18n(?:-html|-ph)?="([a-zA-Z0-9_.]+)"', html_text))
    used |= set(re.findall(r"\bt\(\s*[\"']([a-zA-Z0-9_]+)[\"']", js_text))
    for k in sorted(used):
        if k.endswith("_"):          # 動態組合（tier_ / tab_ 之類）
            continue
        if k not in base:
            issues.append(f"[翻譯] 程式用到但語言檔沒有：{k}")
    return issues


# ── ⑤ CSS ─────────────────────────────────────────────
def check_css(html_text: str, js_text: str) -> list[str]:
    issues: list[str] = []
    # 語言檔裡也可能夾 HTML（例：<b class="num-hl">3</b> 標紅數字）
    #   → 不算進來的話，.num-hl 會被誤判成「沒用到的 CSS」（2026-09-29 實際踩到）
    locales = "\n".join(read(q) for q in _walk_locales())
    for p in _walk((".css",)):
        css = read(p)
        for cls in sorted(set(re.findall(r"\.([a-zA-Z][a-zA-Z0-9_-]{2,})", css))):
            if cls in html_text or cls in js_text or cls in locales:
                continue
            # 常見的狀態 class 由 JS 動態加，寬鬆處理
            if cls in ("on", "active", "hidden", "show", "open", "sel", "ok", "warn", "err"):
                continue
            issues.append(f"[未使用 CSS] {p.relative_to(ROOT)} → .{cls}")
    return issues


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    # 收集所有 HTML 的 id
    html_ids: set[str] = set()
    html_text = ""
    for p in _walk((".html",)):
        t = read(p)
        html_text += t
        html_ids |= set(re.findall(r'id="([A-Za-z0-9_-]+)"', t))
    js_text = "\n".join(read(p) for p in _walk((".js",)))

    print("=" * 74)
    print("🧹 廢碼掃描")
    print("=" * 74)

    groups = [
        ("Python", check_python()),
        ("JavaScript / DOM", check_js(html_ids)),
        ("CSS", check_css(html_text, js_text)),
        ("翻譯（三語）", check_i18n(html_text, js_text)),
    ]
    total = 0
    for name, issues in groups:
        print(f"\n── {name}：{len(issues)} 項")
        for i in issues[:40]:
            print("   ", i)
        if len(issues) > 40:
            print(f"    …還有 {len(issues) - 40} 項")
        total += len(issues)

    print(f"\n{'=' * 74}")
    if total == 0:
        print("✅ 沒有發現廢碼，代碼是乾淨的")
    else:
        print(f"⚠️ 共 {total} 項要處理 —— 修完請再跑一次確認歸零")
    if args.strict and total:
        sys.exit(1)


if __name__ == "__main__":
    main()
