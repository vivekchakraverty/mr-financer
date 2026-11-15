"""Read public pages linked from a company's website."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from .net import RobotsGate, TokenBucket

MAX_PAGES = 16
MAX_BYTES = 1_500_000
MAX_ROWS = 150
SAMPLES = r"demo|portfolio|case-stud(?:y|ies)|our-work|samples?|examples?|showcase"
PLANS = r"pricing|plans?|packages?|tariffs?|menu"
CATALOG = r"products?|services?|solutions?|collections?|shop|store|offerings?|catalog(?:ue)?"
PEOPLE = r"team|founders?|leadership|about(?:-us)?|company|people|staff"
CLIENTS = r"clients?|customers?|partners?|testimonials?|case-stud(?:y|ies)|portfolio|our-work"
PRICE = re.compile(
    r"(?:US\$|CA\$|AU\$|S\$|HK\$|NZ\$|[$€£₹¥]|(?:USD|EUR|GBP|INR|CAD|AUD|JPY)\s*)\s*\d[\d,.]*"
    r"(?:\s*(?:/|per\s+)(?:months?|mo\.?|years?|yr\.?|week|day|user|seat|unit))?"
    r"|\b\d[\d,.]*\s*(?:EUR|GBP|INR|USD|CAD|AUD|JPY)\b",
    re.I,
)
GENERIC = re.compile(r"^(our |the )?(products?|services?|solutions?|features?|pricing|plans?|customers?|clients?|team|about us|learn more|view all|contact (us|sales)|get started|request (a )?demo|see pricing|home)$", re.I)
PLATFORMS = {
    "alternativeto.net", "producthunt.com", "apps.apple.com", "play.google.com",
    "foursquare.com", "openstreetmap.org", "www.google.com", "google.com", "trustpilot.com",
    "store.steampowered.com", "steamcommunity.com", "linkedin.com", "facebook.com",
    "instagram.com", "youtube.com", "youtu.be", "github.com", "g2.com", "capterra.com",
    "clutch.co", "getapp.com", "softpedia.com", "wikipedia.org", "wikidata.org", "x.com",
    "sourceforge.net", "reddit.com", "bing.com", "duckduckgo.com",
}


class WebsiteError(Exception):
    """The target cannot be read within the rules for a public website."""


def site_host(url):
    return (urlsplit(url).hostname or "").removeprefix("www.").rstrip(".").lower()


def web_url(value, base=None, *, keep_query=False):
    if not isinstance(value, str) or not value or len(value) > 2000:
        return None
    try:
        parts = urlsplit(urljoin(base or "", value.strip()))
        host = (parts.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        if (parts.scheme not in ("http", "https") or not host or parts.username is not None
                or parts.password is not None or "." not in host or "\\" in parts.netloc
                or parts.port not in (None, 443 if parts.scheme == "https" else 80)):
            return None
        if re.search(r"[^a-z0-9.-]", host) or host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
            return None
        return urlunsplit((parts.scheme, host, parts.path or "/", parts.query if keep_query else "", ""))
    except (ValueError, UnicodeError):
        return None


def is_platform(url):
    host = site_host(url)
    return any(host == platform or host.endswith("." + platform) for platform in PLATFORMS)


def official_website(product):
    for candidate in (product.homepage, product.url):
        url = web_url(candidate)
        if url and not is_platform(url):
            return url
    return None


async def public_addresses(host: str, port: int) -> list[str]:
    results = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(row[4][0] for row in results))
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast or getattr(ip, "ipv4_mapped", None):
            raise WebsiteError("The website resolved to a private or reserved network.")
    if not addresses:
        raise WebsiteError("The website address could not be resolved.")
    return sorted(addresses, key=lambda address: ":" in address)


class WebsiteReader:
    """Pin vetted DNS answers to the socket while keeping the website's TLS identity."""

    def __init__(self, client: httpx.AsyncClient, start: str, user_agent: str):
        self.client = client
        self.host = site_host(start)
        self.limiter = TokenBucket(2.0)
        self.robots = RobotsGate(self, user_agent, True)

    async def get(self, url: str, timeout: float = 6.0) -> httpx.Response:
        return await self.read(url, timeout=timeout, check_robots=False)

    async def read(self, url: str, timeout: float = 6.0, *, check_robots=True) -> httpx.Response:
        for _ in range(5):
            target = web_url(url, keep_query=True)
            if target is None or site_host(target) != self.host:
                raise WebsiteError("The site redirected outside its domain.")
            if check_robots and not await self.robots.allowed(target):
                raise WebsiteError("A page was blocked by robots.txt or the file was unavailable.")
            await self.limiter.acquire()
            parts = urlsplit(target)
            addresses = await public_addresses(parts.hostname, 443 if parts.scheme == "https" else 80)
            for address in addresses[:3]:
                pinned = str(httpx.URL(target).copy_with(host=address))
                try:
                    async with self.client.stream(
                        "GET", pinned, headers={"Host": parts.hostname, "Accept": "text/html, text/plain"},
                        extensions={"sni_hostname": parts.hostname}, timeout=min(timeout, 6.0),
                    ) as response:
                        if response.is_redirect and response.headers.get("location"):
                            url = urljoin(target, response.headers["location"])
                            break
                        if check_robots and response.is_success and not any(
                            mime in response.headers.get("content-type", "").lower()
                            for mime in ("text/html", "application/xhtml+xml")
                        ):
                            raise WebsiteError("The link did not return a public HTML page.")
                        data = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=16_384):
                            data.extend(chunk)
                            if len(data) > MAX_BYTES:
                                raise WebsiteError("A page exceeded the crawl size limit.")
                        headers = {key: value for key, value in response.headers.items()
                                   if key not in ("content-encoding", "content-length", "transfer-encoding")}
                        return httpx.Response(response.status_code, headers=headers,
                                              content=bytes(data), request=httpx.Request("GET", target))
                except httpx.ConnectError:
                    if address == addresses[:3][-1]:
                        raise
            else:
                break
        raise WebsiteError("The website sent too many redirects.")


def clean(value, length=150):
    return " ".join(value.split())[:length] if isinstance(value, str) else ""


def matches(pattern, value):
    return bool(re.search(r"(?:^|[^a-z0-9])(?:" + pattern + r")(?:$|[^a-z0-9])", value, re.I))


def items(value):
    return value if isinstance(value, list) else [value] if value is not None else []


def schema_graph(value):
    stack = [value]
    for _ in range(500):
        if not stack:
            return
        obj = stack.pop()
        if isinstance(obj, list):
            stack.extend(reversed(obj[:150]))
        elif isinstance(obj, dict):
            yield obj
            stack.extend(reversed([v for v in obj.values() if isinstance(v, (list, dict))][:100]))


def node_types(item):
    return {t.rsplit("/", 1)[-1].rsplit(":", 1)[-1] for t in items(item.get("@type")) if isinstance(t, str)}


def offer_prices(node):
    output = []
    for offer in items(node.get("offers"))[:24]:
        if not isinstance(offer, dict):
            continue
        for record in [offer, *[p for p in items(offer.get("priceSpecification")) if isinstance(p, dict)][:10]]:
            amount = record.get("price", record.get("lowPrice"))
            high = record.get("highPrice")
            if not isinstance(amount, (str, int, float)) or isinstance(amount, bool):
                continue
            interval = clean(record.get("billingDuration") or record.get("unitText"), 30)
            price = clean(" ".join(x for x in (
                clean(record.get("priceCurrency") or offer.get("priceCurrency"), 12),
                str(amount) if high is None else f"{amount}–{high}",
                f"/ {interval}" if interval else "",
            ) if x), 95)
            if price and price not in output:
                output.append(price)
    return output


class Findings:
    def __init__(self):
        self.groups = {key: {} for key in ("people", "clients", "offerings", "samples")}

    def add(self, key, name, source, *, url=None, detail="", prices=(), kind=""):
        label = clean(name)
        if not label or GENERIC.fullmatch(label) or len(label) < 2:
            return
        group = self.groups[key]
        identity = (label.casefold(), url) if key == "samples" else label.casefold()
        if identity not in group:
            if len(group) >= MAX_ROWS:
                return
            group[identity] = {"name": label, "url": url, "detail": detail, "kind": kind,
                               "prices": [], "sources": []}
        row = group[identity]
        if not row["url"] and url:
            row["url"] = url
        if detail and (not row["detail"] or key == "people" and "founder" in detail.casefold()):
            row["detail"] = detail
        if kind and (not row["kind"] or row["kind"] == "Listed offering"):
            row["kind"] = kind
        if source not in row["sources"]:
            row["sources"].append(source)
        for price in prices:
            entry = {"label": price, "source": source}
            if entry not in row["prices"] and len(row["prices"]) < 15:
                row["prices"].append(entry)

    def output(self):
        return {key: list(rows.values()) for key, rows in self.groups.items()}


def structured(soup, url, found):
    for script in soup.find_all("script", type="application/ld+json", limit=35):
        try:
            graph = json.loads(script.string or script.get_text())
        except (ValueError, RecursionError):
            continue
        nodes = list(schema_graph(graph))
        by_id = {node["@id"]: node for node in nodes if isinstance(node.get("@id"), str) and node.get("name")}
        for item in nodes:
            types = node_types(item)
            title = item.get("name")
            target = web_url(item.get("url"), url, keep_query=True) or url
            if types & {"Product", "Service", "SoftwareApplication", "WebApplication", "MenuItem", "Course"}:
                found.add("offerings", title, url, url=target, prices=offer_prices(item),
                          kind="Service" if "Service" in types else "Product")
            if types & {"Organization", "Corporation", "LocalBusiness", "ProfessionalService"}:
                for key, role in (("founder", "Founder"), ("founders", "Founder"),
                                  ("employee", "Team member"), ("employees", "Team member")):
                    for person in items(item.get(key))[:60]:
                        if isinstance(person, dict):
                            person = by_id.get(person.get("@id"), person) if isinstance(person.get("@id"), str) else person
                            role_at_site = clean(person.get("jobTitle"), 90)
                            found.add("people", person.get("name"), url,
                                      detail=role_at_site if key.startswith("employee") and role_at_site else role,
                                      url=web_url(person.get("url"), url))
                        elif isinstance(person, str) and not person.startswith(("http", "#")):
                            found.add("people", person, url, detail=role)
            if "Person" in types:
                role = clean(item.get("jobTitle"), 100)
                if re.search(r"\b(founder|co-founder|CEO|director|head of|president)\b", role, re.I):
                    found.add("people", title, url, url=target, detail=role)
            if types & {"VideoObject", "CreativeWork"} and matches(SAMPLES, urlsplit(url).path):
                found.add("samples", title, url, url=web_url(item.get("embedUrl") or item.get("contentUrl"), url,
                                                          keep_query=True) or target)


def page_links(soup, url, found):
    for a in soup.select("a[href]")[:550]:
        label = clean(a.get_text(" ", strip=True) or a.get("aria-label"))
        target = web_url(a.get("href"), url, keep_query=True)
        if not target or a["href"].lstrip().startswith("#"):
            continue
        path = unquote(urlsplit(target).path)
        tag = f"{label} {path}"
        if matches(SAMPLES, tag) and (matches(SAMPLES, path) or matches(r"demo|sample|case study", label)):
            if not re.search(r"\b(?:book|request|schedule|get a demo)\b", label, re.I):
                found.add("samples", label or clean(path.rsplit("/", 1)[-1].replace("-", " ")),
                          url, url=target)
        if site_host(target) != site_host(url):
            continue
        crawl = web_url(target)
        if crawl == url or not crawl or re.search(
            r"\.(?:zip|pdf|jpg|jpeg|png|svg|webp|gif|mp4|xml|json|css|js)$", path, re.I
        ) or matches(r"login|register|cart|checkout|search|privacy|terms|account|careers|jobs|wp-admin|blog|news", path):
            continue
        priorities = (PEOPLE, CLIENTS, PLANS, CATALOG, SAMPLES)
        ranking = next((index for index, pattern in enumerate(priorities) if matches(pattern, tag)), None)
        if ranking is not None:
            yield ranking, crawl
        segments = [part for part in path.split("/") if part]
        if len(segments) >= 2 and matches(CATALOG, "/".join(segments[:-1])) and not matches(PLANS + "|" + SAMPLES, segments[-1]):
            found.add("offerings", label, url, url=target, kind="Listed offering")


def content_cards(soup, url, found):
    path = urlsplit(url).path
    for node in soup.select("body > header, body > footer, nav, style, script, noscript, [role=banner], [role=contentinfo]"):
        node.decompose()
    for image in soup.select("img[alt]")[:200]:
        alt = clean(image.get("alt"))
        context = " ".join(" ".join(p.get("class", [])) + " " + str(p.get("id", ""))
                           for p in list(image.parents)[:5] if p.name not in ("body", "html", "[document]"))
        if ((matches(CLIENTS, context) or matches(r"clients?|customers?", path))
                and not matches(r"partners?", context)
                and not GENERIC.fullmatch(alt)
                and not re.search(r"\b(image|photo|icon|placeholder|screenshot)\b", alt, re.I)):
            found.add("clients", re.sub(r"\s+(?:logo|logotype)$", "", alt, flags=re.I), url,
                      detail="Named in a client or customer section")
    if matches(r"case-stud(?:y|ies)|our-work|portfolio", path):
        for node in soup.select("dt, strong, b, span")[:350]:
            if clean(node.get_text(" ", strip=True)).casefold().rstrip(":") != "client":
                continue
            adjacent = node.find_next_sibling()
            label = clean(adjacent.get_text(" ", strip=True)) if adjacent else clean(str(node.next_sibling or "")).lstrip(": ")
            if label and len(label.split()) <= 10:
                found.add("clients", label, url, detail="Identified as the client in a case study")
    headings = soup.find_all(["h1", "h2", "h3", "h4"], limit=200)
    for node in headings:
        label = clean(node.get_text(" ", strip=True))
        context = node.parent
        evidence = clean(context.get_text(" ", strip=True), 370)
        role = re.search(r"\b((?:co[- ]?)?founder|CEO|CTO|COO|CFO|managing director|chief [a-z ]{3,35} officer)\b", evidence, re.I)
        name = " ".join(re.findall(r"[\wÀ-ÿ.'’-]+", label))
        ancestry = " ".join(" ".join(p.get("class", [])) + " " + str(p.get("id", ""))
                            for p in list(node.parents)[:4] if p.name not in ("body", "html", "[document]"))
        team_card = node.name in ("h3", "h4") and matches(r"team-member|team-card|team-person|staff-member|employee", ancestry)
        if ((matches(PEOPLE, path) and role or team_card) and 2 <= len(name.split()) <= 6
                and not matches(r"our|the|meet|team|leadership|story|contact|about|founder|ceo|vision|mission|values|join|message|innovation|work", label)
                and len(evidence) < 220):
            found.add("people", name, url, detail=role[1] if role else "Team member")
        nearby_heading = node.find_previous(["h1", "h2"])
        preceding = clean(nearby_heading.get_text(" ", strip=True)) if nearby_heading else ""
        if (node.name in ("h3", "h4") and matches(r"clients?|customers?", ancestry)
                and matches(r"clients?|customers?", preceding) and not matches(r"partners?", ancestry)):
            found.add("clients", label, url, detail="Named in a client or customer section")
        if not (matches(CATALOG + "|" + PLANS, path) or matches(CATALOG + "|" + PLANS, ancestry)):
            continue
        if (not label or "?" in label or len(label) > 95 or len(label.split()) > 13
                or re.search(r"^(?:why|how|what|can we|do i|do you|will i|are |compare|designed|discover|frequently|faq|contact|trusted|ready|simple|choose|plan for)\b", label, re.I)):
            continue
        if context.name in ("body", "html", "main") and node.name != "h1":
            evidence = label
        elif len(context.get_text(" ", strip=True)) > 550:
            evidence = label
        if node.name == "h1" and len([part for part in path.split("/") if part]) < 2 and not matches(r"menu", path):
            continue
        pricing_page = matches(PLANS, path)
        price_context = label if PRICE.search(label) else evidence
        if pricing_page and context.name == "header" and not PRICE.search(price_context):
            card = context.parent.get_text(" ", strip=True)
            if len(card) < 450:
                price_context = card
        prices = list(dict.fromkeys(clean(m[0], 80) for m in PRICE.finditer(price_context)))
        if len(prices) > 3:
            prices = []
        if not prices and pricing_page:
            offer = re.search(r"\bfree(?: forever)?\b|\bcontact sales\b|\bcustom pricing\b", price_context, re.I)
            if not offer:
                continue
            prices = [offer[0]]
        kind = ("Plan" if matches(PLANS, path) else "Service" if matches(r"services?", path) else "Listed offering")
        anchor = node.find("a", href=True)
        name = PRICE.split(label, maxsplit=1)[0].rstrip(" ,:-") if pricing_page and PRICE.search(label) else label
        found.add("offerings", name, url, url=web_url(anchor["href"], url) if anchor else url,
                  prices=prices, kind=kind)


def next_catalog_pages(soup, url):
    for anchor in soup.select('a[rel~="next"], link[rel~="next"]')[:5]:
        target = web_url(anchor.get("href"), url, keep_query=True)
        if target and site_host(target) == site_host(url):
            yield target


async def crawl_company(website, user_agent):
    if website is None:
        return {"website": None, "pages": [], "notes": ["No company website was available in this search result."],
                **Findings().output()}
    found, pages, notes = Findings(), [], []
    visited = set()
    pending = [(-1, website)]
    if urlsplit(website).path.strip("/"):
        pending.append((-1, urljoin(website, "/")))
    async with httpx.AsyncClient(headers={"User-Agent": user_agent}, trust_env=False,
                                 follow_redirects=False, limits=httpx.Limits(max_keepalive_connections=0)) as client:
        reader = WebsiteReader(client, website, user_agent)
        try:
            async with asyncio.timeout(23):
                while pending and len(visited) < MAX_PAGES:
                    pending.sort(key=lambda row: row[0])
                    _, url = pending.pop(0)
                    if url in visited:
                        continue
                    visited.add(url)
                    try:
                        response = await reader.read(url)
                        response.raise_for_status()
                    except WebsiteError as exc:
                        if str(exc) not in notes:
                            notes.append(str(exc))
                        continue
                    except (httpx.HTTPError, OSError, ValueError):
                        if "Some website pages could not be loaded." not in notes:
                            notes.append("Some website pages could not be loaded.")
                        continue
                    actual = str(response.request.url)
                    if actual in pages:
                        continue
                    pages.append(actual)
                    soup = BeautifulSoup(response.text, "html.parser")
                    structured(soup, actual, found)
                    pending.extend((priority, link) for priority, link in page_links(soup, actual, found)
                                   if link not in visited and len(pending) < 250)
                    if matches(CATALOG, urlsplit(actual).path) and len(pending) < 240:
                        pending.extend((3, link) for link in next_catalog_pages(soup, actual) if link not in visited)
                    content_cards(soup, actual, found)
        except TimeoutError:
            notes.append("The website scan reached its time limit. Results found so far are shown.")
    if pending and len(visited) >= MAX_PAGES:
        notes.append(f"The scan reached its limit of {MAX_PAGES} pages. The website may list more entries.")
    if any(len(group) >= MAX_ROWS for group in found.groups.values()):
        notes.append("One or more result lists reached the display limit.")
    if pages and not any(found.groups.values()):
        notes.append("The pages checked did not expose these details in public HTML. Some sites render their catalog with JavaScript.")
    return {"website": website, "pages": pages, "notes": notes, **found.output()}

