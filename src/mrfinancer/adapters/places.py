"""Venue discovery through OpenStreetMap and Foursquare."""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from ..models import Idea, ProductMatch
from ..net import FetchError, Fetcher
from ..provenance import AccessMethod, Gap, Provenance, ReasonCode, Trust
from ..ranking import COUNTRY_WORDS, concepts, market_terms, query_variants, rank_matches, words
from ..textutil import extract_domain
from .public_discovery import PublicSource

# Selected documented OSM shop and amenity values. Overpass queries use a city
# boundary and a few of these tags, never an unbounded global scan.
OSM_TAGS = {
    "laundry": ("shop", "laundry|dry_cleaning"),
    "pet grooming": ("shop", "pet_grooming"),
    "beauty salon": ("shop", "beauty|hairdresser"),
    "restaurant": ("amenity", "restaurant"),
    "cafe": ("amenity", "cafe"),
    "childcare": ("amenity", "childcare|kindergarten"),
    "clinic": ("amenity", "clinic|doctors"),
    "ev charging": ("amenity", "charging_station"),
    "coworking": ("office", "coworking"),
    "fitness": ("leisure", "fitness_centre"),
    "bicycle": ("shop", "bicycle"),
    "bakery": ("shop", "bakery"),
    "pharmacy": ("amenity", "pharmacy"),
    "hotel": ("tourism", "hotel"),
}

def place_model(source: str, label: str, row: dict, bulk: bool = False) -> ProductMatch:
    key = str(row["fsq_place_id"])
    url = "https://foursquare.com/placemakers/review-place/" + quote(key, safe="")
    site = row.get("website")
    labels = row.get("fsq_category_labels") or [
        c.get("name") or c.get("short_name") for c in row.get("categories", [])
    ]
    address = row.get("location") or row
    market = ", ".join(dict.fromkeys(
        str(address[k]) for k in ("locality", "region", "country") if address.get(k)
    ))
    street = address.get("address")
    return ProductMatch(
        signals={"address": street} if street else {},
        name=row["name"], url=url, homepage=site, domain=extract_domain(site),
        topics=[label.replace(" > ", " ") for label in labels if label],
        market=market or None, kind="local_business", source_record_id=key,
        provenance=Provenance(
            source=source, source_name=label, url=url, trust=Trust.OFFICIAL,
            method=AccessMethod.OFFICIAL_BULK_DATA if bulk else AccessMethod.OFFICIAL_API,
            note="Place listing and category. Specific services depend on the individual venue.",
        ),
    )


class LocalSource(PublicSource):
    def skip_reason(self, idea: Idea) -> str | None:
        if idea.delivery_mode != "local":
            return "Skipped for an idea without a physical service"
        if not idea.location:
            return "Skipped because no city or locality was supplied"
        return None


class FoursquarePlacesAdapter(LocalSource):
    id, name = "foursquare_places", "Foursquare Places API"
    endpoint = "https://places-api.foursquare.com/places/search"

    def __init__(self, service_key: str | None) -> None:
        self.service_key = service_key or None

    def available(self) -> Gap | None:
        if self.service_key:
            return None
        return Gap(field="discovery", reason=ReasonCode.CREDENTIALS_MISSING, source=self.id,
                   detail="Set MRF_FOURSQUARE_SERVICE_KEY for live Places Search.")

    async def discover(self, idea: Idea, fetcher: Fetcher, limit: int) -> list[ProductMatch]:
        if not idea.location or idea.delivery_mode != "local":
            return []
        out = {}
        for query in query_variants(idea)[:1]:
            rows = await self.search(query, idea.location, fetcher, min(30, limit))
            for row in rows:
                if row.get("fsq_place_id") and row.get("name"):
                    out[row["fsq_place_id"]] = place_model(self.id, self.name, row)
        return rank_matches(idea, list(out.values()))[:limit]

    async def search(self, query: str, location: str, fetcher: Fetcher, limit: int) -> list[dict]:
        # The default response fields keep the request within the Pro tier.
        data = await fetcher.get_json(
            self.endpoint, params={"query": query, "near": location, "limit": limit},
            headers={"Authorization": f"Bearer {self.service_key}", "X-Places-Api-Version": "2025-06-17"},
            use_cache=False,
        )
        return data["results"]

    async def health(self, fetcher: Fetcher) -> str:
        rows = await self.search("coffee", "Kolkata, India", fetcher, 1)
        return f"ok, {len(rows)} place(s) from a sample query"


class FoursquareSnapshotAdapter(LocalSource):
    id, name = "foursquare_open", "Foursquare Open Source Places"

    def __init__(self, snapshot: Path | None) -> None:
        self.snapshot = snapshot

    def available(self) -> Gap | None:
        if self.snapshot and self.snapshot.is_file():
            return None
        return Gap(field="discovery", reason=ReasonCode.ADAPTER_DISABLED, source=self.id,
                   detail="Import a Places Portal CSV export and set MRF_FOURSQUARE_SNAPSHOT.")

    def _rows(self, terms: list[str], city: str, limit: int) -> list[dict]:
        assert self.snapshot is not None
        expression = " OR ".join('"' + term + '"' for term in terms)
        with closing(sqlite3.connect(self.snapshot.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM places WHERE places MATCH ? AND locality = ? COLLATE NOCASE "
                "ORDER BY rank LIMIT ?",
                (expression, city, limit),
            ).fetchall()
        return [
            {**dict(row), "fsq_category_labels": json.loads(row["category_json"])}
            for row in rows
        ]

    async def discover(self, idea: Idea, fetcher: Fetcher, limit: int) -> list[ProductMatch]:
        if not idea.location or idea.delivery_mode != "local":
            return []
        city = idea.location.split(",")[0].strip()
        variants = query_variants(idea)
        terms = list(dict.fromkeys(" ".join(words(q)) for q in (*variants, *idea.keywords)))
        terms = [t for t in terms if t][:16]
        if not terms:
            return []
        rows = await asyncio.to_thread(self._rows, terms, city, min(limit * 3, 150))
        if "," in idea.location:
            region = market_terms(idea.location.split(",", 1)[1])
            rows = [
                row for row in rows
                if region <= market_terms(f'{row["region"]}, {row["country"]}')
            ]
        return rank_matches(idea, [place_model(self.id, self.name, row, bulk=True) for row in rows])[:limit]

    async def health(self, fetcher: Fetcher) -> str:
        def sample() -> bool:
            assert self.snapshot is not None
            with closing(sqlite3.connect(self.snapshot.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                return db.execute("SELECT 1 FROM places LIMIT 1").fetchone() is not None
        return "ok, snapshot contains places" if await asyncio.to_thread(sample) else "snapshot is empty"


class OverpassAdapter(LocalSource):
    id, name = "overpass", "OpenStreetMap / Overpass"

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    async def discover(self, idea: Idea, fetcher: Fetcher, limit: int) -> list[ProductMatch]:
        if not idea.location or idea.delivery_mode != "local":
            return []
        city = idea.location.split(",")[0].strip()
        if not city or not all(c.isalpha() or c in " -'." for c in city):
            return []
        labels = set(words(idea.raw)) | concepts(idea.raw)
        tags = [spec for label, spec in OSM_TAGS.items() if label in labels][:3]
        if not tags:
            return []
        # Exact place name; constrain to a country via explicit ISO or common
        # full country names. City-only results carry city-level precision.
        qualifier = idea.location.split(",", 1)[-1].strip() if "," in idea.location else ""
        country_name = COUNTRY_WORDS.get(qualifier.casefold(), qualifier)
        country = country_name.upper() if re.fullmatch(r"[A-Za-z]{2}", country_name) else None
        if qualifier and not country:
            return []
        area = f'area["name"={json.dumps(city, ensure_ascii=False)}]["boundary"="administrative"]'
        prefix = ""
        if country:
            prefix = f'area["ISO3166-1"="{country}"]["admin_level"="2"]->.country;'
            area += "(area.country)"
        filters = "".join(f'nwr(area.city)["{tag}"~"^({value})$"]["name"];' for tag, value in tags)
        query = f'[out:json][timeout:9];{prefix}{area}->.city;({filters});out tags {min(80, limit * 2)};'
        data = await fetcher.get_json(self.endpoint, params={"data": query})
        out = []
        for row in data["elements"]:
            t = row.get("tags", {})
            if not t.get("name"):
                continue
            key = f'{row["type"]}/{row["id"]}'
            url = f"https://www.openstreetmap.org/{key}"
            site = t.get("website") or t.get("contact:website")
            out.append(ProductMatch(
                name=t["name"], url=url, homepage=site, domain=extract_domain(site),
                description=t.get("description"), market=idea.location, kind="local_business",
                signals={"address": f"{t['addr:housenumber']} {t['addr:street']}"}
                if t.get("addr:housenumber") and t.get("addr:street") else {},
                topics=[t[k].replace("_", " ") for k, _ in tags if k in t],
                source_record_id=key,
                provenance=self.evidence(url, "OpenStreetMap contributors, ODbL. Mapped category and service tags may be incomplete."),
            ))
        return rank_matches(idea, out)[:limit]
