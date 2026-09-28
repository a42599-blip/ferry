#!/bin/sh
# 轉運站容器開機腳本
# 先在背景開 YouTube 模塊用的兩個小工具（任何一個失敗都不影響網站），再開網站。
#
# ① bgutil 通行證（PO Token）產生器 → 只聽 127.0.0.1:4416
# ② Cloudflare WARP 免費通道（wgcf 申請帳號＋wireproxy 開 HTTP 代理）→ 只聽 127.0.0.1:40001
#    只有 app/platforms/youtube.py 會走這個代理，其他平台照舊直連。
#    為什麼：Railway 機房網路的 IP 會被 YouTube 拒絕（解析或下載），改從 Cloudflare 網路出去。
#    WARP 帳號存在 /data/warp（永久空間）→ 部署時不重新申請（避免 Cloudflare 限制申請次數）。
#    紀錄：/tmp/bgutil.log、/tmp/warp.log

( cd /opt/bgutil/server/node_modules \
  && deno run --allow-env --allow-net --allow-ffi=. --allow-read=. ../src/main.ts \
  > /tmp/bgutil.log 2>&1 ) &

(
  W="${DATA_DIR:-/data}/warp"
  mkdir -p "$W" && cd "$W" || exit 0
  [ -f wgcf-account.toml ] || wgcf register --accept-tos > /tmp/warp.log 2>&1
  [ -f wgcf-profile.conf ] || wgcf generate >> /tmp/warp.log 2>&1
  [ -f wgcf-profile.conf ] || exit 0
  # 通道內只用 IPv4＋MTU 1180（2026-09-28 實測）：
  #   · 容器網路外面還包一層，封包太大會被丟 → 小請求通、大回應（YouTube）卡到逾時 → 要調小
  #   · 但 IPv6 規定 MTU 至少 1280，調小會弄壞 IPv6 → 所以拿掉 IPv6 位址與 IPv6 DNS
  #   本機驗證：warp=on、經通道開 YouTube 首頁 882KB 0.24 秒、yt-dlp 解析 41 種、抓 10MB 之後 206
  { sed -e 's/^MTU = .*/MTU = 1180/' \
        -e 's/^Address = \([^,]*\),.*/Address = \1/' \
        -e 's/^DNS = .*/DNS = 1.1.1.1, 1.0.0.1/' wgcf-profile.conf
    printf '\n[http]\nBindAddress = 127.0.0.1:40001\n'; } > /tmp/wireproxy.conf
  exec wireproxy -c /tmp/wireproxy.conf >> /tmp/warp.log 2>&1
) &

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
