# 轉運站（ferry）

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
