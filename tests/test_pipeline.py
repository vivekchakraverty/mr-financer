"""End-to-end tests with a mock transport.

The property under test that matters most: when company identity is uncertain,
the pipeline attaches NO financials and says why. A wrong match here would put
another company's revenue next to a competitor's name.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from mrfinancer.adapters.companies_house import CompaniesHouseAdapter
from mrfinancer.adapters.wikidata import WikidataAdapter
from mrfinancer.config import Settings
from mrfinancer.idea import normalize_idea
from mrfinancer.models import Idea, Metric, ProductMatch
from mrfinancer.net import Fetcher
from mrfinancer.pipeline import _dedupe, analyze_idea
from mrfinancer.provenance import (
    AccessMethod,
    Provenance,
    ReasonCode,
    Trust,
)
from mrfinancer.report import render_markdown
from mrfinancer.resolve import enrich, guess_jurisdiction

ACCOUNTS = (Path(__file__).parent / "fixtures" / "accounts_sample.xhtml").read_text(
    encoding="utf-8"
)
BROKEN_ACCOUNTS = (
    Path(__file__).parent / "fixtures" / "accounts_scale_error.xhtml"
).read_text(encoding="utf-8")


def ph_provenance() -> Provenance:
    return Provenance(
        source="producthunt",
        source_name="Product Hunt",
        method=AccessMethod.OFFICIAL_API,
        trust=Trust.OFFICIAL,
        url="https://www.producthunt.com/posts/ledgerly",
    )


def make_product(name="Ledgerly", domain="ledgerly.co.uk") -> ProductMatch:
    return ProductMatch(
        name=name,
        tagline="Invoice chasing for freelancers",
        url="https://www.producthunt.com/posts/ledgerly",
        homepage=f"https://{domain}" if domain else None,
        domain=domain,
        similarity=0.8,
        provenance=ph_provenance(),
    )


def companies_house_handler(
    *, search_items: list[dict], accounts: str | None = ACCOUNTS
):
    """Returns the handler callable so tests can wrap it and observe calls."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "query.wikidata.org" in (request.url.host or ""):
            return httpx.Response(200, json={"results": {"bindings": []}})
        if path == "/search/companies":
            return httpx.Response(200, json={"items": search_items})
        if path.endswith("/filing-history"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "date": "2024-09-01",
                            "description": "accounts-with-accounts-type-total-exemption-full",
                            "description_values": {"made_up_date": "2024-03-31"},
                            "links": {
                                "document_metadata": "https://frontend-doc-api.company-information.service.gov.uk/document/abc123"
                            },
                        },
                        {
                            "date": "2023-09-01",
                            "description": "accounts",
                            "description_values": {"made_up_date": "2023-03-31"},
                            "links": {
                                "document_metadata": "https://frontend-doc-api.company-information.service.gov.uk/document/old999"
                            },
                        },
                    ]
                },
            )
        if "/document/" in path and path.endswith("/content"):
            if accounts is None:
                return httpx.Response(404)
            return httpx.Response(
                200,
                text=accounts,
                headers={"Content-Type": "application/xhtml+xml"},
            )
        if path.startswith("/company/"):
            return httpx.Response(
                200,
                json={
                    "company_name": "LEDGERLY LTD",
                    "company_number": "12345678",
                    "company_status": "active",
                    "sic_codes": ["62012"],
                },
            )
        return httpx.Response(404)

    return handler


def companies_house_transport(
    *, search_items: list[dict], accounts: str | None = ACCOUNTS
) -> httpx.MockTransport:
    return httpx.MockTransport(
        companies_house_handler(search_items=search_items, accounts=accounts)
    )


UNIQUE_MATCH = [
    {
        "title": "LEDGERLY LTD",
        "company_number": "12345678",
        "company_status": "active",
        "date_of_creation": "2019-06-04",
        "address_snippet": "1 Example Street, London",
    },
    {
        "title": "ZENITH HAULAGE LIMITED",
        "company_number": "99999999",
        "company_status": "active",
    },
]

NEAR_TIE = [
    {
        "title": "LEDGERLY LTD",
        "company_number": "12345678",
        "company_status": "active",
    },
    {
        "title": "LEDGERLY LIMITED",
        "company_number": "87654321",
        "company_status": "active",
    },
]


class TestEnrichment:
    async def test_confident_match_attaches_filed_financials(self):
        settings = Settings(companies_house_key="k", max_retries=0)
        transport = companies_house_transport(search_items=UNIQUE_MATCH)
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(settings, client=client)
            profile = await enrich(
                make_product(),
                [CompaniesHouseAdapter("k"), WikidataAdapter()],
                fetcher,
                normalize_idea("invoice chasing").jurisdiction_hint,
            )

        assert profile.company is not None
        assert profile.company.registry_id == "12345678"
        assert profile.has_statutory_financials

        turnover = profile.latest(Metric.TURNOVER)
        assert turnover is not None
        assert turnover.value == pytest.approx(4_812_000)
        assert turnover.currency == "GBP"
        assert turnover.as_filed is True
        # Provenance is mandatory and must point somewhere a human can check.
        assert turnover.provenance.url
        assert turnover.provenance.trust is Trust.STATUTORY

    async def test_newest_accounts_filing_is_chosen(self):
        """Filing history is not ordered for us; picking the first item would
        silently report last year's numbers."""
        settings = Settings(companies_house_key="k", max_retries=0)
        requested: list[str] = []
        inner = companies_house_handler(search_items=UNIQUE_MATCH)

        def handler(request: httpx.Request) -> httpx.Response:
            if "/document/" in request.url.path:
                requested.append(request.url.path)
            return inner(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetcher = Fetcher(settings, client=client)
            await enrich(
                make_product(),
                [CompaniesHouseAdapter("k")],
                fetcher,
                normalize_idea("invoices").jurisdiction_hint,
            )

        assert any("abc123" in p for p in requested)
        assert not any("old999" in p for p in requested)

    async def test_near_tie_refuses_to_attach_anything(self):
        settings = Settings(companies_house_key="k", max_retries=0)
        transport = companies_house_transport(search_items=NEAR_TIE)
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(settings, client=client)
            profile = await enrich(
                make_product(),
                [CompaniesHouseAdapter("k")],
                fetcher,
                normalize_idea("invoices").jurisdiction_hint,
            )

        assert profile.company is None
        assert profile.financials == []
        reasons = {g.reason for g in profile.gaps}
        assert ReasonCode.AMBIGUOUS_MATCH in reasons
        # And the candidates are reported so a human can finish the job.
        detail = next(
            g.detail for g in profile.gaps if g.reason is ReasonCode.AMBIGUOUS_MATCH
        )
        assert "12345678" in detail and "87654321" in detail

    async def test_no_match_is_reported_as_not_found(self):
        settings = Settings(companies_house_key="k", max_retries=0)
        transport = companies_house_transport(search_items=[])
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(settings, client=client)
            profile = await enrich(
                make_product(name="Nonesuch", domain="nonesuch.io"),
                [CompaniesHouseAdapter("k")],
                fetcher,
                normalize_idea("invoices").jurisdiction_hint,
            )
        assert profile.company is None
        assert any(g.reason is ReasonCode.NOT_FOUND for g in profile.gaps)

    async def test_missing_credentials_become_a_gap_not_a_crash(self):
        settings = Settings(max_retries=0)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(404))
        ) as client:
            fetcher = Fetcher(settings, client=client)
            profile = await enrich(
                make_product(),
                [CompaniesHouseAdapter(None)],
                fetcher,
                normalize_idea("invoices").jurisdiction_hint,
            )
        assert any(
            g.reason is ReasonCode.CREDENTIALS_MISSING for g in profile.gaps
        )


class TestJurisdictionRouting:
    @pytest.mark.parametrize(
        "domain,expected",
        [("acme.co.uk", "GB"), ("acme.in", "IN"), ("acme.com", "UNKNOWN")],
    )
    def test_tld_routes_jurisdiction(self, domain, expected):
        product = make_product(domain=domain)
        idea = normalize_idea("a generic tool")
        assert guess_jurisdiction(product, idea.jurisdiction_hint).value == expected

    def test_idea_hint_is_used_when_tld_is_neutral(self):
        product = make_product(domain="acme.com")
        idea = normalize_idea("GST invoicing for Indian SMEs")
        assert guess_jurisdiction(product, idea.jurisdiction_hint).value == "IN"


class TestDedupe:
    def test_same_domain_from_two_sources_merges(self):
        api = make_product()
        api.signals = {"producthunt_votes": 410}
        scraped = ProductMatch(
            name="Ledgerly",
            domain="ledgerly.co.uk",
            tagline=None,
            description="Chases invoices",
            similarity=0.5,
            signals={"source_page": "https://alternativeto.net/x"},
            provenance=Provenance(
                source="alternativeto",
                source_name="AlternativeTo",
                method=AccessMethod.SCRAPED_PAGE,
                trust=Trust.UNVERIFIED,
            ),
        )
        merged = _dedupe([scraped, api])
        assert len(merged) == 1
        kept = merged[0]
        # The API-sourced copy survives, but keeps the scraped detail.
        assert kept.provenance.source == "producthunt"
        assert kept.description == "Chases invoices"
        assert kept.signals["producthunt_votes"] == 410
        assert kept.similarity == pytest.approx(0.8)

    def test_different_products_are_kept(self):
        a = make_product(name="Ledgerly", domain="ledgerly.co.uk")
        b = make_product(name="Zenith Haulage", domain="zenith.co.uk")
        assert len(_dedupe([a, b])) == 2


class TestReportRendering:
    async def test_gaps_render_as_explanations_not_blanks(self):
        settings = Settings(companies_house_key="k", max_retries=0)
        transport = companies_house_transport(search_items=NEAR_TIE)
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(settings, client=client)
            profile = await enrich(
                make_product(),
                [CompaniesHouseAdapter("k")],
                fetcher,
                normalize_idea("invoices").jurisdiction_hint,
            )

        from mrfinancer.models import IdeaReport

        report = IdeaReport(
            idea=normalize_idea("invoice chasing for freelancers"),
            competitors=[profile],
        )
        md = render_markdown(report)
        assert "not resolved" in md
        assert "Several companies matched" in md

    async def test_filed_figures_carry_period_and_badge(self):
        settings = Settings(companies_house_key="k", max_retries=0)
        transport = companies_house_transport(search_items=UNIQUE_MATCH)
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(settings, client=client)
            profile = await enrich(
                make_product(),
                [CompaniesHouseAdapter("k")],
                fetcher,
                normalize_idea("invoices").jurisdiction_hint,
            )

        from mrfinancer.models import IdeaReport

        md = render_markdown(
            IdeaReport(idea=normalize_idea("invoice chasing"), competitors=[profile])
        )
        assert "GBP 4.81m" in md
        assert "2024-03-31" in md
        assert "filed" in md


class TestMalformedFilingIsCaught:
    """A filing whose own scale attributes contradict each other.

    The parser is correct and still produces a wrong number, which is exactly
    the case unit tests on the parser cannot reach.
    """

    async def test_broken_figure_is_withheld_and_explained(self):
        settings = Settings(companies_house_key="k", max_retries=0)
        transport = companies_house_transport(
            search_items=UNIQUE_MATCH, accounts=BROKEN_ACCOUNTS
        )
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(settings, client=client)
            profile = await enrich(
                make_product(),
                [CompaniesHouseAdapter("k")],
                fetcher,
                normalize_idea("invoices").jurisdiction_hint,
                settings.thresholds(),
            )

        # The 2024 figure, parsed faithfully as 4,812, is the newest. It must
        # not be what the report shows.
        latest = profile.latest(Metric.TURNOVER)
        assert latest is not None
        assert latest.value == pytest.approx(4_812_000)
        assert latest.period_end == date(2023, 3, 31)

        withheld = [
            g for g in profile.gaps if g.reason is ReasonCode.FAILED_VALIDATION
        ]
        assert withheld, "the contradictory figure should have been withheld"
        assert "different scale attributes" in withheld[0].detail

        md = render_markdown(
            __import__(
                "mrfinancer.models", fromlist=["IdeaReport"]
            ).IdeaReport(
                idea=normalize_idea("invoice chasing"), competitors=[profile]
            )
        )
        # 4,812 as a bare number must appear nowhere as a reported turnover.
        assert "GBP 4.8k" not in md
        assert "withheld" in md

    async def test_strict_profile_withholds_more_than_balanced(self):
        settings_balanced = Settings(
            companies_house_key="k", max_retries=0, validation_profile="balanced"
        )
        settings_strict = Settings(
            companies_house_key="k", max_retries=0, validation_profile="strict"
        )
        counts: dict[str, int] = {}
        for label, cfg in (
            ("balanced", settings_balanced),
            ("strict", settings_strict),
        ):
            transport = companies_house_transport(
                search_items=UNIQUE_MATCH, accounts=BROKEN_ACCOUNTS
            )
            async with httpx.AsyncClient(transport=transport) as client:
                fetcher = Fetcher(cfg, client=client)
                profile = await enrich(
                    make_product(),
                    [CompaniesHouseAdapter("k")],
                    fetcher,
                    normalize_idea("invoices").jurisdiction_hint,
                    cfg.thresholds(),
                )
            counts[label] = sum(1 for f in profile.financials if f.rejected)

        assert counts["strict"] >= counts["balanced"]


class TestFullPipeline:
    async def test_runs_with_no_sources_configured(self):
        """A founder with no API keys must still get a usable answer that
        explains what to configure, not a stack trace."""
        settings = Settings(max_retries=0, search_provider="none")
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(404))
        ) as client:
            fetcher = Fetcher(settings, client=client)
            report = await analyze_idea(
                "an app that chases late invoices for freelancers",
                settings,
                fetcher=fetcher,
            )

        assert report.competitors == []
        disabled = [r for r in report.runs if not r.enabled]
        assert disabled, "expected sources to report why they are unavailable"
        assert all(r.reason for r in disabled)
        md = render_markdown(report)
        assert "No sufficiently similar listings" in md
