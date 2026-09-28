# 轉運站（ferry）


> 🌐 **【轉運站主網域】`scefo.com`（2026-09-29 已搬好上線）**
> - 購買日 **2026-09-29**、年費 US$10.46、**到期 2027-09-29**、自動續約**已開**（Cloudflare Registrar，Visa 末四碼 3892）
> - ✅ **前台**：`https://scefo.com`　**後台**：`https://scefo.com/admin/`
> - ✅ **`www.scefo.com`** → 301 自動轉到 `scefo.com`（Cloudflare Worker，不佔 Railway 名額）
> - ✅ **舊網址 `https://ferry.v8i8.com` 仍可用**（Railway 免費方案「每服務自訂網域」名額已滿，兩個網域都在同一個服務上）
> - ⚠️ 小羅問「後台要不要加前綴」→ **不需要**：後台就是同網域的 `/admin/`（本來就是路徑，不是獨立網域）
> - 📌 **小羅只要講到「轉運站」，AI 一律要主動告訴他網址是 `scefo.com`**

---

13 平台無水印下載 ＋ 同 WiFi 無損傳輸。

## 架構（模塊化：一平台一檔，可各自開關）
- `app/core/` models / errors / config / http / timezone / registry
- `app/platforms/` base + douyin / tiktok / bilibili（新增平台＝加一檔＋註冊一行）
- `app/services/` flags(開關) / quota(免費次數) / auth(預留) / billing(預留) / transfer(WebRTC) / resolve_service
- `static/` 前端（原生 JS、三語）

## 開發
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8800

## 環境變數
FREE_LIMIT_ENABLED / FREE_DOWNLOAD_PER_DAY=5 / FREE_TRANSFER_PER_DAY=5
