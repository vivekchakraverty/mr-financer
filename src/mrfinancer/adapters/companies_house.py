"""UK Companies House -- the best free source of real filed financials.

Free official API. Auth is HTTP Basic with the API key as username and an empty
password.

  search:         GET  /search/companies?q=
  profile:        GET  /company/{company_number}
  filing history: GET  /company/{company_number}/filing-history?category=accounts
  document:       GET  https://document-api.company-information.service.gov.uk
                       /document/{document_id}/content   (Accept: application/xhtml+xml)

Spec: https://developer.company-information.service.gov.uk

Two honest limitations, surfaced as gaps rather than hidden:

1. Companies House search has no website field, so matching a product's domain
   to a registered company is inference. We refuse below a confidence floor
   instead of attaching plausible-looking numbers to the wrong company -- the
   worst failure this module could have.
2. Small companies may file filleted accounts that legally omit turnover. That
   is reported as NO_PUBLIC_FILING_OBLIGATION, not as "no revenue".
"""

from __future__ import annotations

from datetime import date

from ..ixbrl import extract_facts
from ..models import (
    CompanyIdentity,
    FinancialFact,
    FundingEvent,
    Jurisdiction,
    Metric,
)
from ..net import Fetcher, FetchError
from ..provenance import AccessMethod, Gap, Provenance, ReasonCode, Trust
from ..textutil import domain_root, name_similarity

API = "https://api.company-information.service.gov.uk"
DOC_API = "https://document-api.company-information.service.gov.uk"

ACTIVE_STATUSES = {"active", "open"}


def _parse_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


class CompaniesHouseAdapter:
    id = "companies_house"
    name = "UK Companies House"
    is_scraper = False
    jurisdictions = (Jurisdiction.GB,)

    def __init__(self, api_key: str | None, min_confidence: float = 0.80) -> None:
        self.api_key = api_key
        self.min_confidence = min_confidence

    def available(self) -> Gap | None:
        if not self.api_key:
            return Gap(
                field="financials",
                reason=ReasonCode.CREDENTIALS_MISSING,
                source=self.id,
                detail="Get a free key at developer.company-information.service.gov.uk "
                "and set MRF_COMPANIES_HOUSE_KEY.",
            )
        return None

    @property
    def _auth(self) -> tuple[str, str]:
        assert self.api_key is not None
        return (self.api_key, "")

    def _prov(self, url: str | None, note: str | None = None) -> Provenance:
        return Provenance(
            source=self.id,
            source_name=self.name,
            method=AccessMethod.OFFICIAL_API,
            trust=Trust.STATUTORY,
            url=url,
            note=note,
        )

    # -- identification -----------------------------------------------------

    def _score(self, product_name: str, domain: str | None, company_title: str) -> tuple[float, str]:
        by_name = name_similarity(product_name, company_title)
        basis = f"name {by_name:.2f}"
        score = by_name

        root = domain_root(domain)
        if root:
            by_domain = name_similarity(root, company_title)
            if by_domain > score:
                score, basis = by_domain, f"domain root {by_domain:.2f}"
            elif by_domain > 0.7:
                # Agreement between two weak signals is stronger than either.
                score = min(1.0, score + 0.1)
                basis = f"name {by_name:.2f} + domain root {by_domain:.2f}"
        return score, basis

    async def lookup_id(self, registry_id: str, fetcher: Fetcher) -> CompanyIdentity | None:
        row = await fetcher.get_json(f"{API}/company/{registry_id}", auth=self._auth())
        if row.get("company_number") != registry_id or not row.get("company_name"):
            return None
        return CompanyIdentity(name=row["company_name"], jurisdiction=Jurisdiction.GB,
                               registry_id=registry_id, status=row.get("company_status"),
                               match_confidence=1, match_basis="Registry identifier supplied by user",
                               provenance=self._prov(f"https://find-and-update.company-information.service.gov.uk/company/{registry_id}"))

    async def identify(
        self, company_name: str, domain: str | None, fetcher: Fetcher
    ) -> tuple[CompanyIdentity | None, list[CompanyIdentity]]:
        query = company_name.strip()
        if not query:
            return None, []

        try:
            data = await fetcher.get_json(
                f"{API}/search/companies",
                params={"q": query, "items_per_page": 20},
                auth=self._auth,
            )
        except FetchError:
            return None, []

        candidates: list[CompanyIdentity] = []
        for item in (data or {}).get("items", []) or []:
            title = (item.get("title") or "").strip()
            number = item.get("company_number")
            if not title or not number:
                continue
            score, basis = self._score(company_name, domain, title)
            status = (item.get("company_status") or "").lower()
            # A dissolved company with a great name match is usually the wrong
            # entity for a product that is currently on sale.
            if status and status not in ACTIVE_STATUSES:
                score *= 0.75
                basis += " (not active)"

            candidates.append(
                CompanyIdentity(
                    name=title,
                    jurisdiction=Jurisdiction.GB,
                    registry_id=str(number),
                    domain=domain,
                    status=item.get("company_status"),
                    incorporated_on=_parse_date(item.get("date_of_creation")),
                    registered_address=item.get("address_snippet"),
                    match_confidence=round(min(1.0, score), 3),
                    match_basis=basis,
                    provenance=self._prov(
                        f"https://find-and-update.company-information.service.gov.uk"
                        f"/company/{number}"
                    ),
                )
            )

        if not candidates:
            return None, []

        candidates.sort(key=lambda c: c.match_confidence, reverse=True)
        for c in candidates:
            c.candidates_considered = len(candidates)

        best = candidates[0]
        if best.match_confidence < self.min_confidence:
            return None, candidates[:5]
        # If the runner-up is nearly as good, the match is not safe either.
        if len(candidates) > 1 and candidates[1].match_confidence >= best.match_confidence - 0.03:
            return None, candidates[:5]
        return best, candidates[:5]

    # -- financials ---------------------------------------------------------

    async def _accounts_document_id(
        self, number: str, fetcher: Fetcher
    ) -> tuple[str | None, date | None, str | None]:
        """Newest accounts filing: (document_id, made_up_to, filing page url)."""
        data = await fetcher.get_json(
            f"{API}/company/{number}/filing-history",
            params={"category": "accounts", "items_per_page": 25},
            auth=self._auth,
        )
        items = (data or {}).get("items", []) or []
        best: tuple[str, date | None, str | None] | None = None
        best_when = date.min
        for item in items:
            meta = ((item.get("links") or {}).get("document_metadata")) or ""
            if not meta:
                continue
            doc_id = meta.rstrip("/").rsplit("/", 1)[-1]
            made_up_to = _parse_date(
                (item.get("description_values") or {}).get("made_up_date")
            ) or _parse_date(item.get("date"))
            when = made_up_to or date.min
            if when >= best_when:
                best_when = when
                best = (doc_id, made_up_to, item.get("description"))
        if best is None:
            return None, None, None
        return best[0], best[1], best[2]

    async def financials(
        self, company: CompanyIdentity, fetcher: Fetcher
    ) -> tuple[list[FinancialFact], list[Gap]]:
        number = company.registry_id
        if not number:
            return [], [
                Gap(field="financials", reason=ReasonCode.NOT_FOUND, source=self.id)
            ]

        try:
            doc_id, made_up_to, description = await self._accounts_document_id(
                number, fetcher
            )
        except FetchError as exc:
            return [], [
                Gap(
                    field="financials",
                    reason=exc.reason,
                    source=self.id,
                    detail=exc.detail,
                )
            ]

        if not doc_id:
            return [], [
                Gap(
                    field="financials",
                    reason=ReasonCode.NOT_FOUND,
                    source=self.id,
                    detail="No accounts filing with a retrievable document.",
                )
            ]

        doc_url = f"{DOC_API}/document/{doc_id}/content"
        try:
            xhtml = await fetcher.get_text(
                doc_url,
                headers={"Accept": "application/xhtml+xml"},
                auth=self._auth,
            )
        except FetchError as exc:
            return [], [
                Gap(
                    field="financials",
                    reason=exc.reason,
                    source=self.id,
                    detail=f"Could not fetch accounts document. {exc.detail}",
                )
            ]

        # Every period, not just the newest. Accounts restate the prior year,
        # and that comparative is what makes a thousand-fold scale error
        # detectable, so collapsing to one period here would throw away the
        # best evidence validation has.
        parsed = extract_facts(xhtml)
        metrics_present = {f.metric for f in parsed}
        filing_page = (
            "https://find-and-update.company-information.service.gov.uk"
            f"/company/{number}/filing-history"
        )
        note = description or "Filed annual accounts"
        if made_up_to:
            note = f"{note}, made up to {made_up_to.isoformat()}"

        out: list[FinancialFact] = []
        for fact in parsed:
            out.append(
                FinancialFact(
                    metric=fact.metric,
                    value=fact.value,
                    currency=fact.currency
                    or ("GBP" if fact.metric is not Metric.EMPLOYEES else None),
                    unit="count" if fact.metric is Metric.EMPLOYEES else None,
                    period_start=fact.period_start,
                    period_end=fact.period_end or made_up_to,
                    as_filed=True,
                    source_scale=fact.scale,
                    source_decimals=fact.decimals,
                    source_text=fact.text,
                    provenance=self._prov(filing_page, note),
                )
            )

        gaps: list[Gap] = []
        if not out:
            gaps.append(
                Gap(
                    field="financials",
                    reason=ReasonCode.PARSE_FAILED,
                    source=self.id,
                    detail="Accounts retrieved but no recognized iXBRL facts "
                    "found. Older filings are scanned PDFs with no XBRL data.",
                )
            )
        elif Metric.TURNOVER not in metrics_present:
            gaps.append(
                Gap(
                    field="turnover",
                    reason=ReasonCode.NO_PUBLIC_FILING_OBLIGATION,
                    source=self.id,
                    detail="Accounts filed without a turnover figure, which "
                    "small companies are permitted to omit. This is not a "
                    "statement that turnover is zero.",
                )
            )
        return out, gaps

    async def funding(
        self, company: CompanyIdentity, fetcher: Fetcher
    ) -> tuple[list[FundingEvent], list[Gap]]:
        # Companies House records share allotments, not round narratives.
        return [], []

    async def health(self, fetcher: Fetcher) -> str:
        """Known-good company number: 00445790 (Marks and Spencer plc)."""
        data = await fetcher.get_json(
            f"{API}/company/00445790", auth=self._auth, use_cache=False
        )
        return f"ok, read {(data or {}).get('company_name', 'unknown')}"
