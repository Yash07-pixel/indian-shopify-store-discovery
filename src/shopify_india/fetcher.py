from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .config import Settings
from .models import FetchRecord
from .normalize import origin


class FetchError(RuntimeError):
    pass


class RespectfulFetcher:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._global = asyncio.Semaphore(settings.global_concurrency)
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.8,*/*;q=0.1",
                # Avoid proxy/server content-encoding mismatches and make the byte cap deterministic.
                "Accept-Encoding": "identity",
            },
            timeout=httpx.Timeout(settings.timeout_seconds),
            follow_redirects=True,
            http2=True,
            transport=transport,
        )

    async def __aenter__(self) -> "RespectfulFetcher":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.settings.cache_dir / f"{digest}.json"

    def _load_cache(self, url: str) -> FetchRecord | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            return FetchRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _save_cache(self, record: FetchRecord) -> None:
        path = self._cache_path(record.requested_url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json(), encoding="utf-8")

    async def _wait_for_slot(self, url: str) -> asyncio.Lock:
        host = (urlsplit(url).hostname or "").lower()
        lock = self._locks.setdefault(host, asyncio.Lock())
        await lock.acquire()
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        if elapsed < self.settings.per_domain_delay:
            await asyncio.sleep(self.settings.per_domain_delay - elapsed)
        return lock

    async def _raw_get(self, url: str) -> httpx.Response:
        lock = await self._wait_for_slot(url)
        host = (urlsplit(url).hostname or "").lower()
        try:
            async with self._global:
                async with self.client.stream("GET", url) as streamed:
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in streamed.aiter_bytes():
                        remaining = self.settings.max_response_bytes - size
                        if remaining <= 0:
                            break
                        chunks.append(chunk[:remaining])
                        size += min(len(chunk), remaining)
                    response = httpx.Response(
                        status_code=streamed.status_code,
                        headers=streamed.headers,
                        content=b"".join(chunks),
                        request=streamed.request,
                        extensions=streamed.extensions,
                        history=streamed.history,
                    )
            self._last_request[host] = time.monotonic()
            return response
        finally:
            lock.release()

    async def robots_parser(self, site_origin: str) -> RobotFileParser:
        if site_origin in self._robots:
            return self._robots[site_origin]
        parser = RobotFileParser()
        robots_url = f"{site_origin.rstrip('/')}/robots.txt"
        parser.set_url(robots_url)
        try:
            response = await self._raw_get(robots_url)
            if response.status_code < 400:
                parser.parse(response.text.splitlines())
            else:
                parser.parse([])
        except httpx.HTTPError:
            parser.parse([])
        self._robots[site_origin] = parser
        return parser

    async def allowed(self, url: str) -> bool:
        parser = await self.robots_parser(origin(url))
        return parser.can_fetch(self.settings.user_agent, url)

    async def fetch(self, url: str, *, check_robots: bool = True, use_cache: bool = True) -> FetchRecord:
        if use_cache and (cached := self._load_cache(url)) is not None:
            return cached
        if check_robots and not await self.allowed(url):
            return FetchRecord(requested_url=url, final_url=url, status_code=0, robots_allowed=False, error="robots_disallowed")

        last_error = ""
        for attempt in range(self.settings.retries + 1):
            try:
                response = await self._raw_get(url)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.settings.retries:
                    retry_after = response.headers.get("Retry-After", "")
                    delay = float(retry_after) if retry_after.isdigit() else 2**attempt
                    await asyncio.sleep(min(delay, 30))
                    continue
                body_bytes = response.content[: self.settings.max_response_bytes]
                encoding = response.encoding or "utf-8"
                body = body_bytes.decode(encoding, errors="replace")
                record = FetchRecord(
                    requested_url=url,
                    final_url=str(response.url),
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type", ""),
                    body=body,
                    headers={k.lower(): v for k, v in response.headers.items()},
                    redirect_history=[str(item.url) for item in response.history],
                )
                if response.status_code < 400:
                    self._save_cache(record)
                return record
            except (httpx.HTTPError, UnicodeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.settings.retries:
                    await asyncio.sleep(2**attempt)
        raise FetchError(last_error or "request failed")

    async def validate_image(self, url: str) -> bool:
        if not url or any(token in url.lower() for token in ("favicon", "apple-touch", "sprite", "pixel", "tracking")):
            return False
        try:
            if not await self.allowed(url):
                return False
            response = await self._raw_get(url)
            content_type = response.headers.get("content-type", "").lower()
            return response.status_code < 400 and content_type.startswith("image/") and len(response.content) > 64
        except (httpx.HTTPError, FetchError):
            return False


def cached_record_hash(record: FetchRecord) -> str:
    return hashlib.sha256(record.body.encode("utf-8", errors="ignore")).hexdigest()

