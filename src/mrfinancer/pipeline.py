"""Orchestration for module 1: idea text in, competitor report out."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

from .adapters import build_discovery_adapters, build_financial_adapters
from .adapters.base import DiscoveryAdapter
from .config import Settings, get_settings
from .idea import normalize_idea
from .models import CompetitorProfile, Idea, IdeaReport, ProductMatch, SourceRun
from .net import Fetcher, FetchError, ResponseCache
from .provenance import Trust
from .resolve import enrich
from .ranking import market_terms, rank_matches, tokens
from .textutil import name_similarity, normalize_company_name

# Discovery sources ranked by how much we trust their listing, used to decide
# which copy of a duplicate product survives the merge.
TRUST_ORDER = {
    Trust.STATUTORY: 0,
    Trust.OFFICIAL: 1,
    Trust.THIRD_PARTY: 2,
    Trust.ESTIMATE: 3,
    Trust.UNVERIFIED: 4,
}


def _url_identity(url: str | None) -> tuple[str, str] | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        return parsed.hostname.casefold().removeprefix("www."), parsed.path.rstrip("/")
    except ValueError:
        return None


def _same_offering(a: ProductMatch, b: ProductMatch) -> bool:
    if a.provenance.source == b.provenance.source and a.source_record_id and b.source_record_id:
        return a.source_record_id == b.source_record_id
    if a.kind == "demand_signal" or b.kind == "demand_signal":
        return False
    if a.kind == "supplier" or b.kind == "supplier":
        return False
    if (a.kind == "local_business") != (b.kind == "local_business"):
        return False
    similarity = name_similarity(a.name, b.name)
    if a.kind == "local_business":
        a_market, b_market = market_terms(a.market or ""), market_terms(b.market or "")
        if not a_market or not b_market or not (a_market <= b_market or b_market <= a_market):
            return False
        # Locations with the same chain name stay distinct unless both sources
        # give the same street address.
        a_address, b_address = a.signals.get("address"), b.signals.get("address")
        return bool(a_address and b_address and similarity >= 0.92
                    and tokens(str(a_address)) == tokens(str(b_address)))
    if a.domain and a.domain == b.domain and similarity >= 0.88:
        return True
    url_a, url_b = _url_identity(a.homepage), _url_identity(b.homepage)
    return bool(url_a and url_a == url_b and similarity >= 0.80)


def _dedupe(matches: list[ProductMatch]) -> list[ProductMatch]:
    """Keep one listing per evidenced identity and retain all its source links."""
    kept: list[ProductMatch] = []
    for match in sorted(matches, key=lambda m: (
        m.kind == "technical_analogue",
        TRUST_ORDER.get(m.provenance.trust, 9),
        -m.similarity,
    )):
        duplicate = next((candidate for candidate in kept if _same_offering(match, candidate)), None)
        if duplicate is None:
            match.evidence = match.evidence or [match.provenance]
            kept.append(match)
            continue
        recorded = {(p.source, p.url) for p in duplicate.evidence}
        for item in match.evidence or [match.provenance]:
            key = (item.source, item.url)
            if key not in recorded:
                duplicate.evidence.append(item)
                recorded.add(key)
        for key, value in match.signals.items():
            duplicate.signals.setdefault(key, value)
        duplicate.domain = duplicate.domain or match.domain
        duplicate.homepage = duplicate.homepage or match.homepage
        duplicate.tagline = duplicate.tagline or match.tagline
        duplicate.description = duplicate.description or match.description
        duplicate.topics = list(dict.fromkeys(duplicate.topics + match.topics))
        duplicate.similarity = max(duplicate.similarity, match.similarity)
    return kept


async def _run_discovery(
    adapter: DiscoveryAdapter, idea: Idea, fetcher: Fetcher, limit: int
) -> tuple[list[ProductMatch], SourceRun]:
    started = time.monotonic()

    def run(ok: bool, results: int, reason: str | None) -> SourceRun:
        return SourceRun(
            adapter=adapter.id,
            enabled=True,
            ok=ok,
            results=results,
            reason=reason,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    unavailable = adapter.available()
    if unavailable is not None:
        return [], SourceRun(
            adapter=adapter.id,
            enabled=False,
            ok=False,
            reason=unavailable.human,
            elapsed_ms=0,
        )

    applicable = getattr(adapter, "skip_reason", None)
    reason = applicable(idea) if applicable else None
    if reason:
        return [], run(True, 0, reason)

    try:
        async with asyncio.timeout(fetcher.settings.discovery_timeout_seconds):
            matches = await adapter.discover(idea, fetcher, limit)
    except TimeoutError:
        return [], run(False, 0, "Discovery source timed out")
    except FetchError as exc:
        return [], run(False, 0, f"{exc.reason.value}: {exc.detail}".strip(": "))
    except Exception as exc:  # an adapter must never kill the run
        return [], run(False, 0, f"Unexpected adapter response ({type(exc).__name__})")

    return matches, run(True, len(matches), None)


async def collect_discovery(
    idea: Idea, settings: Settings, fetcher: Fetcher, depth: int
) -> tuple[list[ProductMatch], list[SourceRun]]:
    adapters = build_discovery_adapters(settings)
    results = await asyncio.gather(*(
        _run_discovery(adapter, idea, fetcher, depth) for adapter in adapters
    ))
    merged = _dedupe([entry for matches, _ in results for entry in matches])
    return rank_matches(idea, merged), [run for _, run in results]


def firm_shortlist(ranked: list[ProductMatch], minimum: float) -> list[ProductMatch]:
    """Keep one result per evidenced business, preferring direct offerings."""
    firms: list[ProductMatch] = []
    seen: set[tuple] = set()
    for match in sorted(ranked, key=lambda product: product.match_type != "direct"):
        if match.kind not in ("product", "local_business", "supplier") or match.similarity < minimum:
            continue
        publisher = normalize_company_name(str(match.signals.get("publisher") or ""))
        identities = set()
        if match.domain:
            identities.add(("domain", match.domain))
        if publisher:
            identities.add(("publisher", publisher))
        if not identities:
            identities.add(("name", normalize_company_name(match.name) or match.name.casefold(),
                            (match.market or "").casefold()))
        if seen & identities:
            seen.update(identities)
            continue
        seen.update(identities)
        firms.append(match)
    return firms


async def analyze_idea(
    raw_idea: str,
    settings: Settings | None = None,
    fetcher: Fetcher | None = None,
    *,
    location: str | None = None,
) -> IdeaReport:
    settings = settings or get_settings()
    idea = normalize_idea(raw_idea, location)

    owns_fetcher = fetcher is None
    if fetcher is None:
        cache = ResponseCache(settings.cache_path, settings.cache_ttl_seconds)
        fetcher = Fetcher(settings, cache=cache)

    try:
        financial = build_financial_adapters(settings)
        ranked, runs = await collect_discovery(
            idea, settings, fetcher, min(100, max(18, settings.max_competitors * 3))
        )
        documented_offerings = [m for m in ranked if m.kind != "demand_signal"]
        demand = [m for m in ranked if m.kind == "demand_signal"]
        shortlist = [
            m for m in (*documented_offerings, *demand)
            if m.similarity > 0 and m.similarity >= settings.min_similarity
        ][:settings.max_competitors]

        # Enrichment is the expensive half (several requests per product), so
        # it is bounded by max_concurrency rather than fired all at once.
        semaphore = asyncio.Semaphore(settings.max_concurrency)
        thresholds = settings.thresholds()

        async def bounded(match: ProductMatch) -> CompetitorProfile:
            if match.kind in ("demand_signal", "technical_analogue"):
                return CompetitorProfile(product=match)
            async with semaphore:
                return await enrich(
                    match, financial, fetcher, idea.jurisdiction_hint, thresholds
                )

        profiles = await asyncio.gather(
            *(bounded(m) for m in shortlist), return_exceptions=True
        )

        competitors: list[CompetitorProfile] = []
        for match, outcome in zip(shortlist, profiles):
            if isinstance(outcome, BaseException):
                competitors.append(CompetitorProfile(product=match))
            else:
                competitors.append(outcome)

        for adapter in financial:
            unavailable = adapter.available()
            runs.append(
                SourceRun(
                    adapter=adapter.id,
                    kind="financial",
                    enabled=unavailable is None,
                    ok=unavailable is None,
                    results=sum(
                        1
                        for c in competitors
                        if c.company
                        and c.company.provenance
                        and c.company.provenance.source == adapter.id
                    ),
                    reason=unavailable.human if unavailable else None,
                )
            )

        return IdeaReport(idea=idea, competitors=competitors, runs=runs)
    finally:
        if owns_fetcher:
            await fetcher.aclose()
