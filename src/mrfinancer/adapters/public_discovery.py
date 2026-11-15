"""Public software, launch and procurement discovery endpoints."""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote, urlparse

from ..models import Idea, ProductMatch
from ..net import Fetcher, FetchError
from ..provenance import AccessMethod, Provenance, Trust
from ..ranking import query_variants, rank_matches, words
from ..textutil import extract_domain


class PublicSource:
    is_scraper = False

    def available(self) -> None:
        return None

    def evidence(self, url: str, note: str) -> Provenance:
        return Provenance(source=self.id, source_name=self.name,
                          method=AccessMethod.OFFICIAL_API, trust=Trust.OFFICIAL,
                          url=url, note=note)


class DigitalSource(PublicSource):
    def skip_reason(self, idea: Idea) -> str | None:
        if idea.delivery_mode == "local":
            return "Skipped for a physical service idea"
        return None


class GitHubAdapter(DigitalSource):
    id, name = "github", "GitHub REST"
    endpoint = "https://api.github.com/search/repositories"

    async def discover(self, idea: Idea, fetcher: Fetcher, limit: int) -> list[ProductMatch]:
        if idea.delivery_mode == "local":
            return []
        out = {}
        for query in query_variants(idea)[:3]:
            data = await fetcher.get_json(self.endpoint, params={
                "q": f"{' '.join(words(query))} in:name,description,topics fork:false archived:false",
                "per_page": min(30, limit),
            }, headers={"Accept": "application/vnd.github+json"})
            for row in data["items"]:
                url = row["html_url"]
                site = row.get("homepage")
                if site and urlparse(site).scheme not in ("http", "https"):
                    site = None
                out[url] = ProductMatch(
                    name=row["name"].replace("-", " "), tagline=row.get("description"),
                    homepage=site, domain=extract_domain(site), url=url,
                    topics=[t.replace("-", " ") for t in row.get("topics", [])],
                    kind="technical_analogue", source_record_id=str(row["id"]),
                    signals={"github_stars": row.get("stargazers_count", 0)},
                    provenance=self.evidence(url, "Public repository. Commercial availability is not established."),
                )
        return rank_matches(idea, list(out.values()))[:limit]


class ShowHNAdapter(DigitalSource):
    id, name = "show_hn", "Show HN (Algolia)"
    endpoint = "https://hn.algolia.com/api/v1/search"

    async def discover(self, idea: Idea, fetcher: Fetcher, limit: int) -> list[ProductMatch]:
        if idea.delivery_mode == "local":
            return []
        out = {}
        for query in query_variants(idea)[:4]:
            data = await fetcher.get_json(self.endpoint, params={
                "query": query, "tags": "show_hn", "hitsPerPage": min(30, limit),
            })
            for row in data["hits"]:
                title = (row.get("title") or "").removeprefix("Show HN: ").strip()
                record_id = str(row.get("objectID") or "")
                if not title or not record_id:
                    continue
                url = "https://news.ycombinator.com/item?id=" + quote(record_id, safe="")
                site = row.get("url")
                if site and urlparse(site).scheme not in ("http", "https"):
                    site = None
                # Story content can contain HTML, including links. Keep its text only.
                from bs4 import BeautifulSoup
                description = BeautifulSoup(row.get("story_text") or "", "html.parser").get_text(" ", strip=True)
                out[record_id] = ProductMatch(
                    name=title, description=description[:1100] or None,
                    url=url, homepage=site, domain=extract_domain(site),
                    kind="product" if site else "technical_analogue", source_record_id=record_id,
                    signals={"hn_points": row.get("points") or 0},
                    provenance=self.evidence(url, "Community-submitted launch. The landing page or deployment status may change."),
                )
        return rank_matches(idea, list(out.values()))[:limit]


def localized(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return localized(value.get("eng") or next(iter(value.values()), None))
    if isinstance(value, list):
        return " ".join(text for item in value if (text := localized(item)))
    return ""


class TEDAdapter(PublicSource):
    id, name = "eu_ted", "EU TED Search"
    endpoint = "https://api.ted.europa.eu/v3/notices/search"

    async def discover(self, idea: Idea, fetcher: Fetcher, limit: int) -> list[ProductMatch]:
        out = {}
        last_error = None
        successful = False
        for query in query_variants(idea)[:2]:
            term = " ".join(words(query))
            if not term:
                continue
            try:
                data = await fetcher.post_json(self.endpoint, {
                    "query": f"FT ~ ({term})",
                    "fields": ["publication-number", "notice-title"],
                    "limit": min(30, limit), "page": 1,
                })
                notices = data["notices"]
            except FetchError as exc:
                last_error = exc
                continue
            successful = True
            for row in notices:
                key = row.get("publication-number")
                name = localized(row.get("notice-title"))
                if key and name:
                    url = "https://ted.europa.eu/en/notice/-/detail/" + quote(str(key), safe="")
                    out[key] = ProductMatch(
                        name=name, url=url, kind="demand_signal", source_record_id=str(key),
                        provenance=self.evidence(url, "Published procurement notice and evidence of buyer demand."),
                    )
        if last_error is not None and not successful:
            raise last_error
        return rank_matches(idea, list(out.values()))[:limit]


class UKContractsAdapter(PublicSource):
    id, name = "uk_contracts", "UK Contracts Finder"
    endpoint = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"

    async def discover(self, idea: Idea, fetcher: Fetcher, limit: int) -> list[ProductMatch]:
        if not idea.keywords:
            return []
        today = date.today()
        data = await fetcher.get_json(self.endpoint, params={
            "publishedFrom": (today - timedelta(days=14)).isoformat(),
            "publishedTo": today.isoformat(), "limit": 100,
        })
        out = {}
        for row in data["releases"]:
            tender = row.get("tender") or {}
            title = tender.get("title")
            desc = (tender.get("description") or "")[:1800]
            if not title:
                continue
            key = str(row.get("id") or row.get("ocid") or "")
            if not key:
                continue
            origin = "https://www.contractsfinder.service.gov.uk"
            url = f'{origin}/Published/Notice/Releases/{quote(str(row["ocid"]), safe="")}.json' if row.get("ocid") else self.endpoint
            suppliers = {
                s["name"] for award in row.get("awards", []) if award.get("status") == "active"
                for s in award.get("suppliers", []) if s.get("name")
            }
            for supplier in sorted(suppliers) or [None]:
                name = supplier or title
                record_id = f"{key}:{name}"
                out[record_id] = ProductMatch(
                    name=name, tagline=title if supplier else None, description=desc,
                    url=url, kind="supplier" if supplier else "demand_signal",
                    market="United Kingdom", source_record_id=record_id,
                    provenance=self.evidence(url, "Recent public contract notice. The supplier is shown only where an active award names it."),
                )
        return rank_matches(idea, list(out.values()))[:limit]
