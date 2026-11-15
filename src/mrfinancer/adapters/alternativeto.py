"""AlternativeTo scraper -- the gap-filler for "what else is like this".

OFF BY DEFAULT. Enable with MRF_ENABLE_SCRAPER_ALTERNATIVETO=true.

Read this before shipping it to users:

* There is no public API, so this parses HTML. HTML changes without notice, so
  treat the selectors below as a maintenance cost, not a one-off.
* robots.txt is checked before every request and we fail closed.
* Scraping may breach the site's terms of use even when robots.txt allows the
  path. That is a decision for whoever ships the app, which is why this is an
  explicit opt-in and why the report labels these rows as scraped.

The parser is written to degrade: unknown markup yields zero results and a
PARSE_FAILED gap, never an exception and never a half-invented row.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from ..models import Idea, ProductMatch
from ..net import Fetcher, FetchError
from ..provenance import AccessMethod, Gap, Provenance, ReasonCode, Trust
from ..textutil import extract_domain, text_overlap

BASE = "https://alternativeto.net"
SEARCH_PATH = "/browse/search"

# Several candidate selectors, tried in order, because the site has shipped at
# least two different card layouts. Add to the front when it changes again.
CARD_SELECTORS = [
    "li[class*='AppListItem']",
    "div[class*='AppListItem']",
    "div[class*='app-list-item']",
    "li.app",
]
NAME_SELECTORS = ["h2 a", "h3 a", "a[class*='Heading']", "a.app-name"]
DESC_SELECTORS = ["p[class*='Description']", "p.description", "p"]


class AlternativeToAdapter:
    id = "alternativeto"
    name = "AlternativeTo (scraped)"
    is_scraper = True

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.last_gap: Gap | None = None

    def available(self) -> Gap | None:
        if not self.enabled:
            return Gap(
                field="discovery",
                reason=ReasonCode.ADAPTER_DISABLED,
                source=self.id,
                detail="Scrapers are opt-in. Set "
                "MRF_ENABLE_SCRAPER_ALTERNATIVETO=true to use this, having "
                "checked the site's terms yourself.",
            )
        return None

    def _provenance(self, url: str | None) -> Provenance:
        return Provenance(
            source=self.id,
            source_name="AlternativeTo",
            method=AccessMethod.SCRAPED_PAGE,
            trust=Trust.UNVERIFIED,
            url=url,
            note="Scraped from HTML. Unstable and community-maintained; "
            "confirm anything you act on.",
        )

    @staticmethod
    def _first(node, selectors: list[str]):
        for sel in selectors:
            found = node.select_one(sel)
            if found is not None:
                return found
        return None

    def _parse(self, html: str, idea: Idea, limit: int) -> list[ProductMatch]:
        soup = BeautifulSoup(html, "lxml")

        cards = []
        for sel in CARD_SELECTORS:
            cards = soup.select(sel)
            if cards:
                break
        if not cards:
            self.last_gap = Gap(
                field="discovery",
                reason=ReasonCode.PARSE_FAILED,
                source=self.id,
                detail="No product cards matched any known selector; the page "
                "layout has probably changed. Update CARD_SELECTORS.",
            )
            return []

        out: list[ProductMatch] = []
        for card in cards[: limit * 2]:
            link = self._first(card, NAME_SELECTORS)
            if link is None:
                continue
            name = link.get_text(strip=True)
            if not name:
                continue
            href = link.get("href") or ""
            page_url = href if href.startswith("http") else f"{BASE}{href}"

            desc_node = self._first(card, DESC_SELECTORS)
            desc = desc_node.get_text(strip=True) if desc_node else None

            # AlternativeTo links out to the product's own site; that outbound
            # link is the only entity-resolution signal on the page.
            homepage = None
            for a in card.select("a[href^='http']"):
                d = extract_domain(a.get("href"))
                if d and "alternativeto.net" not in (a.get("href") or ""):
                    homepage = a.get("href")
                    break

            out.append(
                ProductMatch(
                    name=name,
                    tagline=desc,
                    url=page_url,
                    homepage=homepage,
                    domain=extract_domain(homepage),
                    signals={"source_page": page_url},
                    similarity=text_overlap(idea.keywords, name, desc),
                    provenance=self._provenance(page_url),
                )
            )
            if len(out) >= limit:
                break
        return out

    async def discover(
        self, idea: Idea, fetcher: Fetcher, limit: int
    ) -> list[ProductMatch]:
        self.last_gap = None
        results: dict[str, ProductMatch] = {}

        for query in idea.search_queries[:2]:
            if len(results) >= limit:
                break
            url = f"{BASE}{SEARCH_PATH}"
            try:
                html = await fetcher.get_text(
                    url,
                    params={"q": query},
                    check_robots=True,
                )
            except FetchError as exc:
                self.last_gap = Gap(
                    field="discovery",
                    reason=exc.reason,
                    source=self.id,
                    detail=exc.detail,
                )
                continue

            for match in self._parse(html, idea, limit):
                results.setdefault(match.name.lower(), match)

        return list(results.values())[:limit]
