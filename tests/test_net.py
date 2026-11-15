from __future__ import annotations

import time

import httpx
import pytest

from mrfinancer.config import Settings
from mrfinancer.net import ComplexityBudget, FetchError, Fetcher, RobotsGate, TokenBucket
from mrfinancer.provenance import ReasonCode


class TestComplexityBudget:
    async def test_charges_and_reports_remaining(self):
        budget = ComplexityBudget(budget=100, window_seconds=900)
        await budget.charge(40)
        assert budget.remaining == 60

    async def test_refuses_rather_than_overspending(self):
        """Product Hunt bills complexity points, so exceeding the budget earns a
        429. Refusing locally is strictly better than being rate limited."""
        budget = ComplexityBudget(budget=50, window_seconds=900)
        await budget.charge(40)
        with pytest.raises(FetchError) as exc:
            await budget.charge(20)
        assert exc.value.reason is ReasonCode.BUDGET_EXHAUSTED
        # The failed charge must not have been recorded.
        assert budget.remaining == 10

    def test_server_headers_override_local_accounting(self):
        budget = ComplexityBudget(budget=6250, window_seconds=900)
        budget.observe_headers(
            httpx.Headers({"X-Rate-Limit-Limit": "6250", "X-Rate-Limit-Remaining": "250"})
        )
        assert budget.remaining == 250

    def test_malformed_headers_are_ignored(self):
        budget = ComplexityBudget(budget=100, window_seconds=900)
        budget.observe_headers(httpx.Headers({"X-Rate-Limit-Limit": "lots"}))
        assert budget.remaining == 100


class TestTokenBucket:
    async def test_spaces_out_requests(self):
        bucket = TokenBucket(rate_per_second=20.0)  # 50ms apart
        started = time.monotonic()
        for _ in range(3):
            await bucket.acquire()
        elapsed = time.monotonic() - started
        # First is free, then two waits of ~50ms.
        assert elapsed >= 0.08


class TestRobotsGate:
    async def test_disallowed_path_is_refused(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text="User-agent: *\nDisallow: /private\n"
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            gate = RobotsGate(client, "TestBot", enabled=True)
            assert await gate.allowed("https://example.com/public") is True
            assert await gate.allowed("https://example.com/private/x") is False

    async def test_missing_robots_means_allowed(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(404))
        ) as client:
            gate = RobotsGate(client, "TestBot", enabled=True)
            assert await gate.allowed("https://example.com/anything") is True

    async def test_unreadable_robots_fails_closed(self):
        """If we cannot read robots.txt we decline to scrape. Most scrapers do
        the opposite; for software other people run, this is the safer default."""
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ) as client:
            gate = RobotsGate(client, "TestBot", enabled=True)
            assert await gate.allowed("https://example.com/anything") is False


class TestFetcher:
    async def test_404_maps_to_not_found(self):
        settings = Settings(max_retries=0)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(404))
        ) as client:
            fetcher = Fetcher(settings, client=client)
            with pytest.raises(FetchError) as exc:
                await fetcher.get_text("https://example.com/x", use_cache=False)
            assert exc.value.reason is ReasonCode.NOT_FOUND

    async def test_auth_failure_is_not_retried(self):
        settings = Settings(max_retries=3)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(401)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetcher = Fetcher(settings, client=client)
            with pytest.raises(FetchError):
                await fetcher.get_text("https://example.com/x", use_cache=False)
        assert calls["n"] == 1

    async def test_graphql_errors_surface_as_upstream_error(self):
        settings = Settings(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"errors": [{"message": "bad query"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetcher = Fetcher(settings, client=client)
            with pytest.raises(FetchError) as exc:
                await fetcher.post_graphql(
                    "https://example.com/graphql",
                    "query {}",
                    {},
                    token="t",
                    estimated_cost=1,
                )
            assert exc.value.reason is ReasonCode.UPSTREAM_ERROR
            assert "bad query" in exc.value.detail
