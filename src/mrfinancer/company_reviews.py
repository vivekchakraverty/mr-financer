"""Review samples from documented provider APIs and identifiable listings."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from urllib.parse import urlencode, urlsplit

import httpx

from .company_crawl import clean, site_host, web_url

TRUSTPILOT = "https://api.trustpilot.com/v1/business-units"
GOOGLE = "https://places.googleapis.com/v1/places"
STEAM = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"


def review_url(value):
    if not value:
        return None
    return web_url(value, keep_query=True)


def domain_url(domain):
    return f"https://www.trustpilot.com/review/{domain}" if domain else None


def provider(name, url, scope, status, note, reviews=(), **data):
    return dict(name=name, url=url, scope=scope, status=status, note=note,
                reviews=sorted(reviews, key=lambda row: row["rating"]), **data)


def row(rating, *, text="", title="", author="", date="", link=None, profile=None, avatar=None,
        visit_date="", report_link=None, translated=False):
    return dict(rating=rating, title=clean(title, 200), text=text.strip()[:4000] if isinstance(text, str) else "",
                author=clean(author, 120), date=clean(date, 40), url=review_url(link),
                author_url=review_url(profile), avatar=review_url(avatar), visit_date=visit_date,
                report_link=review_url(report_link), translated=translated)


def stars(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 1 <= value <= 5


async def get_json(client, url, **kwargs):
    result = await client.get(url, **kwargs)
    result.raise_for_status()
    return result.json()


async def trustpilot(client, domain, key):
    name, scope, link = "Trustpilot", "Firm reviews", domain_url(domain)
    if not domain or not key:
        note = ("Add an authorized Trustpilot API key to load reviews." if domain
                else "An official company domain is needed to find the Trustpilot profile.")
        return provider(name, link, scope, "unavailable", note)
    headers = {"apikey": key}
    unit = await get_json(client, f"{TRUSTPILOT}/find", params={"name": domain}, headers=headers)
    identity = unit.get("id", "")
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identity):
        return provider(name, link, scope, "unavailable", "No matching Trustpilot profile was found.")
    profile_name = (unit.get("identifyingName") or "").lower().removeprefix("www.")
    if profile_name != domain:
        return provider(name, link, scope, "unavailable", "The profile domain did not match the selected company.")
    collected, seen = [], set()
    cursor = None
    for _ in range(2):
        data = await get_json(client, f"{TRUSTPILOT}/{identity}/all-reviews",
                              params={"pageToken": cursor} if cursor else {}, headers=headers)
        for review in data.get("reviews", []):
            score, entry = review.get("stars"), review.get("id", "")
            if not stars(score) or not isinstance(entry, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", entry) or entry in seen:
                continue
            seen.add(entry)
            collected.append(row(score, title=review.get("title"), text=review.get("text"),
                                 author=(review.get("consumer") or {}).get("displayName", ""),
                                 date=(review.get("createdAt") or "")[:10],
                                 link=f"https://www.trustpilot.com/reviews/{entry}"))
        next_page = data.get("nextPageToken")
        if not next_page or next_page == cursor:
            break
        cursor = next_page
    return provider(name, link, scope, "ok", "Up to two API pages, sorted from lowest to highest within the sample.",
                    collected, scale=5)


def normalized(value):
    return " ".join(re.findall(r"\w+", clean(value, 500).casefold()))


def maps_link(product):
    context = product.signals.get("address") or product.market or ""
    return "https://www.google.com/maps/search/?" + urlencode({"api": 1, "query": f"{product.name} {context}".strip()})


async def google(client, product, domain, key):
    name, scope, link = "Google Maps", "Location reviews", maps_link(product)
    if not key:
        return provider(name, link, scope, "unavailable",
                        "Google Places review requests require a configured billing-enabled API key.")
    address = product.signals.get("address", "")
    market = product.market if product.kind == "local_business" else ""
    query = " ".join(str(value) for value in (product.name, address, market) if value)
    response = await client.post(f"{GOOGLE}:searchText", json={"textQuery": query, "pageSize": 5}, headers={
        "X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.websiteUri",
    })
    response.raise_for_status()
    candidates = []
    for item in response.json().get("places", []):
        identifier = item.get("id", "")
        if not isinstance(identifier, str) or not re.fullmatch(r"[\w-]{1,150}", identifier, re.ASCII):
            continue
        site = web_url(item.get("websiteUri"))
        exact_domain = bool(domain and site and site_host(site) == domain)
        exact_name = normalized((item.get("displayName") or {}).get("text")) == normalized(product.name)
        street = normalized(address)
        location = normalized(item.get("formattedAddress"))
        exact_address = bool(street and street in location)
        if exact_name and (exact_address or exact_domain):
            candidates.append((exact_address, identifier))
    strong = [identifier for exact_address, identifier in candidates if exact_address]
    ids = list(dict.fromkeys(strong or [identifier for _, identifier in candidates]))
    if len(ids) != 1:
        return provider(name, link, scope, "unavailable", "No unique Google Maps location could be verified.")
    data = await get_json(client, f"{GOOGLE}/{ids[0]}", headers={
        "X-Goog-Api-Key": key, "X-Goog-FieldMask": "googleMapsUri,formattedAddress,rating,userRatingCount,reviews",
    })
    collected = []
    for review in data.get("reviews", []):
        if not stars(review.get("rating")) or not review_url(review.get("googleMapsUri")):
            continue
        author = review.get("authorAttribution") or {}
        visit = review.get("visitDate") or {}
        text = (review.get("text") or {}).get("text")
        original = (review.get("originalText") or {}).get("text")
        collected.append(row(review["rating"], text=text or original, author=author.get("displayName") or "Google Maps user",
                             date=(review.get("publishTime") or "")[:10], link=review.get("googleMapsUri"),
                             profile=author.get("uri"), avatar=author.get("photoUri"),
                             report_link=review.get("flagContentUri"), translated=bool(text and original and text != original),
                             visit_date=f"{visit['year']}-{visit['month']:02d}" if visit.get("month") and visit.get("year") else ""))
    return provider(name, review_url(data.get("googleMapsUri")) or link, scope, "ok",
                    "Google selects up to five reviews by relevance. This sample is sorted from lowest to highest.",
                    collected, scale=5, average=data.get("rating"), count=data.get("userRatingCount"),
                    address=clean(data.get("formattedAddress"), 240))


def steam_app(product, website):
    for href in (product.url, product.homepage, website, product.provenance.url):
        url = web_url(href)
        if url and urlsplit(url).hostname == "store.steampowered.com":
            found = re.match(r"/app/([0-9]{1,10})(?:/|$)", urlsplit(url).path)
            if found:
                return found[1]
    if product.provenance.source == "steam" and re.fullmatch(r"[0-9]{1,10}", product.source_record_id or ""):
        return product.source_record_id
    return None


async def steam(client, appid):
    link = f"https://store.steampowered.com/app/{appid}/#app_reviews_hash"
    data = await get_json(client, STEAM, params={"input_json": json.dumps({
        "appid": int(appid), "num_per_page": 50, "languages": ["all"], "purchase_type": 1,
    }, separators=(",", ":"))})
    body = data.get("response", data)
    collected = []
    for review in body.get("reviews", []):
        vote = review.get("voted_up")
        if not isinstance(vote, bool):
            continue
        timestamp = review.get("timestamp_created")
        stamp = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d") if isinstance(timestamp, int) and 0 < timestamp < 5_000_000_000 else ""
        author_id = str((review.get("author") or {}).get("steamid") or "")
        link_to_review = f"https://steamcommunity.com/profiles/{author_id}/recommended/{appid}/" if re.fullmatch(r"[0-9]{1,20}", author_id) else link
        collected.append(row(1 if vote else 0, title="Recommended" if vote else "Not recommended",
                             text=review.get("review"), date=stamp, link=link_to_review))
    return provider("Steam", link, "Product reviews", "ok",
                    "Up to 50 Steam product reviews. Steam uses recommendations, so negative reviews appear first.",
                    collected, scale=1, count=(body.get("query_summary") or {}).get("total_reviews"))


async def collect_reviews(product, website, settings):
    domain = site_host(website) if website else None
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0), follow_redirects=False) as client:
        sources = [
            ("Trustpilot", domain_url(domain), "Firm reviews", trustpilot(client, domain, settings.trustpilot_api_key)),
            ("Google Maps", maps_link(product), "Location reviews", google(client, product, domain, settings.google_places_api_key)),
        ]
        appid = steam_app(product, website)
        if appid:
            sources.append(("Steam", f"https://store.steampowered.com/app/{appid}/#app_reviews_hash",
                            "Product reviews", steam(client, appid)))
        results = await asyncio.gather(*(part[3] for part in sources), return_exceptions=True)
    output = []
    for (name, link, scope, _), result in zip(sources, results, strict=True):
        if isinstance(result, httpx.HTTPStatusError):
            reason = {401: "The API key was rejected.", 403: "The API key lacks access or the provider requires a plan.",
                      404: "No matching review profile was found.", 429: "The review API rate limit was reached."}
            output.append(provider(name, link, scope, "unavailable",
                                   reason.get(result.response.status_code, "The review API could not complete this lookup.")))
        elif isinstance(result, Exception):
            output.append(provider(name, link, scope, "unavailable", "The review API could not complete this lookup."))
        else:
            output.append(result)
    return output

