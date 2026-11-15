"""Entity resolution: product -> company.

This is the riskiest step in the module. Attaching the wrong company's
financials to a competitor produces a confident, specific, wrong number -- the
exact failure mode the research flagged in funding-data products. So the rules
here are conservative by design:

* a match must clear a confidence floor (default 0.80);
* a near-tie between the top two candidates is treated as no match;
* when we refuse, we keep the candidates and report AMBIGUOUS_MATCH with them,
  so a founder can resolve it by hand instead of being told "not found".

Jurisdiction routing is a hint, not a filter: a product with a .co.uk domain is
tried against Companies House first, but Wikidata is always tried because it is
free, keyless and jurisdiction-agnostic.
"""

from __future__ import annotations

import asyncio

from .models import CompanyIdentity, CompetitorProfile, Jurisdiction, ProductMatch
from .net import FetchError, Fetcher
from .provenance import Gap, ReasonCode
from .validation import Thresholds, validate_facts
from .adapters.base import FinancialAdapter

# TLD -> jurisdiction, for routing only.
TLD_JURISDICTION = {
    "uk": Jurisdiction.GB,
    "in": Jurisdiction.IN,
    "us": Jurisdiction.US,
    "no": Jurisdiction.NO,
    "sk": Jurisdiction.SK,
    "kr": Jurisdiction.KR,
    "fr": Jurisdiction.FR,
}


def guess_jurisdiction(product: ProductMatch, idea_hint: Jurisdiction) -> Jurisdiction:
    if product.domain:
        tld = product.domain.rsplit(".", 1)[-1]
        if tld in TLD_JURISDICTION:
            return TLD_JURISDICTION[tld]
    return idea_hint


def rank_adapters(
    adapters: list[FinancialAdapter], jurisdiction: Jurisdiction
) -> list[FinancialAdapter]:
    """Jurisdiction-specific adapters first, then the generic ones."""

    def key(adapter: FinancialAdapter) -> tuple[int, str]:
        if jurisdiction in adapter.jurisdictions and len(adapter.jurisdictions) == 1:
            return (0, adapter.id)
        if jurisdiction in adapter.jurisdictions:
            return (1, adapter.id)
        return (2, adapter.id)

    return sorted(adapters, key=key)


async def resolve_company(
    product: ProductMatch,
    adapters: list[FinancialAdapter],
    fetcher: Fetcher,
    idea_hint: Jurisdiction,
    *,
    company_name: str | None = None,
    registry_id: str | None = None,
    jurisdiction_override: Jurisdiction | None = None,
    restrict_routing: bool = False,
) -> tuple[CompanyIdentity | None, list[Gap], FinancialAdapter | None]:
    """Try adapters in order; return the first confident match."""
    jurisdiction = jurisdiction_override or guess_jurisdiction(product, idea_hint)
    gaps: list[Gap] = []
    best_rejected: list[CompanyIdentity] = []

    search_name = company_name or str(product.signals.get("publisher") or product.name)
    for adapter in rank_adapters(adapters, jurisdiction):
        if restrict_routing and adapter.id != "wikidata" and jurisdiction not in adapter.jurisdictions:
            continue
        if registry_id and (adapter.id == "wikidata" or jurisdiction not in adapter.jurisdictions):
            continue
        unavailable = adapter.available()
        if unavailable is not None:
            gaps.append(unavailable)
            continue

        try:
            if registry_id:
                lookup = getattr(adapter, "lookup_id", None)
                identity = await lookup(registry_id, fetcher) if lookup else None
                candidates = []
            else:
                async with asyncio.timeout(8 if adapter.id == "wikidata" else fetcher.settings.financial_timeout_seconds):
                    identity, candidates = await adapter.identify(search_name, product.domain, fetcher)
        except TimeoutError:
            gaps.append(Gap(field="company", source=adapter.id, reason=ReasonCode.TIMEOUT))
            continue
        except FetchError as exc:
            gaps.append(Gap(field="company", source=adapter.id, reason=exc.reason, detail=exc.detail))
            continue
        if identity is not None:
            return identity, gaps, adapter
        if candidates:
            best_rejected.extend(candidates)
        if adapter.id == "registeruz" and not registry_id:
            gaps.append(Gap(field="company", source=adapter.id, reason=ReasonCode.NOT_FOUND,
                            detail="Use the Slovak companyâ€™s eight-digit IÄŒO to retrieve statements."))

    if restrict_routing and jurisdiction is Jurisdiction.UNKNOWN and not best_rejected:
        gaps.append(Gap(field="company", reason=ReasonCode.JURISDICTION_UNSUPPORTED,
                        detail="Choose the companyâ€™s registry country to check its official filings."))
    if best_rejected:
        names = ", ".join(
            f"{c.name} ({c.registry_id}, {c.match_confidence:.2f})"
            for c in best_rejected[:3]
        )
        gaps.append(
            Gap(
                field="company",
                reason=ReasonCode.AMBIGUOUS_MATCH,
                detail=f"Closest candidates were: {names}. Not attached, "
                "because a wrong match would produce wrong financials.",
            )
        )
    else:
        gaps.append(
            Gap(
                field="company",
                reason=ReasonCode.NOT_FOUND,
                detail=f"No registry record matched '{search_name}'"
                + (f" or domain {product.domain}." if product.domain else "."),
            )
        )
    return None, gaps, None


async def enrich(
    product: ProductMatch,
    adapters: list[FinancialAdapter],
    fetcher: Fetcher,
    idea_hint: Jurisdiction,
    thresholds: Thresholds | None = None,
    *,
    company_name: str | None = None,
    registry_id: str | None = None,
    jurisdiction_override: Jurisdiction | None = None,
    restrict_routing: bool = False,
) -> CompetitorProfile:
    """Build a full profile for one product.

    Validation runs here, once, after every adapter has contributed. Doing it
    centrally rather than per adapter matters: the strongest checks compare
    figures against each other (one period against the next, turnover against
    headcount), so they only work on the assembled set.
    """
    profile = CompetitorProfile(product=product)

    company, gaps, matched_by = await resolve_company(
        product, adapters, fetcher, idea_hint, company_name=company_name, registry_id=registry_id,
        jurisdiction_override=jurisdiction_override, restrict_routing=restrict_routing,
    )
    profile.gaps.extend(gaps)
    profile.company = company
    if company is None or matched_by is None:
        return profile

    def finish() -> CompetitorProfile:
        _, validation_gaps = validate_facts(profile.financials, thresholds)
        profile.gaps.extend(validation_gaps)
        return profile

    try:
        records = getattr(matched_by, "records", None)
        if records:
            facts, profile.documents, fin_gaps = await records(company, fetcher)
        else:
            facts, fin_gaps = await matched_by.financials(company, fetcher)
        profile.financials.extend(facts)
        profile.gaps.extend(fin_gaps)
    except FetchError as exc:
        profile.gaps.append(Gap(field="financials", source=matched_by.id, reason=exc.reason, detail=exc.detail))

    try:
        rounds, fund_gaps = await matched_by.funding(company, fetcher)
        profile.funding.extend(rounds)
        profile.gaps.extend(fund_gaps)
    except FetchError as exc:
        profile.gaps.append(Gap(field="funding", source=matched_by.id, reason=exc.reason, detail=exc.detail))

    # Wikidata is keyless, so it is worth a second pass for anything the
    # matched registry could not supply (employees, reported revenue).
    for adapter in adapters:
        if registry_id or company_name or adapter.id != "wikidata" or adapter is matched_by:
            continue
        if adapter.available() is not None:
            continue
        try:
            async with asyncio.timeout(5):
                extra_identity, _ = await adapter.identify(product.name, product.domain, fetcher)
                if extra_identity is None:
                    continue
                extra_facts, _ = await adapter.financials(extra_identity, fetcher)
        except (TimeoutError, FetchError):
            continue
        have = {f.metric for f in profile.financials}
        profile.financials.extend(f for f in extra_facts if f.metric not in have)

    return finish()
