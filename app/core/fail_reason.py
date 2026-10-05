"""解析失敗原因分類（給客戶看的白話訊息用）。

小羅 2026-10-06：
  「客人遇到不能解析時，不要再一律顯示『平台改版』；要依『每一種情況』給對應的白話訊息。」
  原則：客人訊息**不提登入/cookies**；只要讓他知道「是這則內容的問題」。

用法：
  from .core import fail_reason
  situation = fail_reason.classify(platform, exc.code, exc.message)
  # → "NOT_PUBLIC" / "RESTRICTED" / ... 前端用 err_<situation> 的翻譯顯示

前端的翻譯 key ＝ `err_<SITUATION>`（見 static/locales/*.json）。
對照正本文件：D:/pi-agent/專案_客戶回覆資料庫_2026-10-06.md
"""
from __future__ import annotations

# 已經是「明確、可直接顯示」的錯誤碼 → 不重新分類
_KEEP = {
    "QUOTA_EXCEEDED", "AD_REQUIRED", "UNSUPPORTED_URL", "LOGIN_REQUIRED",
    "PLATFORM_DISABLED", "FEATURE_DISABLED", "MAINTENANCE", "NOT_FOUND",
    "BAD_REQUEST", "AUTH_REQUIRED", "PAYMENT_NOT_CONFIGURED", "EMAIL_TAKEN",
    "WEAK_PASSWORD", "BAD_EMAIL",
}

# 情況代碼 → 判斷關鍵字（先比對的放前面：越具體越前面）
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("NOT_PUBLIC", (
        "未開放所有人查看", "特定受眾", "未公開", "沒有公開", "只有追蹤者",
        "僅限好友", "只有粉絲", "保護帳號", "私人帳號", "私人貼文", "此內容並未開放",
        "並非對所有人開放", "部分受眾無法看到",
        "未公开", "特定受众", "此内容并未开放", "仅限好友", "只有粉丝", "保护账号",
        "并非对所有人开放", "部分受众无法看到",
        "private account", "followers only", "not available to everyone",
        "this content isn't available", "protected account",
    )),
    ("RESTRICTED", (
        "需登入", "需要登入", "請登入", "登入後", "會員專屬", "大會員", "年齡限制",
        "成人內容", "付費會員專屬",
        "需登录", "需要登录", "请登录", "会员专属", "年龄限制", "成人内容",
        "login required", "sign in", "log in",
    )),
    ("PAID", ("付費", "充電", "番劇", "購買", "付费", "充电", "番剧", "购买", "purchase", "premium only")),
    ("REGION", (
        "地區限制", "所在地區", "不支援您的地區", "地区限制", "所在地区",
        "not available in your country", "geo", "region",
    )),
    ("COPYRIGHT", ("版權", "版权", "copyright")),
    ("DELETED", ("已刪除", "已被刪除", "刪除", "不存在", "已失效", "已删除", "删除",
                  "not found", "404")),
    ("NO_VIDEO", (
        "沒有影片", "找不到影片", "沒有可下載", "沒有影片資料", "圖文", "純文字",
        "没有视频", "找不到视频", "没有可下载", "没有视频数据", "图文", "纯文字",
        "no video", "not a video",
    )),
    ("LIVE", ("直播", "live stream", "正在直播")),
    ("BUSY", (
        "逾時", "timeout", "忙碌", "暫時", "請稍後", "限流",
        "超时", "暂时", "请稍后", "繁忙",
        "rate limit", "風控", "风控", "擋住", "挡住", "blocked",
    )),
]


def classify(platform: str | None, code: str | None, message: str | None) -> str:
    """回傳「情況代碼」（前端用 err_<代碼> 顯示白話訊息）。

    - 已是明確錯誤碼（額度／不支援…）→ 原樣回傳
    - 否則依訊息關鍵字判斷情況；判斷不出 → PLATFORM_CHANGED（平台更新中）
    """
    code = (code or "").upper()
    if code in _KEEP:
        return code

    text = (message or "").lower()
    for situation, keys in _RULES:
        for k in keys:
            if k.lower() in text:
                return situation
    return "PLATFORM_CHANGED"
