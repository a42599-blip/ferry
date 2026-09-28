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

## 🌐 鐵律一之二：新增／修改前台文字，**一定要同時做三語翻譯**（小羅 2026-09-29）

> 小羅原話：「我們新加的一些功能、新更改的這些設定、欄位、按鈕，
>           都是要記得你要把翻譯這件事也做進去 3 種語言，不要等一下又忘記了。」

1. 任何**新的或改過的前台文字**（欄位標籤、placeholder、按鈕、提示訊息、錯誤訊息、
   等級名稱、方案說明…）→ **同一次改動**就要補齊：
   - `static/locales/zh-Hant.json`（繁體）
   - `static/locales/zh-Hans.json`（简体）
   - `static/locales/en.json`（English）
2. 動態產生的文字（JS 用 `t('key')` 組的）也要走語言檔，**不可以寫死中文**。
3. 改完自我檢查：
   ```bash
   python - <<'PY'
   import json,re
   d=json.load(open("static/locales/zh-Hant.json",encoding="utf-8"))
   for f in ("zh-Hans","en"):
       e=json.load(open(f"static/locales/{f}.json",encoding="utf-8"))
       miss=[k for k in d if k not in e]
       print(f, "缺：", miss)
   PY
   ```
   （三份 key 數量要一致；缺的補上）
4. 改 `static/*.js`／`*.css` 一樣要升 `?v=` 與 `sw.js` 快取名稱（鐵律七）。

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

---

## 🎯 轉運站開發鐵律（小羅 2026-09-27 定案，★必須遵守★）

### 鐵律一：零廢碼（每次改完「當下」就要清）
> 小羅：「你每改一個，你就要把廢的程式碼給刪除乾淨，這個是鐵律。我要要求到零廢碼。」

1. **每次改完程式，立刻**跑：`./.venv/Scripts/python.exe tools/deadcode.py`
2. **必須 0 項**才算完成。有殘留就修到歸零，不可「之後再清」。
3. 不留：註解掉的舊程式、沒人呼叫的函式、沒人用的 import／CSS／i18n 鍵。
4. 改完也要跑 `tools/audit_frontend.py --admin`，必須全綠。
5. **錯誤示範（真實事故）**：把 `id="go"` 改成別的，卻沒清 JS 參照 → 整支 JS 掛掉、前台全失效。

### 鐵律二：v8i8 只能「參考」，絕對不能動
> 小羅：「千萬記住，我跟你講的所有東西你一定只能參考，不能去動 v8i8 的任何東西，
>        也不能讓它觸發部署。」

| | 規則 |
|---|---|
| ✅ 可以 | 讀 `D:/pi-agent/diedai-ban-work/**` 的程式碼，學它的做法 |
| ✅ 可以 | 把學到的做法**重新實作**在轉運站（ferry）|
| ❌ 禁止 | 對 v8i8 的 repo（`a42599-blip/diedai-ban`）做 push／commit／建分支／改設定 |
| ❌ 禁止 | 用 GitHub API 對 v8i8 做任何寫入 |
| ❌ 禁止 | 在 v8i8 的目錄裡建立／修改任何檔案（會觸發 Railway 重新部署 → 換 IP → 抖音/YouTube 掛掉）|
| ❌ 禁止 | 跑任何會改動 v8i8 目錄的指令 |

**要寫入 v8i8 之前，一律先問小羅。**

### 鐵律三：參考的「範圍」要對
> 小羅：「我叫你去參考的只是**所有平台的解析方法**。」

- ✅ **要參考**：v8i8 的**解析邏輯**（怎麼跟平台要資料、快在哪、為什麼手機電腦都能用）
- ❌ **不要參考**：v8i8 的**畫質清單做法** —— 它沒有做到「所有畫質都給使用者選」。
  轉運站的畫質是**自己做的**（ffprobe 讀真實解析度 → 平台給幾種就列幾種）。
- 原因：v8i8 手機／電腦都能用 → 它的**解析路徑**是對的；但它的畫質清單是篩選過的。

### 鐵律四：刪除一律丟資源回收筒
見上方「過渡檔鐵律 〇」——`bash "D:/pi-agent/_工具/回收筒.sh" "<路徑>"`，永久刪除權只在小羅手上。

### 鐵律五：兩個實際踩過的技術陷阱（2026-09-27）

**① yt-dlp 不要用手機 User-Agent**
   手機 UA 會讓 Facebook／TikTok／微博／今日頭條 回「沒有影片資料」的頁面（畫質 0 種）。
   → 一律用桌面 Chrome UA（`app/platforms/_ytdlp.py` 的 `_DESKTOP_UA`）。
   實測：FB 手機 UA 0 種 / 桌面 UA 1080P；TikTok 手機 UA 失敗 / 桌面 UA 1920。

**② 不要回 5xx 狀態碼**
   本站前面有 Cloudflare，它會**攔截 origin 的 5xx**，換成自己的純文字錯誤頁
   （使用者只看到 `error code: 502`，看不到我們的說明）。
   → 平台錯誤用 **422**（`app/core/errors.py` 的 `PlatformError.http_status`）。

### 鐵律六：哪個模塊壞就修哪個（小羅 2026-09-27）

> 小羅：「我們每一個都是分開的模塊，我如果壞什麼你就修那個模塊，你不要去動別的。」

- 每個平台是**獨立模塊**（`app/platforms/<平台>.py`）→ 壞哪個改哪個
- ❌ 不要為了解一個平台，去改共用的邏輯讓**所有平台**跟著變
  （實際踩到：為了修抖音，我把「所有影片都改走伺服器轉發」→ 動到全部平台）
- ✅ 正確做法：讓那個平台的模塊「自己標記」（例：抖音自己在 douyin.py 標 `mode="relay"`）
- 共用層只放「大家都需要的正確行為」（例：下載一律優先 H.264，因為 iPhone 不支援 AV1）

### 鐵律七：改前台的 JS/CSS，**一定要升版本號**（小羅 2026-09-27 實際踩到）

> 小羅：「你都沒改，還跟我講你改好了，到底是怎麼回事？」

改完 `static/*.js`／`static/*.css`／`static/admin/*` 之後，**必須**把
`static/index.html` 與 `static/admin/index.html` 裡的 `?v=` 數字 +1，
並把 `static/sw.js` 的快取名稱換一個（例如 v3 → v4）。

**為什麼**：瀏覽器會快取同一個網址的 JS/CSS。程式改了但版本號沒升，
使用者的手機／電腦**還是跑舊的程式** → 會出現「你說改了但我看到的還是舊的」。
（實際踩到：免費次數開關的中文標籤、save.js 的 MIME 修正都因為這樣沒生效）

檢查方式：`curl -s https://ferry.v8i8.com/admin/ | grep -o "app.js?v=[0-9]*"`

### 📌 進行中的專案 → 交接文件在哪（每次開機必查）

> 小羅 2026-09-28：「我怕你下次開機又找不到交接檔。」

**固定位置（照順序找，一定找得到）：**
1. `D:/pi-agent/_開機必讀.md` → 搜尋「**進行中專案交接文件**」那個表格
2. 當日記憶檔開頭有 🔴 區塊直接寫交接檔路徑
3. 交接檔命名規則：**`D:/pi-agent/交接_YYYY-MM-DD_<專案名>.md`**
4. 目前最新：**`D:/pi-agent/交接_2026-09-28_轉運站開發.md`**

**⛔ 不要自己猜檔名、不要說找不到。** 找不到就直接 `ls D:/pi-agent/ | grep 交接`。

### 🔴🔴 鐵律（硬性・最高優先）：模塊化專案「只修要修的模塊」

> 小羅 2026-09-28 明確要求：「我們現在是**模塊化**的區域製作這個項目，
> 所以我們**只修我們要修的模塊，不要去動到別的模塊**。
> 改動的時候也是一樣，**以免造成修一個壞另外一個**。」

**⭐ 這是硬性規則，不是建議。每次動手前後都要檢查。⭐**

#### 一、動手前（必問自己）
1. **我要修的是哪一個模塊？**（哪個檔案）
2. **這個改動會不會碰到別的模塊？**（例如共用的 `resolve_service.py`、`_ytdlp.py`、`style.css`、`app.js`）
3. **如果非改共用層不可** → **先問小羅**，並確認「所有用到它的模塊都會跟著變」

#### 二、正確做法：讓「那個模塊自己處理」
```
✅ 對：抖音的問題 → 只在 app/platforms/douyin.py 裡改
✅ 對：蝦皮要 relay → 只在 shopee.py 標 mode="relay"
✅ 對：IG 要 relay  → 只在 instagram.py 標 mode="relay"
❌ 錯：為了解抖音，把「所有平台都改成 relay」（動到全部 13 個平台）
```

#### 三、動手後（必做）
1. 跑 `python tools/deadcode.py` → 必須 **0 項**
2. 跑 `python tools/audit_frontend.py --admin` → 必須全綠（含「電腦版 vs 手機版功能對等」）
3. **跑 `python tools/check_linkage.py`** → 確認前後台＋資料庫聯動沒壞
4. **回想：我剛剛有沒有動到不該動的模塊？**

#### 四、真實事故（血淋淋的教訓）
| 事故 | 我做了什麼 | 後果 |
|---|---|---|
| 抖音 | 為了修抖音，把**所有平台**都改成走伺服器轉發 | 動到全部 13 個平台（已改回，只留抖音自己標記）|
| 前台 | 為了改回覆框，動了 `style.css` 的共用規則 | 用 regex 刪規則留下孤兒 `}` → **手機版樣式整段失效** |
| 後台 | 為了加會員資料模塊，把 JS 插進 `pgDevices()` 裡面 | 變成區域函式 → **`pgMembers is not defined`、整頁載入失敗** |
| 抖音 | 為了加 Referer，動了格式建構 | 漏了常數定義 → `NameError` → **抖音完全不能解析** |

**→ 每一次都是「動到不該動的」或「改了沒驗」，不是「模塊本身寫錯」。**
