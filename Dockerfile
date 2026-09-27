FROM python:3.12-slim-bookworm
WORKDIR /app

# ffmpeg      → B站 DASH 影音合流、YouTube 高畫質合併
# unzip/curl  → 安裝 Deno 用
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg unzip curl ca-certificates \
 && apt-get clean \
 && find /var/lib/apt/lists -mindepth 1 -delete

# ── Deno：yt-dlp 解 YouTube JS 驗證（nsig）的必要執行環境 ──────────
# ⚠️ 沒有它，YouTube 會回「需要登入或 cookies」。
#
# ⚠️⚠️ 一定要**鎖定 v2.8.3**（2026-09-27 從 v8i8 的 Dockerfile 學到）：
#     v8i8 的註解原文 ——「鎖定版本 v2.8.3！每次部署抓最新版會導致
#     Deno 更新後 yt-dlp 不相容」。
#     我原本用 `curl https://deno.land/install.sh | sh` 抓最新版 → YouTube 一直失敗。
#     yt-dlp 也要一起鎖（requirements.txt 的 yt-dlp==2026.6.9），兩者要搭配。
#     （用 python 直接解壓，不留暫存檔，避免多餘的檔案清理動作）
ENV DENO_DIR=/tmp/deno
RUN curl -fsSL https://github.com/denoland/deno/releases/download/v2.8.3/deno-x86_64-unknown-linux-gnu.zip \
      | python3 -c "import sys,zipfile,io; zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())).extractall('/usr/local/bin')" \
 && chmod +x /usr/local/bin/deno \
 && deno --version
ENV PATH="/usr/local/bin:${PATH}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && apt-get clean \
 && find /var/lib/apt/lists -mindepth 1 -delete

COPY app ./app
COPY static ./static

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
