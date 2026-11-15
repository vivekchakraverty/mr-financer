"""India MCA (Ministry of Corporate Affairs).

This adapter deliberately does less than it looks like it should, and the
reason matters for the product:

* MCA publishes **no API**. The only free route is the "View Company Master
  Data" web form, which is CAPTCHA-protected. Automating past a CAPTCHA is both
  a terms breach and brittle, so this does not attempt it.
* Master data, when looked up, gives registration status, incorporation date,
  authorised capital and paid-up capital. It does **not** give turnover or
  profit.
* Balance sheet and P&L exist in MCA's records but are sold as paid documents
  (Form AOC-4 attachments) through "Get Certified Copies". There is no free
  bulk route.

So for Indian competitors this module returns:
  - a deep link a founder can click to do the lookup themselves, and
  - REQUIRES_PAID_DOCUMENT for revenue and profit, which the UI should render
    as an explanation rather than an empty cell.

If you later want Indian financials automated, the realistic options are a
commercial aggregator that licenses MCA data, or accepting a CIN and uploaded
filing from the founder. Both are product decisions, not scraping problems.
Verified against https://www.mca.gov.in/MinistryV2/aboutmasterdata.html, which
describes the free service and mentions no API.
"""

from __future__ import annotations

import re

from ..models import CompanyIdentity, FinancialFact, FundingEvent, Jurisdiction
from ..net import Fetcher
from ..provenance import AccessMethod, Gap, Provenance, ReasonCode, Trust

MASTER_DATA_URL = (
    "https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do"
)
CERTIFIED_COPIES_URL = (
    "https://www.mca.gov.in/mcafoportal/getCertifiedCopies.do"
)

# A CIN is 21 characters: L/U + 5-digit industry + 2-letter state + 4-digit year
# + 3-letter ownership + 6-digit registration number.
CIN_RE = re.compile(r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")


def looks_like_cin(text: str) -> bool:
    return bool(CIN_RE.search(text.upper()))


class MCAIndiaAdapter:
    id = "mca_india"
    name = "India MCA"
    is_scraper = True  # the only free route is a web form, if enabled at all
    jurisdictions = (Jurisdiction.IN,)

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def available(self) -> Gap | None:
        if not self.enabled:
            return Gap(
                field="financials",
                reason=ReasonCode.ADAPTER_DISABLED,
                source=self.id,
                detail="MCA has no API and its free lookup is CAPTCHA-gated. "
                "Set MRF_ENABLE_SCRAPER_MCA=true to emit manual-lookup links.",
            )
        return None

    def _prov(self, url: str) -> Provenance:
        return Provenance(
            source=self.id,
            source_name=self.name,
            method=AccessMethod.OFFICIAL_BULK_DATA,
            trust=Trust.STATUTORY,
            url=url,
            note="MCA master data is authoritative but must be looked up "
            "manually; financial statements are paid documents.",
        )

    async def identify(
        self, company_name: str, domain: str | None, fetcher: Fetcher
    ) -> tuple[CompanyIdentity | None, list[CompanyIdentity]]:
        # No searchable API, so we cannot confirm an entity. Returning a
        # confident-looking identity here would be a fabrication.
        return None, []

    async def financials(
        self, company: CompanyIdentity, fetcher: Fetcher
    ) -> tuple[list[FinancialFact], list[Gap]]:
        """No free automated financials exist. Explain, and hand over links."""
        lookup = MASTER_DATA_URL
        gaps = [
            Gap(
                field="turnover",
                reason=ReasonCode.REQUIRES_PAID_DOCUMENT,
                source=self.id,
                detail="Indian private limiteds file AOC-4 with MCA, but the "
                f"statements are sold as certified copies: {CERTIFIED_COPIES_URL}",
            ),
            Gap(
                field="profit_before_tax",
                reason=ReasonCode.REQUIRES_PAID_DOCUMENT,
                source=self.id,
                detail="Same as turnover: available only as a paid document.",
            ),
            Gap(
                field="paid_up_capital",
                reason=ReasonCode.NOT_FOUND,
                source=self.id,
                detail="Free to read, but only through the CAPTCHA-gated "
                f"master-data form: {lookup}",
            ),
        ]
        return [], gaps

    async def funding(
        self, company: CompanyIdentity, fetcher: Fetcher
    ) -> tuple[list[FundingEvent], list[Gap]]:
        return [], []
