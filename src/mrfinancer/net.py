"""Shared network layer: rate limiting, complexity budgets, caching, robots.txt.

Every adapter goes through `Fetcher`. Centralising this is what keeps the
scraper half of the hybrid strategy defensible: one place enforces the user
agent, the per-host rate limit, the robots.txt check and the cache, so an
adapter author cannot accidentally hammer a site.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import Settings
from .provenance import ReasonCode


class FetchError(Exception):
    """Carries a machine-readable reason so callers can build a Gap."""

    def __init__(self, reason: ReasonCode, detail: str = "") -> None:
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
        self.reason = reason
        self.detail = detail


# --------------------------------------------------------------------------
# rate limiting
# --------------------------------------------------------------------------


@dataclass
class TokenBucket:
    """Simple per-host rate limiter. Serialises waiters so bursts cannot slip
    past when several coroutines wake at once."""

    rate_per_second: float
    _next_free: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self) -> None:
        interval = 1.0 / self.rate_per_second
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_free - now)
            self._next_free = max(now, self._next_free) + interval
        if wait > 0:
            await asyncio.sleep(wait)


@dataclass
class ComplexityBudget:
    """Product Hunt charges complexity points per 15-minute window rather than
    counting requests, so a request limiter would not protect us. This tracks
    spend in a sliding window and refuses rather than earning a 429."""

    budget: int
    window_seconds: int
    _spent: list[tuple[float, int]] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        self._spent = [(t, c) for (t, c) in self._spent if t > cutoff]

    @property
    def remaining(self) -> int:
        now = time.monotonic()
        self._prune(now)
        return self.budget - sum(c for _, c in self._spent)

    async def charge(self, cost: int) -> None:
        async with self._lock:
            now = time.monotonic()
            self._prune(now)
            if sum(c for _, c in self._spent) + cost > self.budget:
                raise FetchError(
                    ReasonCode.BUDGET_EXHAUSTED,
                    f"needs {cost} points, {self.remaining} left in window",
                )
            self._spent.append((now, cost))

    def observe_headers(self, headers: httpx.Headers) -> None:
        """Trust the server's own accounting when it reports it."""
        remaining = headers.get("X-Rate-Limit-Remaining")
        limit = headers.get("X-Rate-Limit-Limit")
        if not (remaining and limit):
            return
        try:
            used = int(limit) - int(remaining)
        except ValueError:
            return
        now = time.monotonic()
        self._spent = [(now, max(0, used))]
        self.budget = int(limit)


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------


class ResponseCache:
    """Tiny sqlite cache. Keeps development off the network and keeps repeated
    runs polite."""

    def __init__(self, path: Path, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            "  key TEXT PRIMARY KEY,"
            "  stored_at REAL NOT NULL,"
            "  status INTEGER NOT NULL,"
            "  body TEXT NOT NULL"
            ")"
        )
        self._db.commit()

    def get(self, key: str) -> tuple[int, str] | None:
        row = self._db.execute(
            "SELECT stored_at, status, body FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        stored_at, status, body = row
        if time.time() - stored_at > self.ttl:
            return None
        return status, body

    def put(self, key: str, status: int, body: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO cache (key, stored_at, status, body) "
            "VALUES (?, ?, ?, ?)",
            (key, time.time(), status, body),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()


class NullCache(ResponseCache):
    def __init__(self) -> None:  # noqa: D107 - deliberately not calling super
        self.ttl = 0

    def get(self, key: str) -> tuple[int, str] | None:
        return None

    def put(self, key: str, status: int, body: str) -> None:
        return None

    def close(self) -> None:
        return None


# --------------------------------------------------------------------------
# robots.txt
# --------------------------------------------------------------------------


class RobotsGate:
    """Fetches and caches robots.txt per origin.

    Fails closed: if robots.txt cannot be read we decline to scrape. That is
    the opposite of what most scrapers do, and it is the right default when the
    thing being shipped is a product other people will run.
    """

    def __init__(self, client: httpx.AsyncClient, user_agent: str, enabled: bool) -> None:
        self._client = client
        self._ua = user_agent
        self._enabled = enabled
        self._parsers: dict[str, robotparser.RobotFileParser | None] = {}
        self._lock = asyncio.Lock()

    async def allowed(self, url: str) -> bool:
        if not self._enabled:
            return True
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self._lock:
            if origin not in self._parsers:
                self._parsers[origin] = await self._load(origin)
        parser = self._parsers[origin]
        if parser is None:
            return False  # fail closed
        return parser.can_fetch(self._ua, url)

    async def _load(self, origin: str) -> robotparser.RobotFileParser | None:
        try:
            resp = await self._client.get(f"{origin}/robots.txt", timeout=10.0)
        except httpx.HTTPError:
            return None
        parser = robotparser.RobotFileParser()
        if resp.status_code == 404:
            parser.parse([])  # no robots.txt means no restrictions
            return parser
        if resp.status_code != 200:
            return None
        parser.parse(resp.text.splitlines())
        return parser


# --------------------------------------------------------------------------
# fetcher
# --------------------------------------------------------------------------


class Fetcher:
    """The only way adapters touch the network."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        cache: ResponseCache | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout_seconds,
            follow_redirects=True,
        )
        self.cache = cache if cache is not None else NullCache()
        self.robots = RobotsGate(self.client, settings.user_agent, settings.respect_robots)
        self._buckets: dict[str, TokenBucket] = {}
        self.ph_budget = ComplexityBudget(
            settings.producthunt_complexity_budget,
            settings.producthunt_budget_window_seconds,
        )

    def _bucket(self, url: str) -> TokenBucket:
        host = urlparse(url).netloc
        if host not in self._buckets:
            self._buckets[host] = TokenBucket(self.settings.requests_per_second)
        return self._buckets[host]

    async def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        check_robots: bool = False,
        use_cache: bool = True,
    ) -> str:
        key = f"GET {url} {json.dumps(params or {}, sort_keys=True)}"
        if use_cache:
            hit = self.cache.get(key)
            if hit:
                return hit[1]

        if check_robots and not await self.robots.allowed(url):
            raise FetchError(ReasonCode.ROBOTS_DISALLOWED, url)

        await self._bucket(url).acquire()
        resp = await self._request("GET", url, params=params, headers=headers, auth=auth)
        if use_cache:
            self.cache.put(key, resp.status_code, resp.text)
        return resp.text

    async def get_json(self, url: str, **kw: Any) -> Any:
        text = await self.get_text(url, **kw)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise FetchError(ReasonCode.PARSE_FAILED, str(exc)) from exc

    async def get_bytes(self, url: str, *, params: dict[str, Any] | None = None) -> bytes:
        await self._bucket(url).acquire()
        response = await self._request("GET", url, params=params)
        return response.content

    async def post_json(
        self, url: str, body: dict[str, Any], *, use_cache: bool = True
    ) -> Any:
        key = f"POST {url} {json.dumps(body, sort_keys=True)}" if use_cache else None
        hit = self.cache.get(key) if key else None
        if hit:
            return json.loads(hit[1])
        await self._bucket(url).acquire()
        resp = await self._request("POST", url, json_body=body, headers={"Accept": "application/json"})
        if resp.status_code >= 400:
            raise FetchError(ReasonCode.UPSTREAM_ERROR, f"HTTP {resp.status_code} from {urlparse(url).netloc}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise FetchError(ReasonCode.PARSE_FAILED, "Expected a JSON response") from exc
        if key:
            self.cache.put(key, resp.status_code, resp.text)
        return payload

    async def post_graphql(
        self,
        url: str,
        query: str,
        variables: dict[str, Any],
        *,
        token: str,
        estimated_cost: int,
        cache_public: bool = False,
    ) -> Any:
        key = f"GRAPHQL {url} {query} {json.dumps(variables, sort_keys=True)}" if cache_public else None
        hit = self.cache.get(key) if key else None
        if hit:
            return json.loads(hit[1])
        await self.ph_budget.charge(estimated_cost)
        await self._bucket(url).acquire()
        resp = await self._request(
            "POST",
            url,
            json_body={"query": query, "variables": variables},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        self.ph_budget.observe_headers(resp.headers)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise FetchError(ReasonCode.PARSE_FAILED, str(exc)) from exc
        if isinstance(payload, dict) and payload.get("errors"):
            msg = "; ".join(
                str(e.get("message", e)) for e in payload["errors"][:3]
            )
            raise FetchError(ReasonCode.UPSTREAM_ERROR, msg)
        if key:
            self.cache.put(key, resp.status_code, resp.text)
        return payload

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        json_body: Any | None = None,
    ) -> httpx.Response:
        attempts = self.settings.max_retries + 1
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = await self.client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    auth=auth,
                    json=json_body,
                )
            except httpx.TimeoutException as exc:
                last = FetchError(ReasonCode.TIMEOUT, urlparse(url).netloc)
            except httpx.HTTPError as exc:
                last = FetchError(ReasonCode.UPSTREAM_ERROR, urlparse(url).netloc)
            else:
                if resp.status_code == 429:
                    last = FetchError(ReasonCode.RATE_LIMITED, url)
                elif resp.status_code in (401, 403):
                    # Not retryable: a key is wrong or access is refused.
                    raise FetchError(
                        ReasonCode.UPSTREAM_ERROR,
                        f"HTTP {resp.status_code} from {urlparse(url).netloc}",
                    )
                elif resp.status_code == 404:
                    raise FetchError(ReasonCode.NOT_FOUND, url)
                elif resp.status_code >= 500:
                    last = FetchError(
                        ReasonCode.UPSTREAM_ERROR, f"HTTP {resp.status_code}"
                    )
                elif resp.status_code >= 400:
                    raise FetchError(
                        ReasonCode.UPSTREAM_ERROR,
                        f"HTTP {resp.status_code} from {urlparse(url).netloc}",
                    )
                else:
                    return resp
            if attempt < attempts - 1:
                await asyncio.sleep(1.5 * (2**attempt))
        assert last is not None
        raise last

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
        self.cache.close()
