"""Adapter protocols.

Two kinds of adapter, deliberately separated because they answer different
questions and have different trust levels:

* DiscoveryAdapter -- "what else is like this idea?" (product catalogs, search)
* FinancialAdapter -- "what are that company's numbers?" (statutory registries)

Joining them is entity resolution, which lives in resolve.py and is the part
most likely to produce a wrong answer, so it is kept out of the adapters.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import (
    CompanyIdentity,
    FinancialFact,
    FundingEvent,
    Idea,
    Jurisdiction,
    ProductMatch,
)
from ..net import Fetcher
from ..provenance import Gap


class AdapterUnavailable(Exception):
    """Raised at construction//check time when an adapter cannot run at all."""

    def __init__(self, reason: Gap) -> None:
        super().__init__(reason.human)
        self.gap = reason


@runtime_checkable
class DiscoveryAdapter(Protocol):
    id: str
    name: str
    is_scraper: bool

    def available(self) -> Gap | None:
        """Return a Gap explaining why this adapter cannot run, or None."""
        ...

    async def discover(self, idea: Idea, fetcher: Fetcher, limit: int) -> list[ProductMatch]:
        ...


@runtime_checkable
class FinancialAdapter(Protocol):
    id: str
    name: str
    is_scraper: bool
    jurisdictions: tuple[Jurisdiction, ...]

    def available(self) -> Gap | None:
        ...

    async def identify(
        self, company_name: str, domain: str | None, fetcher: Fetcher
    ) -> tuple[CompanyIdentity | None, list[CompanyIdentity]]:
        """Return (best match or None, all candidates considered)."""
        ...

    async def financials(
        self, company: CompanyIdentity, fetcher: Fetcher
    ) -> tuple[list[FinancialFact], list[Gap]]:
        ...

    async def funding(
        self, company: CompanyIdentity, fetcher: Fetcher
    ) -> tuple[list[FundingEvent], list[Gap]]:
        ...
