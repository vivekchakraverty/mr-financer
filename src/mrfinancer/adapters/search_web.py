"""Search-API discovery.

This is the gap-filler that keeps the hybrid strategy from needing many
site-specific scrapers: point it at any one of three search APIs and it finds
competitor products wherever they are listed. Each provider has a different
payload shape, normalized here.

A search result is weaker evidence than a catalog entry, so everything from
here is tagged Trust.THIRD_PARTY and the report says so.
"""

from __future__ import annotations

from typing import Any

from ..models import Idea, ProductMatch
from ..net import Fetcher, FetchError
from ..provenance import AccessMethod, Gap, Provenance, ReasonCode, Trust
from ..textutil import extract_domain, text_overlap

PROVIDERS = {
    "brave": {
        "url": "https://api.search.brave.com/res/v1/web/search",
        "method": "GET",
    },
    "serper": {"url": "https://google.serper.dev/search", "method": "POST"},
    "tavily": {"url": "https://api.tavily.com/search", "method": "POST"},
}

# Query suffixes that bias results towards competitor lists rather than blogs.
QUERY_SHAPES = [
    "{q} alternatives",
    "best {q} tools",
    "{q} competitors",
]


class WebSearchAdapter:
    id = "web_search"
    name = "Web search"
    is_scraper = False

    def __init__(self, provider: str, api_key: str | None) -> None:
        self.provider = (provider or "none").lower()
        self.api_key = api_key

    def available(self) -> Gap | None:
        if self.provider == "none":
            return Gap(
                field="discovery",
                reason=ReasonCode.ADAPTER_DISABLED,
                source=self.id,
                detail="Set MRF_SEARCH_PROVIDER to brave, serper or tavily.",
            )
        if self.provider not in PROVIDERS:
            return Gap(
                field="discovery",
                reason=ReasonCode.ADAPTER_DISABLED,
                source=self.id,
                detail=f"Unknown provider '{self.provider}'.",
            )
        if not self.api_key:
            return Gap(
                field="discovery",
                reason=ReasonCode.CREDENTIALS_MISSING,
                source=self.id,
                detail="Set MRF_SEARCH_API_KEY.",
            )
        return None

    def _provenance(self, url: str | None) -> Provenance:
        return Provenance(
            source=self.id,
            source_name=f"Web search ({self.provider})",
            method=AccessMethod.SEARCH_API,
            trust=Trust.THIRD_PARTY,
            url=url,
            note="Found by search, not from a product catalog. Verify before use.",
        )

    async def _run_query(self, query: str, fetcher: Fetcher, count: int) -> list[dict[str, Any]]:
        spec = PROVIDERS[self.provider]
        assert self.api_key is not None

        if self.provider == "brave":
            data = await fetcher.get_json(
                spec["url"],
                params={"q": query, "count": count},
                headers={
                    "X-Subscription-Token": self.api_key,
                    "Accept": "application/json",
                },
            )
            return ((data or {}).get("web") or {}).get("results") or []

        if self.provider == "serper":
            resp = await fetcher.client.post(
                spec["url"],
                json={"q": query, "num": count},
                headers={"X-API-KEY": self.api_key},
            )
            if resp.status_code != 200:
                raise FetchError(ReasonCode.UPSTREAM_ERROR, f"HTTP {resp.status_code}")
            return (resp.json() or {}).get("organic") or []

        resp = await fetcher.client.post(
            spec["url"],
            json={
                "api_key": self.api_key,
                "query": query,
                "max_results": count,
                "search_depth": "basic",
            },
        )
        if resp.status_code != 200:
            raise FetchError(ReasonCode.UPSTREAM_ERROR, f"HTTP {resp.status_code}")
        return (resp.json() or {}).get("results") or []

    @staticmethod
    def _fields(row: dict[str, Any]) -> tuple[str, str | None, str | None]:
        """Normalize (title, url, snippet) across providers."""
        title = row.get("title") or row.get("name") or ""
        url = row.get("url") or row.get("link")
        snippet = (
            row.get("description")
            or row.get("snippet")
            or row.get("content")
            or None
        )
        return str(title).strip(), url, snippet

    async def discover(
        self, idea: Idea, fetcher: Fetcher, limit: int
    ) -> list[ProductMatch]:
        out: dict[str, ProductMatch] = {}
        seeds = idea.search_queries[:2] or [" ".join(idea.keywords[:3])]
        queries = [shape.format(q=s) for s in seeds for shape in QUERY_SHAPES[:2]]

        for query in queries:
            if len(out) >= limit:
                break
            try:
                rows = await self._run_query(query, fetcher, count=min(10, limit))
            except FetchError:
                continue

            for row in rows:
                title, url, snippet = self._fields(row)
                if not title or not url:
                    continue
                domain = extract_domain(url)
                # Without a usable domain a search hit cannot be resolved to a
                # company, which is most of its value here. Skip it.
                if not domain:
                    continue
                if domain in out:
                    continue
                out[domain] = ProductMatch(
                    name=title.split(" - ")[0].split(" | ")[0].strip() or title,
                    tagline=snippet,
                    url=url,
                    homepage=f"https://{domain}",
                    domain=domain,
                    signals={"search_query": query},
                    similarity=text_overlap(idea.keywords, title, snippet),
                    provenance=self._provenance(url),
                )
                if len(out) >= limit:
                    break

        return list(out.values())
