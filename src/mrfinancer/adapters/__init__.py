"""Adapter construction from settings."""

from __future__ import annotations

from ..config import Settings
from .alternativeto import AlternativeToAdapter
from .app_store import AppStoreAdapter
from .base import DiscoveryAdapter, FinancialAdapter
from .companies_house import CompaniesHouseAdapter
from .mca_india import MCAIndiaAdapter
from .international import InpiAdapter, NorwayAdapter, OpenDartAdapter, SecAdapter, SlovakiaAdapter
from .places import FoursquarePlacesAdapter, FoursquareSnapshotAdapter, OverpassAdapter
from .public_discovery import GitHubAdapter, ShowHNAdapter, TEDAdapter, UKContractsAdapter
from .producthunt import ProductHuntAdapter
from .search_web import WebSearchAdapter
from .wikidata import WikidataAdapter

__all__ = [
    "AlternativeToAdapter",
    "CompaniesHouseAdapter",
    "MCAIndiaAdapter",
    "ProductHuntAdapter",
    "WebSearchAdapter",
    "WikidataAdapter",
    "build_discovery_adapters",
    "build_financial_adapters",
]


def build_discovery_adapters(settings: Settings) -> list[DiscoveryAdapter]:
    """Order matters: API sources first so their results win on dedupe, with
    scrapers only filling what is left (the hybrid strategy, made literal)."""
    return [
        ProductHuntAdapter(settings.producthunt_token, settings.producthunt_client_id,
                           settings.producthunt_client_secret, settings.producthunt_enabled),
        GitHubAdapter(),
        ShowHNAdapter(),
        AppStoreAdapter(),
        OverpassAdapter(settings.overpass_url),
        TEDAdapter(),
        UKContractsAdapter(),
        FoursquareSnapshotAdapter(settings.foursquare_snapshot),
        FoursquarePlacesAdapter(settings.foursquare_service_key),
        WebSearchAdapter(settings.search_provider, settings.search_api_key),
        AlternativeToAdapter(settings.enable_scraper_alternativeto),
    ]


def build_financial_adapters(settings: Settings) -> list[FinancialAdapter]:
    return [
        CompaniesHouseAdapter(
            settings.companies_house_key, settings.min_company_match_confidence
        ),
        NorwayAdapter(),
        SlovakiaAdapter(),
        SecAdapter(settings.sec_contact),
        OpenDartAdapter(settings.opendart_key),
        InpiAdapter(settings.inpi_username, settings.inpi_password),
        WikidataAdapter(),
        MCAIndiaAdapter(settings.enable_scraper_mca),
    ]
