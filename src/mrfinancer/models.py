"""Domain models for module 1 (product idea research)."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from .provenance import Gap, Provenance


class Jurisdiction(str, Enum):
    GB = "GB"
    IN = "IN"
    US = "US"
    NO = "NO"
    SK = "SK"
    KR = "KR"
    FR = "FR"
    UNKNOWN = "UNKNOWN"


class Idea(BaseModel):
    """The founder's input, normalized."""

    raw: str
    keywords: list[str] = Field(default_factory=list)
    category_terms: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    jurisdiction_hint: Jurisdiction = Jurisdiction.UNKNOWN
    location: str | None = None
    delivery_mode: Literal["digital", "local", "general"] = "general"


class ProductMatch(BaseModel):
    """A product or service that looks similar to the idea."""

    name: str
    tagline: str | None = None
    description: str | None = None
    url: str | None = None
    homepage: str | None = None
    domain: str | None = None
    topics: list[str] = Field(default_factory=list)
    # Popularity signals differ by source; keep them raw and labelled.
    signals: dict[str, float | int | str] = Field(default_factory=dict)
    similarity: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Our similarity to the idea, 0-1"
    )
    provenance: Provenance
    evidence: list[Provenance] = Field(default_factory=list)
    kind: Literal["product", "local_business", "supplier", "demand_signal", "technical_analogue"] = "product"
    market: str | None = None
    source_record_id: str | None = None
    match_type: Literal["direct", "similar", "related"] = "related"
    score_reasons: list[str] = Field(default_factory=list)


class CompanyIdentity(BaseModel):
    """A company we believe sits behind a product."""

    name: str
    jurisdiction: Jurisdiction = Jurisdiction.UNKNOWN
    registry_id: str | None = Field(
        default=None, description="Company number / CIN / CIK"
    )
    domain: str | None = None
    status: str | None = None
    incorporated_on: date | None = None
    sic_codes: list[str] = Field(default_factory=list)
    registered_address: str | None = None
    # How confident we are that this company is the one behind the product.
    match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    match_basis: str | None = Field(
        default=None, description="e.g. 'exact domain', 'name 0.93 + jurisdiction'"
    )
    candidates_considered: int = 0
    provenance: Provenance | None = None


class Metric(str, Enum):
    TURNOVER = "turnover"
    NET_PROFIT = "net_profit"
    TOTAL_ASSETS = "total_assets"
    TOTAL_LIABILITIES = "total_liabilities"
    PROFIT_BEFORE_TAX = "profit_before_tax"
    OPERATING_PROFIT = "operating_profit"
    NET_ASSETS = "net_assets"
    CASH = "cash"
    EMPLOYEES = "employees"
    AUTHORISED_CAPITAL = "authorised_capital"
    PAID_UP_CAPITAL = "paid_up_capital"
    TOTAL_FUNDING = "total_funding"
    LAST_ROUND_AMOUNT = "last_round_amount"
    VALUATION = "valuation"


class Severity(str, Enum):
    """How badly a fact failed a sanity check."""

    INFO = "info"
    WARN = "warn"  # report it, but flag it
    REJECT = "reject"  # withhold it; report the reason instead


class FactIssue(BaseModel):
    """One failed sanity check on one figure.

    These exist because a scale or sign error produces a number that looks
    entirely plausible. A plausible wrong number is worse than no number, so
    every figure is checked and anything that cannot be true is withheld.
    """

    rule: str = Field(description="Stable rule id, e.g. 'scale_jump'")
    severity: Severity
    message: str


class FinancialFact(BaseModel):
    """One financial number, with the period it covers and where it came from.

    There is deliberately no plain `float` anywhere in the public surface of
    this module. A figure without a period and a source is not reportable.
    """

    metric: Metric
    value: float | None = None
    currency: str | None = Field(default=None, description="ISO 4217, e.g. GBP, INR")
    unit: str | None = Field(default=None, description="e.g. 'count' for employees")
    period_start: date | None = None
    period_end: date | None = None
    statement_scope: Literal["consolidated", "standalone"] | None = None
    as_filed: bool = Field(
        default=False, description="True only if taken from a statutory filing"
    )
    provenance: Provenance

    # Populated by validation.py. Empty means every check passed.
    issues: list[FactIssue] = Field(default_factory=list)
    # The raw presentation details, kept so a sanity check can question them.
    source_scale: int | None = None
    source_decimals: int | None = None
    source_text: str | None = None

    @property
    def rejected(self) -> bool:
        return any(i.severity is Severity.REJECT for i in self.issues)

    @property
    def suspect(self) -> bool:
        return any(i.severity is Severity.WARN for i in self.issues)

    @property
    def period_label(self) -> str:
        if self.period_end and self.period_start:
            return f"{self.period_start.isoformat()} to {self.period_end.isoformat()}"
        if self.period_end:
            return f"FY to {self.period_end.isoformat()}"
        return "period not stated"


class FundingEvent(BaseModel):
    round_label: str | None = None
    announced_on: date | None = None
    amount: float | None = None
    currency: str | None = None
    investors: list[str] = Field(default_factory=list)
    provenance: Provenance


class FilingDocument(BaseModel):
    title: str
    period_end: date | None = None
    provenance: Provenance


class CompetitorProfile(BaseModel):
    """Everything we assembled about one similar product and its company."""

    product: ProductMatch
    company: CompanyIdentity | None = None
    financials: list[FinancialFact] = Field(default_factory=list)
    funding: list[FundingEvent] = Field(default_factory=list)
    documents: list[FilingDocument] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)

    def latest(self, metric: Metric) -> FinancialFact | None:
        """Newest reportable figure for a metric. Rejected facts are invisible
        here: a figure that failed a sanity check must never reach the UI as a
        number, only as the reason it was withheld."""
        facts = [
            f
            for f in self.financials
            if f.metric is metric and f.value is not None and not f.rejected
        ]
        if not facts:
            return None
        return max(facts, key=lambda f: (f.period_end or date.min))

    def series(self, metric: Metric) -> list[FinancialFact]:
        """All reportable periods for a metric, oldest first."""
        facts = [
            f
            for f in self.financials
            if f.metric is metric and f.value is not None and not f.rejected
        ]
        return sorted(facts, key=lambda f: (f.period_end or date.min))

    @property
    def has_statutory_financials(self) -> bool:
        return any(
            f.as_filed and f.value is not None and not f.rejected
            for f in self.financials
        )

    @property
    def suspect_facts(self) -> list[FinancialFact]:
        return [f for f in self.financials if f.suspect and not f.rejected]


class SourceRun(BaseModel):
    """What each adapter actually did, so a run is auditable."""

    adapter: str
    enabled: bool
    ok: bool
    results: int = 0
    reason: str | None = None
    elapsed_ms: int = 0
    kind: Literal["discovery", "financial"] = "discovery"


class IdeaReport(BaseModel):
    idea: Idea
    competitors: list[CompetitorProfile] = Field(default_factory=list)
    runs: list[SourceRun] = Field(default_factory=list)
    generated_at: date = Field(default_factory=date.today)

    @property
    def coverage(self) -> str:
        n = len(self.competitors)
        with_co = sum(1 for c in self.competitors if c.company)
        with_fin = sum(1 for c in self.competitors if c.has_statutory_financials)
        return (
            f"{n} market results, {with_co} resolved to a company, "
            f"{with_fin} with filed financials"
        )
