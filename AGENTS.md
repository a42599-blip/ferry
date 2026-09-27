# 轉運站（ferry）專案指令

## 🔴 開機第一件事

先讀（照全域規則）：
1. `C:/Users/USER/Desktop/記憶目錄_備份/小羅的人設.md`（帳密／API Key／表單欄位）
2. `D:/pi-agent/_開機必讀.md`（全部專案總表）
3. `D:/pi-agent/交接_2026-09-26_轉運站開發.md`（本專案交接）

---

## 🧹 鐵律一：每次改完程式，**當下**清掉廢碼（小羅 2026-09-27 定案）

> 小羅原話：
> 「每次修一個 bug 或改一段代碼，把廢掉的、之前壞掉的、沒有用的代碼要刪掉。
>   不要殘留 —— 否則最後代碼會亂七八糟，動一個壞另一個，查也查不到。」

### 為什麼這條是鐵律（真實教訓）

**2026-09-27 實際出事：** 把首頁的「解析」按鈕（`id="go"`）換成「貼上並解析」（`id="paste"`），
**但沒清掉 app.js 裡 `$('#go')` 的參照** → JS 一載入就拋錯 →
**整個前台功能全掛**（小羅在 iPhone 上「解析不了」，查半天）。

### 做法（每次改完必做，不得跳過）

```bash
cd D:/ferry
python tools/deadcode.py          # 有問題就修到 0 項為止
python tools/deadcode.py --strict # 有問題會 exit 1（可掛流程）
```

`tools/deadcode.py` 會檢查 5 類：

| # | 類別 | 為什麼要檢查 |
|:-:|:---|:---|
| ① | Python 未使用的 import | 垃圾累積 |
| ② | Python 定義了但沒人呼叫的函式 | 死碼 |
| ③ | **JS 引用了不存在的 HTML id** | ⚠️ **最致命**（會讓整支 JS 掛掉，實際發生過）|
| ④ | JS 定義了但沒用到的函式 | 死碼 |
| ⑤ | CSS 沒有對應 class | 垃圾累積 |

### 驗收標準

- **`tools/deadcode.py` 必須 0 項**
- 改完跑 `python tools/audit_frontend.py --admin`（版面／功能／後台／網址路由）
- 不留「註解掉的舊程式」——要留就寫進 git commit，不要留在檔案裡

---

## 🧪 鐵律二：改完要自己審核（不要等小羅抓）

```bash
# 本機
python tools/audit_frontend.py --admin

# 線上正式站
python tools/audit_frontend.py --base https://ferry.v8i8.com --admin --admin-pass "Ferry-C2cPqk-1827"
```

| 類別 | 檢查內容 |
|:---|:---|
| A. 版面 | 上→下、左→右：破圖／翻譯殘留／空按鈕／空連結 |
| B. 功能 | 真的按下去：語言切換、解析、下載鈕、配對碼、註冊、價格、教學、圖示 |
| C. 後台 | 7 頁資料、平台開關、功能開關 |
| D. 網址路由 | 22 個網址是否交給正確平台 |

---

## 🔴 鐵律三：絕對不碰 v8i8

- `a42599-blip/diedai-ban`（v8i8.com）：❌ **不准 push／commit／建分支／改任何設定**
- 其他 repo（video-downloader／chao8／video-ai-search*）與 ferry 本身：可以動
- 研究 v8i8 **只能讀**
- GitHub 寫入一律先問小羅

---

## 🏗️ 架構速記

```
app/
  main.py              只組裝（FastAPI＋middleware＋事件記錄）
  api_member.py        會員／付款／回報 API
  admin/
    routes.py          後台 API（7 頁）
    security.py        HMAC 權杖 ＋ TOTP 兩步驟驗證
  core/
    db.py              SQLite（唯一碰 DB 的地方，含輕量遷移）
    registry.py        平台註冊表（新增平台＝加一行）
    errors.py          錯誤碼（前端靠 code 翻譯）
    config.py          環境變數（唯一讀 env 的地方）
  platforms/
    base.py            平台契約（Resolver）
    _ytdlp.py          yt-dlp 通用基底
    _ssr.py            瀏覽器 SSR 助手 ＋ 安全 meta 解析 ＋ page_video_info
    _douyin_shared.py  抖音／西瓜共用（a_bogus 官方 API）
    _weibo_visitor.py  微博匿名訪客 cookie
    _bili_wbi.py       B站 WBI 簽章
    _douyin_abogus.py  抖音 a_bogus 簽章
  services/
    events.py  flags.py  quota.py  resolve_service.py  downloader.py
    transfer.py  members.py  billing.py  notify.py  monitor.py
    cookies.py  browser.py
static/
  index.html  app.js  transfer.js  save.js  sha256.js  style.css
  save.js              ⚠️ 跨平台存檔（iOS 相簿／Android 下載／桌機選路徑）
  admin/               後台（左側邊欄 ＋ KPI 卡 ＋ 底部狀態列）
  locales/             三語（繁中／简中／EN）—— 所有文字都走 t()
tools/
  audit_frontend.py    審核員（版面／功能／後台／路由）
  deadcode.py          廢碼掃描
  check_platform.py    單一平台解析測試
```

---

## 📌 常用指令

```bash
cd D:/ferry

# 本機啟動
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8800

# 改完必跑
python tools/deadcode.py
python tools/audit_frontend.py --admin

# 測單一平台
python tools/check_platform.py "https://www.bilibili.com/video/BV1xx411c7mD"

# 部署（推上去 Railway 自動部署）
git add -A && git commit -m "..." && git push origin main
```

---

## ⚙️ 技術要點（踩過的坑，別再踩）

| # | 要點 |
|:-:|:---|
| 1 | **Playwright 必須 `channel="chromium"`**（新版無頭）——headless shell 會被抖音判機器人 |
| 2 | **解析 HTML 絕不用 `[^>]+` 接 `.*?`** → 災難性回溯會鎖死 event loop（逾時也救不了）|
| 3 | **Service Worker 不要 cache-first 供 HTML/CSS/JS** → 使用者會卡在舊版 |
| 4 | Python 裡處理 `\u002F` 一定要用 **raw string**，否則被 Python 吃掉 → 網址被截斷 |
| 5 | 後端錯誤一律回 **錯誤碼**，前端靠 `err_<CODE>` 翻譯 |
| 6 | 事件一律用**裝置 ID** 記錄（會員登入後軌跡才不會斷成兩段）|
| 7 | 抖音系列（抖音／西瓜／頭條）的影片網址常是 **URL 編碼**的，要解碼 |

---

*本檔由 pi 於 2026-09-27 建立。*
