"""Browser workflow for discovery followed by a separate financial lookup."""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .adapters import build_financial_adapters
from .company_crawl import crawl_company, official_website
from .company_reviews import collect_reviews
from .g2_reviews import fetch_g2, G2Error
from .config import get_settings
from .idea import normalize_idea
from .models import CompetitorProfile, Jurisdiction, ProductMatch, SourceRun
from .net import Fetcher, ResponseCache
from .pipeline import collect_discovery, firm_shortlist
from .provenance import Gap, ReasonCode
from .resolve import enrich

router = APIRouter()
assets = Path(__file__).parent / "web"

SEARCH_TTL = 3600
SEARCH_LIMIT = 100
searches: OrderedDict[str, tuple[float, list[ProductMatch]]] = OrderedDict()
# Evidence used by the viability brief is retained server-side with its search.
# The browser cannot supply replacement company records to the brief endpoint.
research_contexts: dict[str, dict] = {}

ID_RULES = {
    Jurisdiction.GB: r"[A-Z0-9]{8}",
    Jurisdiction.US: r"[0-9]{1,10}",
    Jurisdiction.NO: r"[0-9]{9}",
    Jurisdiction.SK: r"[0-9]{8}",
    Jurisdiction.FR: r"[0-9]{9}",
    Jurisdiction.KR: r"[0-9]{8}",
}


class DiscoverRequest(BaseModel):
    idea: str = Field(min_length=3, max_length=2000)
    location: str | None = Field(default=None, max_length=120)


class DiscoverResponse(BaseModel):
    search_id: str
    matches: list[ProductMatch]
    direct_count: int
    evidence: list[ProductMatch]
    sources: list[SourceRun]


class FirmSelection(BaseModel):
    index: int = Field(ge=0, lt=100)
    jurisdiction: Jurisdiction | None = None
    registry_id: str | None = Field(default=None, max_length=24)
    legal_name: str | None = Field(default=None, max_length=200)


class CompanyResearchRequest(BaseModel):
    search_id: str = Field(min_length=24, max_length=50)
    index: int = Field(ge=0, lt=SEARCH_LIMIT)


company_scan_slots = asyncio.Semaphore(4)


class ReviewRequest(BaseModel):
    search_id: str = Field(min_length=24, max_length=50)
    firms: list[FirmSelection] = Field(min_length=1, max_length=100)


class FirmReview(BaseModel):
    index: int
    profile: CompetitorProfile
    identity_inputs: Literal["registry_id", "legal_name", "offering"]


@router.get("/", include_in_schema=False)
def homepage():
    return FileResponse(assets / "index.html", headers={"Cache-Control": "no-cache"})


@router.get("/assets/{name}", include_in_schema=False)
def file_asset(name: str):
    actual = {"app.js", "viability.js", "financial-analysis.js", "business-plan.js", "pitch-deck.js", "downloads.js", "company-research.js", "style.css", "sources.json", "loans.js", "investors.js", "navigation.js", "incorporation.js", "learn.js", "app-icon.png", "favicon.ico", "apple-touch-icon.png"}
    if name not in actual:
        raise HTTPException(status_code=404)
    return FileResponse(assets / name, headers={"Cache-Control": "no-cache"})


@router.post("/idea/discover", response_model=DiscoverResponse)
async def discover(body: DiscoverRequest):
    settings = get_settings()
    try:
        idea = normalize_idea(body.idea, body.location)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    fetcher = Fetcher(settings, cache=ResponseCache(settings.cache_path, settings.cache_ttl_seconds))
    try:
        ranked, runs = await collect_discovery(idea, settings, fetcher, SEARCH_LIMIT)
    finally:
        await fetcher.aclose()

    firms = firm_shortlist(ranked, settings.min_similarity)[:SEARCH_LIMIT]
    evidence = [row for row in ranked if row.kind in ("demand_signal", "technical_analogue")
                and row.similarity >= settings.min_similarity][:12]
    key = secrets.token_urlsafe(24)
    now = time.monotonic()
    while searches and (len(searches) >= SEARCH_LIMIT or
                        next(iter(searches.values()))[0] + SEARCH_TTL < now):
        expired, _ = searches.popitem(last=False)
        research_contexts.pop(expired, None)
    searches[key] = (now, firms)
    research_contexts[key] = {"idea": idea, "evidence": evidence, "sources": runs,
                              "research": {}, "financials": {}, "g2": {}}
    return DiscoverResponse(search_id=key, matches=firms, sources=runs, evidence=evidence,
                            direct_count=sum(row.match_type == "direct" for row in firms))


@router.post("/idea/company-research")
async def company_research(body: CompanyResearchRequest):
    snapshot = searches.get(body.search_id)
    if snapshot is None or time.monotonic() - snapshot[0] > SEARCH_TTL:
        raise HTTPException(status_code=404, detail="This search expired. Run the idea search again.")
    if body.index >= len(snapshot[1]):
        raise HTTPException(status_code=422, detail="Select a company from the current search results.")

    product = snapshot[1][body.index]
    website = official_website(product)
    settings = get_settings()
    try:
        async with asyncio.timeout(27):
            async with company_scan_slots:
                site, reviews = await asyncio.gather(
                    crawl_company(website, settings.user_agent),
                    collect_reviews(product, website, settings),
                )
    except TimeoutError:
        raise HTTPException(status_code=503, detail="Company research timed out. Try again shortly.") from None
    result = {"company": product.name, **site, "reviews": reviews,
              "retrieved_at": datetime.now(timezone.utc).isoformat()}
    if body.search_id in research_contexts:
        research_contexts[body.search_id]["research"][body.index] = result
    return result


@router.post("/idea/company-research/g2")
async def company_g2(body: CompanyResearchRequest, authorization: str | None = Header(default=None)):
    snapshot = searches.get(body.search_id)
    if snapshot is None or time.monotonic() - snapshot[0] > SEARCH_TTL:
        raise HTTPException(status_code=404, detail="This search expired. Run the idea search again.")
    if body.index >= len(snapshot[1]):
        raise HTTPException(status_code=422, detail="Select a company from the current search results.")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail={"code": "hf_login_required", "message": "Enter your personal Hugging Face token to identify your G2 allowance."})
    product = snapshot[1][body.index]
    website = official_website(product)
    try:
        result = await fetch_g2(product, website, get_settings(), token)
        if body.search_id in research_contexts:
            research_contexts[body.search_id]["g2"][body.index] = result.get("feed")
        return result
    except G2Error as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from None


@router.post("/financials/review", response_model=list[FirmReview])
async def review(body: ReviewRequest):
    snapshot = searches.get(body.search_id)
    if snapshot is None or time.monotonic() - snapshot[0] > SEARCH_TTL:
        raise HTTPException(status_code=404, detail="This search expired. Run the idea search again.")
    matches = snapshot[1]
    indices = {item.index for item in body.firms}
    if len(indices) != len(body.firms) or any(index >= len(matches) for index in indices):
        raise HTTPException(status_code=422, detail="Select distinct firms from your search results.")

    for item in body.firms:
        if item.registry_id:
            pattern = ID_RULES.get(item.jurisdiction)
            if not pattern or not re.fullmatch(pattern, item.registry_id.strip().upper()):
                raise HTTPException(status_code=422, detail=f"Check the registry number and country for firm {item.index + 1}.")
        if item.legal_name and item.legal_name.strip() and item.jurisdiction in (None, Jurisdiction.UNKNOWN):
            raise HTTPException(status_code=422, detail=f"Choose a registry country for firm {item.index + 1}.")

    settings = get_settings()
    fetcher = Fetcher(settings, cache=ResponseCache(settings.cache_path, settings.cache_ttl_seconds))
    adapters = build_financial_adapters(settings)
    thresholds = settings.thresholds()
    semaphore = asyncio.Semaphore(settings.max_concurrency)

    async def lookup(item: FirmSelection) -> FirmReview:
        match = matches[item.index]
        registry = item.registry_id.strip().upper() if item.registry_id else None
        name = item.legal_name.strip() if item.legal_name else None
        method = "registry_id" if registry else "legal_name" if name else "offering"
        # A reference market describes the founder's target and cannot establish
        # the competitor's incorporation. Use an explicit registry or website.
        async with semaphore:
            try:
                async with asyncio.timeout(settings.financial_timeout_seconds):
                    result = await enrich(match, adapters, fetcher, Jurisdiction.UNKNOWN, thresholds,
                                          company_name=name, registry_id=registry,
                                          jurisdiction_override=item.jurisdiction,
                                          restrict_routing=True)
            except TimeoutError:
                result = CompetitorProfile(product=match, gaps=[
                    Gap(field="financials", reason=ReasonCode.TIMEOUT)])
            except Exception:
                result = CompetitorProfile(product=match, gaps=[
                    Gap(field="financials", reason=ReasonCode.UPSTREAM_ERROR,
                        detail="The registry response could not be processed.")])
        result.financials = [fact for fact in result.financials
                             if not fact.rejected and fact.period_end and (fact.currency or fact.unit)]
        return FirmReview(index=item.index, profile=result, identity_inputs=method)

    try:
        reviews = await asyncio.gather(*(lookup(item) for item in body.firms))
        if body.search_id in research_contexts:
            research_contexts[body.search_id]["financials"].update({row.index: row.profile for row in reviews})
        return reviews
    finally:
        await fetcher.aclose()

