"""共用 HTTP 客戶端（逾時、重試、UA 統一）。

所有平台模組都要用這一支，不准自己建 client。
（規格書第 21-2 章）
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from .config import settings


class HttpClient:
    """薄薄一層 httpx.AsyncClient 包裝。"""

    def __init__(self, ua: Optional[str] = None, timeout: Optional[int] = None):
        self._ua = ua or settings.user_agent
        self._timeout = timeout or settings.http_timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "HttpClient":
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={
                "User-Agent": self._ua,
                "Accept": "*/*",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            },
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        if self._client is None:
            raise RuntimeError("HttpClient 必須用 async with 使用")
        last_exc: Exception | None = None
        for attempt in range(settings.http_retries + 1):
            try:
                resp = await self._client.request(method, url, **kw)
                if resp.status_code >= 500 and attempt < settings.http_retries:
                    await asyncio.sleep(0.6 * (attempt + 1))
                    continue
                return resp
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < settings.http_retries:
                    await asyncio.sleep(0.6 * (attempt + 1))
                    continue
                raise
        raise last_exc  # pragma: no cover

    async def get(self, url: str, **kw) -> httpx.Response:
        return await self._request("GET", url, **kw)

    async def post(self, url: str, **kw) -> httpx.Response:
        return await self._request("POST", url, **kw)

    async def get_json(self, url: str, **kw) -> Any:
        resp = await self.get(url, **kw)
        resp.raise_for_status()
        return resp.json()

    async def get_text(self, url: str, **kw) -> str:
        resp = await self.get(url, **kw)
        resp.raise_for_status()
        return resp.text

    async def post_json(self, url: str, **kw) -> Any:
        resp = await self.post(url, **kw)
        resp.raise_for_status()
        return resp.json()


def default_headers(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    """常用的請求標頭（平台模組可再增補）。"""
    h = {
        "User-Agent": settings.user_agent,
        "Accept": "*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }
    if extra:
        h.update(extra)
    return h
