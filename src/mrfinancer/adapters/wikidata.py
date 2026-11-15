"""Wikidata company facts via the public SPARQL endpoint.

Free, no key, no scraping. Matched on official website (P856), which is the
strongest identity signal we have for a product: the domain we already hold.

What it realistically yields: revenue (P2139), employee count (P1128) and
inception (P571) for companies notable enough to have an item -- so mostly the
larger competitors in a set. It almost never has startup funding rounds, so
funding() reports that honestly rather than returning nothing silently.

Trust tier is THIRD_PARTY: Wikidata is community-edited, and a revenue figure
there is someone's transcription of a report, not a filing.
"""

from __future__ import annotations

from datetime import date
import re
from typing import Any

from ..models import (
    CompanyIdentity,
    FinancialFact,
    FundingEvent,
    Jurisdiction,
    Metric,
)
from ..net import Fetcher, FetchError
from ..textutil import extract_domain
from ..provenance import AccessMethod, Gap, Provenance, ReasonCode, Trust

ENDPOINT = "https://query.wikidata.org/sparql"

# Unit QIDs for the currencies we care about. Wikidata gives the unit as an
# entity URI, so a value without a known unit is reported with no currency
# rather than guessed as USD.
CURRENCY_QIDS = {
    "Q4917": "USD",
    "Q25224": "GBP",
    "Q4916": "EUR",
    "Q80524": "INR",
    "Q8146": "INR",  # Indian rupee (alt item used in some statements)
    "Q8343": "AUD",
    "Q1104069": "CAD",
    "Q25417": "CHF",
    "Q8152": "JPY",
    "Q39099": "SGD",
}

QUERY = """
SELECT ?item ?itemLabel ?website ?revenue ?revenueUnit ?revenuePoint ?employees ?inception
WHERE {
  ?item wdt:P856 ?website .
  FILTER(REGEX(LCASE(STR(?website)), "^https?://([a-z0-9-]+[.])*%(domain)s(:[0-9]+)?([/?#]|$)"))
  OPTIONAL {
    ?item p:P2139 ?revStatement .
    ?revStatement psv:P2139 ?revValue .
    ?revValue wikibase:quantityAmount ?revenue .
    OPTIONAL { ?revValue wikibase:quantityUnit ?revenueUnit }
    OPTIONAL { ?revStatement pq:P585 ?revenuePoint }
  }
  OPTIONAL { ?item wdt:P1128 ?employees }
  OPTIONAL { ?item wdt:P571 ?inception }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
LIMIT 10
"""


def _year_end(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _qid(uri: str | None) -> str | None:
    if not uri:
        return None
    return uri.rstrip("/").rsplit("/", 1)[-1]


class WikidataAdapter:
    id = "wikidata"
    name = "Wikidata"
    is_scraper = False
    jurisdictions = (
        Jurisdiction.GB,
        Jurisdiction.IN,
        Jurisdiction.US,
        Jurisdiction.UNKNOWN,
    )

    def available(self) -> Gap | None:
        return None  # no credentials needed

    def _prov(self, url: str | None, note: str | None = None) -> Provenance:
        return Provenance(
            source=self.id,
            source_name="Wikidata",
            method=AccessMethod.PUBLIC_SPARQL,
            trust=Trust.THIRD_PARTY,
            url=url,
            note=note or "Community-edited; transcribed from company reports.",
        )

    async def _query(self, domain: str, fetcher: Fetcher) -> list[dict[str, Any]]:
        if not re.fullmatch(r"[a-z0-9.-]+", domain):
            return []
        sparql = QUERY % {"domain": domain.lower().replace(".", "[.]")}
        data = await fetcher.get_json(
            ENDPOINT,
            params={"query": sparql, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
        )
        rows = ((data or {}).get("results") or {}).get("bindings") or []
        return [row for row in rows
                if extract_domain((row.get("website") or {}).get("value")) == domain]

    async def identify(
        self, company_name: str, domain: str | None, fetcher: Fetcher
    ) -> tuple[CompanyIdentity | None, list[CompanyIdentity]]:
        if not domain:
            return None, []
        try:
            rows = await self._query(domain, fetcher)
        except FetchError:
            return None, []
        if len({(row.get("item") or {}).get("value") for row in rows}) != 1:
            return None, []

        row = rows[0]
        item_uri = (row.get("item") or {}).get("value")
        label = (row.get("itemLabel") or {}).get("value") or company_name
        identity = CompanyIdentity(
            name=label,
            jurisdiction=Jurisdiction.UNKNOWN,
            registry_id=_qid(item_uri),
            domain=domain,
            incorporated_on=_year_end((row.get("inception") or {}).get("value")),
            # Website match on the exact domain is a strong signal, but Wikidata
            # is not a registry, so this never reaches 1.0.
            match_confidence=0.9,
            match_basis=f"Wikidata P856 official website uses {domain}",
            candidates_considered=len(rows),
            provenance=self._prov(item_uri),
        )
        return identity, [identity]

    async def financials(
        self, company: CompanyIdentity, fetcher: Fetcher
    ) -> tuple[list[FinancialFact], list[Gap]]:
        if not company.domain:
            return [], []
        try:
            rows = await self._query(company.domain, fetcher)
        except FetchError as exc:
            return [], [
                Gap(
                    field="financials",
                    reason=exc.reason,
                    source=self.id,
                    detail=exc.detail,
                )
            ]

        rows = [row for row in rows if _qid((row.get("item") or {}).get("value")) == company.registry_id]
        item_uri = next(((row.get("item") or {}).get("value") for row in rows), None)
        facts: list[FinancialFact] = []
        seen: set[tuple[Metric, date | None]] = set()

        for row in rows:
            revenue = (row.get("revenue") or {}).get("value")
            if revenue:
                period_end = _year_end((row.get("revenuePoint") or {}).get("value"))
                key = (Metric.TURNOVER, period_end)
                if key not in seen:
                    seen.add(key)
                    currency = CURRENCY_QIDS.get(
                        _qid((row.get("revenueUnit") or {}).get("value")) or ""
                    )
                    try:
                        value = float(revenue)
                    except ValueError:
                        value = None
                    if value is not None:
                        facts.append(
                            FinancialFact(
                                metric=Metric.TURNOVER,
                                value=value,
                                currency=currency,
                                period_end=period_end,
                                as_filed=False,
                                provenance=self._prov(
                                    item_uri,
                                    "Reported revenue, not a filing. "
                                    + (
                                        "Currency not stated in the source."
                                        if not currency
                                        else ""
                                    ),
                                ),
                            )
                        )

            employees = (row.get("employees") or {}).get("value")
            if employees and (Metric.EMPLOYEES, None) not in seen:
                seen.add((Metric.EMPLOYEES, None))
                try:
                    facts.append(
                        FinancialFact(
                            metric=Metric.EMPLOYEES,
                            value=float(employees),
                            unit="count",
                            as_filed=False,
                            provenance=self._prov(item_uri),
                        )
                    )
                except ValueError:
                    pass

        return facts, []

    async def funding(
        self, company: CompanyIdentity, fetcher: Fetcher
    ) -> tuple[list[FundingEvent], list[Gap]]:
        return [], [
            Gap(
                field="funding",
                reason=ReasonCode.NOT_FOUND,
                source=self.id,
                detail="Wikidata rarely records startup funding rounds. For "
                "round data the free options are patchy (OpenVC, press "
                "releases); Crunchbase and Dealroom are paid.",
            )
        ]

    async def health(self, fetcher: Fetcher) -> str:
        rows = await self._query("apple.com", fetcher)
        return f"ok, {len(rows)} binding(s) for apple.com"
