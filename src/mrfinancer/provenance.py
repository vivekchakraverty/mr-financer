"""Provenance and reason codes.

Design rule for this whole module: a number without a source is a bug, and a
missing number must say *why* it is missing. The research behind Mr. Financer
found that the single most common failure in funding-data products is quoting a
figure that does not resolve to a publisher's own page. These types exist to
make that failure structurally impossible: every value carries where it came
from and when, and every gap carries a machine-readable reason the UI can
explain to a founder.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class AccessMethod(str, Enum):
    """How a value was obtained. Surfaced to the user, because an official API
    and a scraped page do not deserve equal trust."""

    OFFICIAL_API = "official_api"
    OFFICIAL_BULK_DATA = "official_bulk_data"
    PUBLIC_SPARQL = "public_sparql"
    SEARCH_API = "search_api"
    SCRAPED_PAGE = "scraped_page"
    DERIVED = "derived"  # computed by us from other sourced values
    USER_SUPPLIED = "user_supplied"


class Trust(str, Enum):
    """Coarse trust tier, so the report can sort and the UI can badge."""

    STATUTORY = "statutory"  # filed with a registrar; legally accountable
    OFFICIAL = "official"  # publisher's own API about its own data
    THIRD_PARTY = "third_party"  # someone else's aggregation
    ESTIMATE = "estimate"  # explicitly an estimate
    UNVERIFIED = "unverified"  # scraped, unstable, or self-reported


class ReasonCode(str, Enum):
    """Why a field is empty. Never leave a gap unexplained."""

    NOT_FOUND = "not_found"
    AMBIGUOUS_MATCH = "ambiguous_match"
    REQUIRES_PAID_DOCUMENT = "requires_paid_document"
    NO_PUBLIC_FILING_OBLIGATION = "no_public_filing_obligation"
    JURISDICTION_UNSUPPORTED = "jurisdiction_unsupported"
    ADAPTER_DISABLED = "adapter_disabled"
    CREDENTIALS_MISSING = "credentials_missing"
    ROBOTS_DISALLOWED = "robots_disallowed"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PARSE_FAILED = "parse_failed"
    FAILED_VALIDATION = "failed_validation"
    UPSTREAM_ERROR = "upstream_error"
    TIMEOUT = "timeout"


HUMAN_REASONS: dict[ReasonCode, str] = {
    ReasonCode.NOT_FOUND: "No record found in this source.",
    ReasonCode.AMBIGUOUS_MATCH: (
        "Several companies matched and none closely enough to attach figures safely."
    ),
    ReasonCode.REQUIRES_PAID_DOCUMENT: (
        "The registry holds this but only sells it as a paid document."
    ),
    ReasonCode.NO_PUBLIC_FILING_OBLIGATION: (
        "This company has no obligation to file figures publicly in this jurisdiction."
    ),
    ReasonCode.JURISDICTION_UNSUPPORTED: "No connector covers this jurisdiction yet.",
    ReasonCode.ADAPTER_DISABLED: "This source is switched off in configuration.",
    ReasonCode.CREDENTIALS_MISSING: "No API key configured for this source.",
    ReasonCode.ROBOTS_DISALLOWED: "The site's robots.txt disallows this path.",
    ReasonCode.RATE_LIMITED: "Rate limit reached; try again later.",
    ReasonCode.BUDGET_EXHAUSTED: "The source's query budget for this window is spent.",
    ReasonCode.PARSE_FAILED: "The page or payload did not match the expected shape.",
    ReasonCode.FAILED_VALIDATION: (
        "A figure was found but failed a sanity check, so it was withheld "
        "rather than reported."
    ),
    ReasonCode.UPSTREAM_ERROR: "The source returned an error.",
    ReasonCode.TIMEOUT: "The source did not respond in time.",
}


class Provenance(BaseModel):
    """Where a single value came from."""

    source: str = Field(description="Adapter id, e.g. 'companies_house'")
    source_name: str = Field(description="Human name of the publisher")
    method: AccessMethod
    trust: Trust
    url: str | None = Field(
        default=None, description="Page or endpoint a human can open to check"
    )
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    note: str | None = None

    def citation(self) -> str:
        """One-line citation for the report."""
        when = self.retrieved_at.date().isoformat()
        return f"{self.source_name} ({self.url or 'no link'}), retrieved {when}"


class Gap(BaseModel):
    """An explained absence. Rendered to the user instead of a blank cell."""

    field: str
    reason: ReasonCode
    source: str | None = None
    detail: str | None = None

    @property
    def human(self) -> str:
        base = HUMAN_REASONS.get(self.reason, self.reason.value)
        return f"{base} {self.detail}".strip() if self.detail else base
