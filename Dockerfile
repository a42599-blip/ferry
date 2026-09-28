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

# ── YouTube 通行證（PO Token）產生器 bgutil ─────────────────────────────────────
# 只有 app/platforms/youtube.py 會用到（其他平台不受影響）。開機時在背景跑（見 CMD），只聽容器內 127.0.0.1:4416。
# 為什麼：雲端 IP 會被 YouTube 要求「Sign in to confirm you're not a bot」，yt-dlp 官方建議用 PO Token 外掛。
# 版本鎖 2.0.0，要跟 requirements.txt 的 bgutil-ytdlp-pot-provider 一致。產生器沒跑起來也沒關係：
# youtube.py 會自動跳過通行證方案、改用其他方案。
RUN curl -fsSL https://github.com/Brainicism/bgutil-ytdlp-pot-provider/archive/refs/tags/2.0.0.tar.gz \
      | tar -xz -C /opt \
 && mv /opt/bgutil-ytdlp-pot-provider-2.0.0 /opt/bgutil \
 && cd /opt/bgutil/server \
 && deno install --allow-scripts=npm:canvas --frozen

# ── Cloudflare WARP 免費通道工具（只有 YouTube 模塊會走；開機由 deploy/start.sh 啟動）──────
# wgcf＝申請免費 WARP 帳號、產生通道設定；wireproxy＝不需系統權限就能開通道（當 HTTP 代理用）
RUN curl -fsSL -o /usr/local/bin/wgcf https://github.com/ViRb3/wgcf/releases/download/v2.3.0/wgcf_2.3.0_linux_amd64 \
 && chmod +x /usr/local/bin/wgcf \
 && curl -fsSL https://github.com/windtf/wireproxy/releases/download/v1.1.3/wireproxy_linux_amd64.tar.gz \
      | tar -xz -C /usr/local/bin wireproxy \
 && chmod +x /usr/local/bin/wireproxy

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && apt-get clean \
 && find /var/lib/apt/lists -mindepth 1 -delete

COPY app ./app
COPY static ./static
COPY deploy ./deploy

EXPOSE 8000
# 開機腳本：先在背景開 YouTube 用的通行證產生器與 WARP 通道（失敗都不影響網站），再開網站
CMD ["sh", "deploy/start.sh"]
