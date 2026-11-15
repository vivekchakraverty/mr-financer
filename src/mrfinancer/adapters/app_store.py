"""App listings with publisher attribution from Apple’s public Search API."""

from __future__ import annotations

import re

from ..models import Idea, Jurisdiction, ProductMatch
from ..net import FetchError
from ..ranking import query_variants, rank_matches
from ..textutil import extract_domain
from .public_discovery import DigitalSource


class AppStoreAdapter(DigitalSource):
    id, name = "app_store", "Apple App Store"
    endpoint = "https://itunes.apple.com/search"

    async def discover(self, idea: Idea, fetcher, limit: int) -> list[ProductMatch]:
        storefront = {Jurisdiction.GB: "gb", Jurisdiction.IN: "in"}.get(
            idea.jurisdiction_hint, idea.jurisdiction_hint.value.lower()
        )
        if idea.jurisdiction_hint is Jurisdiction.UNKNOWN:
            storefront = "us"

        matches = {}
        error = None
        completed = False
        queries = list(dict.fromkeys(query_variants(idea)))[:3]
        for query in queries:
            try:
                data = await fetcher.get_json(self.endpoint, params={
                    "term": query, "country": storefront, "entity": "software",
                    "limit": min(60, limit),
                })
            except FetchError as exc:
                error = exc
                continue
            completed = True
            for row in data.get("results", []):
                track_id = str(row.get("trackId") or "")
                seller = row.get("sellerName")
                if not re.fullmatch(r"\d+", track_id) or not seller or not row.get("trackName"):
                    continue
                publisher_id = str(row.get("artistId") or "")
                url = f"https://apps.apple.com/{storefront}/app/id{track_id}"
                site = row.get("sellerUrl")
                matches.setdefault(track_id, ProductMatch(
                    name=row["trackName"], description=(row.get("description") or "")[:1800],
                    homepage=site, domain=extract_domain(site), url=url,
                    source_record_id=track_id,
                    signals={"publisher": seller, "publisher_id": publisher_id},
                    provenance=self.evidence(url, f"App Store listing in the {storefront.upper()} storefront."),
                ))
        if not completed and error:
            raise error
        return rank_matches(idea, list(matches.values()))[:limit]

