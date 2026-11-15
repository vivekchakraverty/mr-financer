"""Country-based loan directory lookups for the Loans section."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Query

from .config import get_settings
from .loan_browser import browser_results, extract
from .loan_parse import EXPORTED, clean, content_area, exported_records, record, safe_link, unique_records
from .loan_sources_config import BCB_API, EXPORT_PATTERNS, EXTRA_HOSTS, FDIC_API, FINTABLE_API, FSS_PATHS, PAGES, PUBLISHED_FILES, REGAFI_API, RENDERED_LISTS
from .net import RobotsGate, TokenBucket

router = APIRouter(prefix="/loans")
logger = logging.getLogger(__name__)
CATALOG = json.loads(Path(__file__).with_name("web").joinpath("loan-sources.json").read_text(encoding="utf-8"))
COUNTRIES = {country["code"]: country for country in CATALOG}
LoanKind = Literal["personal", "business", "both"]
LIMIT_BYTES = 12_000_000
BROWSER_SLOTS = asyncio.Semaphore(2)
buckets: dict[str, TokenBucket] = {}
cached: dict[tuple, tuple[float, dict]] = {}
running: dict[tuple, asyncio.Task] = {}


class DirectoryError(Exception):
    pass


def allowed_url(url, hosts):
    address = urlsplit(url)
    return (address.scheme == "https" and address.hostname in hosts and address.port in (None, 443)
            and not address.username and not address.password)


def approved_hosts(source):
    host = urlsplit(source["url"]).hostname
    bare = host.removeprefix("www.")
    return {host, bare, f"www.{bare}", *EXTRA_HOSTS.get(source["id"], ())}


def make_client():
    return httpx.AsyncClient(timeout=httpx.Timeout(9, connect=5), follow_redirects=False,
                             headers={"User-Agent": get_settings().user_agent})


async def checked_document(client, robots, url, hosts):
    for _ in range(5):
        if not allowed_url(url, hosts):
            raise DirectoryError("The directory redirects outside its published site.")
        if robots and not await robots.allowed(url):
            raise DirectoryError("The source blocks automated access or its access rules are unavailable.")
        await buckets.setdefault(urlsplit(url).hostname, TokenBucket(2)).acquire()
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                url = safe_link(url, response.headers.get("location"))
                if not url:
                    raise DirectoryError("The source returned an invalid link.")
                continue
            if response.status_code >= 400:
                raise DirectoryError(f"The source returned HTTP {response.status_code}.")
            body = bytearray()
            async for data in response.aiter_bytes():
                body.extend(data)
                if len(body) > LIMIT_BYTES:
                    raise DirectoryError("This source file is too large to read here.")
            content = bytes(body)
            if response.encoding and response.encoding.lower() != "utf-8" and "html" in response.headers.get("content-type", ""):
                content = content.decode(response.encoding, errors="replace")
            return content, url, response.headers.get("content-type", "")
    raise DirectoryError("The source redirected too many times.")


def result(source, rows, data_url=None, message=None, method=None):
    return {"source": source, "rows": unique_records(rows), "data_url": data_url,
            "message": message, "method": method}


def website(value):
    value = clean(value) if isinstance(value, str) else ""
    if not value or "." not in value or any(char.isspace() for char in value):
        return None
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return safe_link(value, value)


async def official_api(client, source, hosts):
    source_id = source["id"]
    if source_id == "bcb":
        raw, data_url, _ = await checked_document(client, None, BCB_API, hosts)
        banks = json.loads(raw).get("value", [])
        rows = [record(clean(bank["NOME_INSTITUICAO"]), {
            "Institution type": clean(bank.get("SEGMENTO")), "CNPJ": clean(bank.get("CNPJ")),
            "City": clean(bank.get("MUNICIPIO")), "State": clean(bank.get("UF")),
        }, website(bank.get("SITIO_NA_INTERNET"))) for bank in banks if bank.get("NOME_INSTITUICAO")]
    elif source_id == "regafi":
        url = str(httpx.URL(REGAFI_API, params={
            "where": "pays = 'FRANCE' AND categorie IN ('Établissement de Crédit','Société de Financement')",
            "select": "denomination,categorie,ville,siren,nature_autorisation,authorisations", "limit": 100,
        }))
        raw, data_url, _ = await checked_document(client, None, url, hosts)
        rows = []
        for bank in json.loads(raw).get("results", []):
            permissions = bank.get("authorisations")
            if isinstance(permissions, str):
                try:
                    permissions = json.loads(permissions)
                except ValueError:
                    continue
            if bank.get("denomination") and isinstance(permissions, dict) and any(
                isinstance(item, dict) and item.get("situation") == "ACTIF" for item in permissions.values()
            ):
                rows.append(record(clean(bank["denomination"]), {"Category": clean(bank.get("categorie")),
                                   "City": clean(bank.get("ville")), "SIREN": clean(bank.get("siren"))}))
    else:
        url = str(httpx.URL(FDIC_API, params={"filters": "ACTIVE:1", "fields": "NAME,CITY,STALP,CERT,WEBADDR", "limit": 100}))
        raw, data_url, _ = await checked_document(client, None, url, hosts)
        banks = [item.get("data", {}) for item in json.loads(raw).get("data", [])]
        rows = [record(clean(bank["NAME"]), {"City": clean(bank.get("CITY")), "State": clean(bank.get("STALP")),
                      "FDIC certificate": clean(bank.get("CERT"))}, website(bank.get("WEBADDR")))
                for bank in banks if bank.get("NAME")]
    return result(source, rows, data_url, method="Published API")


def export_links(html, parent_url, source_id, hosts):
    pattern = EXPORT_PATTERNS.get(source_id)
    if not pattern:
        return []
    soup = BeautifulSoup(html, "lxml")
    links = []
    for tag in content_area(soup).select("a[href]"):
        link = safe_link(parent_url, tag["href"].strip())
        if link and allowed_url(link, hosts) and re.search(pattern, f"{link} {tag.get_text(' ', strip=True)}", re.I):
            if link not in links:
                links.append(link)
    return links[:4]


async def static_results(client, robots, source, hosts):
    source_id = source["id"]
    initial, root, mime = await checked_document(client, robots, source["url"], hosts)
    if EXPORTED.search(root) or "pdf" in mime:
        rows = await asyncio.to_thread(exported_records, initial, root, mime)
        return result(source, rows, root, method="Published file")
    documents = [(initial, root, mime)]
    issue = None
    for path in PAGES.get(source_id, ()):
        try:
            documents.append(await checked_document(client, robots, safe_link(root, path), hosts))
        except DirectoryError as error:
            issue = error
    rows = []
    used_url = None
    method = "Published list"
    if source_id not in PAGES:
        direct = extract(initial, root, source_id)
        if direct:
            rows.extend(direct)
            used_url = root
    for body, url, _ in documents if source_id not in {"cbi", "bde"} else []:
        if url != root:
            found = extract(body, url, source_id)
            if found:
                rows.extend(found)
                used_url = used_url or url
        for link in export_links(body, url, source_id, hosts):
            try:
                raw, final, content_type = await checked_document(client, robots, link, hosts)
                found = await asyncio.to_thread(exported_records, raw, final, content_type)
                if found:
                    rows.extend(found)
                    used_url = used_url or final
                    method = "Published file"
            except (DirectoryError, httpx.HTTPError):
                continue
    if issue and not rows:
        raise issue
    return result(source, rows, used_url, method=method)


async def headless(robots, source, hosts, loan_types):
    source_id = source["id"]
    url = source["url"]
    pages = PAGES.get(source_id, ())
    if pages:
        url = safe_link(url, pages[0])
    checks = [source["url"], url]
    if source_id == "fss":
        checks.extend(safe_link(url, FSS_PATHS[kind]) for kind in loan_types)
    for entry in dict.fromkeys(checks):
        if not allowed_url(entry, hosts) or not await robots.allowed(entry):
            raise DirectoryError("The source blocks automated access or its access rules are unavailable.")
    async with BROWSER_SLOTS:
        try:
            rows, final, searched = await asyncio.to_thread(
                browser_results, source, url, loan_types, hosts, get_settings().user_agent,
            )
        except PermissionError as error:
            raise DirectoryError(f"The directory refused automated access ({error}).") from None
        except Exception as error:
            if "Executable doesn't exist" in str(error) or isinstance(error, (ImportError, FileNotFoundError)):
                raise DirectoryError("Headless Chromium is missing. Run the app's browser setup and try again.") from None
            logger.warning("Loan directory %s browser failed (%s)", source_id, type(error).__name__)
            raise DirectoryError("The directory could not be opened in the headless browser.") from None
    method = "Directory search" if searched else "Browser-rendered list"
    return result(source, rows, final if allowed_url(final, hosts) else url, method=method)


async def fetch_directory(source, loan_types):
    hosts = approved_hosts(source)
    source_id = source["id"]
    candidate = None
    try:
        async with asyncio.timeout(48), make_client() as client, make_client() as robots_client:
            async def check_robots_host(request):
                if not allowed_url(str(request.url), hosts):
                    raise httpx.RequestError("Robots redirects to an unrelated host.", request=request)
            robots_client.follow_redirects = True
            robots_client.event_hooks["request"].append(check_robots_host)
            robots = RobotsGate(robots_client, get_settings().user_agent, get_settings().respect_robots)
            for url in PUBLISHED_FILES.get(source_id, ()):
                try:
                    body, final, mime = await checked_document(client, None, url, hosts)
                    records = await asyncio.to_thread(exported_records, body, final, mime)
                except (DirectoryError, httpx.HTTPError):
                    continue
                if records:
                    return result(source, records, final, method="Published file")
            if source_id == "bcb":
                candidate = await official_api(client, source, hosts)
            elif source["mode"] == "browser" or source_id in RENDERED_LISTS:
                try:
                    candidate = await headless(robots, source, hosts, loan_types)
                except DirectoryError:
                    if source_id not in {"fdic", "regafi"}:
                        raise
                if source_id == "nfra" and candidate and candidate["data_url"] and EXPORTED.search(candidate["data_url"]):
                    raw, final, mime = await checked_document(client, robots, candidate["data_url"], hosts)
                    rows = await asyncio.to_thread(exported_records, raw, final, mime)
                    candidate = result(source, rows, final, method="Published file")
                if source_id in {"fdic", "regafi"} and (not candidate or not candidate["rows"]):
                    candidate = await official_api(client, source, hosts)
            else:
                candidate = await static_results(client, robots, source, hosts)
    except DirectoryError as error:
        return result(source, [], message=str(error))
    except (TimeoutError, httpx.TimeoutException):
        return result(source, [], message="The source took too long to respond.")
    except (httpx.HTTPError, ValueError):
        return result(source, [], message="The source could not be reached or its data format changed.")
    except Exception:
        logger.exception("Loan directory %s could not be read", source_id)
        return result(source, [], message="The app could not read the source's published records.")
    if not candidate["rows"]:
        candidate["message"] = ("The app could not extract provider records from this source. "
                                "Open the original directory to check its current results.")
    return candidate


async def fetch_fintable(country, query, page):
    params = {"q": query or country["name"], "country": country["code"], "supported": 1, "page": page}
    url = str(httpx.URL(FINTABLE_API, params=params))
    async with asyncio.timeout(12), make_client() as client:
        data, _, _ = await checked_document(client, None, url, {"fintable.io", "www.fintable.io"})
    payload = json.loads(data)
    rows = []
    for entry in payload.get("data", []):
        if country["code"] not in entry.get("countries", []) or not entry.get("name"):
            continue
        providers = sorted({conn.get("provider", {}).get("display_name", "") for conn in entry.get("connections", [])
                            if conn.get("supported") and country["code"] in conn.get("countries", []) and conn.get("provider")})
        rows.append(record(clean(entry["name"]), {"Banking data providers": ", ".join(filter(None, providers))},
                           website(entry.get("domain")) if entry.get("domain_verified") else None))
    return {"rows": rows, "has_more": bool(payload.get("meta", {}).get("has_more"))}


def load_country(country):
    entry = COUNTRIES.get(country.upper())
    if not entry:
        raise HTTPException(404, "Country not found in the directory.")
    return entry


@router.get("/countries")
def list_countries():
    return CATALOG


@router.get("/fintable/{country}")
async def fintable_results(country: str, q: str = Query("", max_length=96), page: int = Query(1, ge=1, le=25)):
    entry = load_country(country)
    query = clean(q, 96)
    if query and len(query) < 3:
        raise HTTPException(422, "Enter at least three letters to search Fintable.")
    try:
        return await fetch_fintable(entry, query, page)
    except (DirectoryError, httpx.HTTPError, TimeoutError, ValueError):
        raise HTTPException(502, "Fintable could not complete the search. Try again or open its directory.") from None


@router.get("/sources/{country}/{source_id}")
async def loan_source_results(country: str, source_id: str, kind: LoanKind = "both"):
    entry = load_country(country)
    source = next((item for item in entry["sources"] if item["id"] == source_id), None)
    if not source:
        raise HTTPException(404, "Source not found for this country.")
    loan_types = ("personal", "business") if kind == "both" else (kind,)
    if source["coverage"] in {"Personal", "Business"} and source["coverage"].lower() not in loan_types:
        return result(source, [], message=f"This directory covers {source['coverage'].lower()} lending.")
    key = (source_id, loan_types if source["mode"] == "browser" or source_id == "cbi-downloads" else ())
    saved = cached.get(key)
    if saved and time.monotonic() - saved[0] < (900 if saved[1]["rows"] else 60):
        return saved[1]

    async def load():
        try:
            output = await fetch_directory(source, loan_types)
            cached[key] = (time.monotonic(), output)
            return output
        finally:
            running.pop(key, None)

    if key not in running:
        running[key] = asyncio.create_task(load())
    return await asyncio.shield(running[key])
