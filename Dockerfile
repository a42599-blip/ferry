FROM python:3.12-slim-bookworm
WORKDIR /app

# ffmpeg      → B站 DASH 影音合流、YouTube 高畫質合併
# unzip/curl  → 安裝 Deno 用
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg unzip curl ca-certificates \
 && apt-get clean \
 && find /var/lib/apt/lists -mindepth 1 -delete

# ── Deno：yt-dlp 解 YouTube JS 驗證（nsig）的必要執行環境 ──────────
# ⚠️ 沒有它，YouTube 會一直回「需要登入或 cookies」（雲端 IP 尤其明顯）。
#    v8i8 就是靠這個才通的（見 D:/pi-agent/diedai-ban-work/server.py 的 _YT_OPTS_EXTRA）。
ENV DENO_INSTALL=/usr/local \
    DENO_DIR=/tmp/deno
RUN curl -fsSL https://deno.land/install.sh | sh -s -- -y --no-modify-path \
 && deno --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && apt-get clean \
 && find /var/lib/apt/lists -mindepth 1 -delete

COPY app ./app
COPY static ./static

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
