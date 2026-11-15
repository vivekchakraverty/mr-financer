"""Country and source endpoints for the incorporation research workspace."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from fastapi import APIRouter, HTTPException

from .config import get_settings
from .incorporation_extract import TOPICS, parse_html, parse_pdf, parse_search, search_tasks
from .loan_browser import public_page, run_browser
from .loans import BROWSER_SLOTS, DirectoryError, allowed_url, approved_hosts, checked_document, make_client
from .net import RobotsGate

router = APIRouter(prefix="/incorporation")
logger = logging.getLogger(__name__)
DATA_PATH = Path(__file__).with_name("data") / "incorporation.json.gz"

with gzip.open(DATA_PATH, "rt", encoding="utf-8") as stream:
    CATALOG = json.load(stream)

COUNTRIES = {country["code"]: country for country in CATALOG["countries"]}
SOURCE_SLOTS = asyncio.Semaphore(6)
CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
IN_FLIGHT: dict[tuple[str, str], asyncio.Task] = {}
# The supplied links are deep content pages on these search portals. Open the
# exact source page first, then use its public search form for related material.
PORTAL_SEARCH = {
    "IT-CODE": {"path": "/ricerca/veloce/0", "input": "#testoRicerca", "submit": "form:has(#testoRicerca) button[type=submit]"},
    "PL-S24": {"path": "/pl/portal/szukaj", "parameter": "query"},
}
BROWSER_RENDER = {"PL-KRS", "PL-S24", "PL-ACCTS", "IT-CODE", "IL-REG", "IL-AR", "IL-LIQ", "SA-REG", "SA-CR"}


def country_or_404(code):
    country = COUNTRIES.get(code.upper())
    if not country:
        raise HTTPException(404, "Country not found in the incorporation workbook.")
    return country


@router.get("/countries")
def countries():
    return {
        "workbook": CATALOG["workbook"], "topics": list(TOPICS),
        "countries": [{key: item[key] for key in ("code", "name", "order")} for item in CATALOG["countries"]],
    }


@router.get("/countries/{code}")
def country_details(code: str):
    return country_or_404(code)


def result(source, *, status, method=None, url=None, entries=None, searches=None, message=None):
    return {
        "id": source["id"], "status": status, "method": method, "url": url or source["url"],
        "entries": entries or [], "searches": searches or [], "message": message,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def browser_issue(error):
    if isinstance(error, (ImportError, FileNotFoundError)) or "Executable doesn't exist" in str(error):
        return "Headless Chromium is unavailable. Run the app’s browser setup to enable this source."
    if isinstance(error, PermissionError):
        return f"The source refused automated access ({error})."
    return "The headless browser could not load the public page."


async def rendered_source(source, base_url, hosts, user_agent, site_search):
    from playwright.async_api import Error

    entries = []
    search_runs = []
    async with public_page(hosts, user_agent, "MRF_INCORPORATION_BROWSER_PROXY") as page:
        response = await page.goto(base_url, wait_until="domcontentloaded")
        if response and response.status in {401, 403, 429}:
            raise PermissionError(f"HTTP {response.status}")
        if response and response.ok and allowed_url(page.url, hosts):
            try:
                await page.locator("main, article, [role=main]").first.wait_for(timeout=2800)
            except Error:
                pass
            for _ in range(6):
                entries = parse_html(await page.content(), page.url, source)
                if entries:
                    break
                await asyncio.sleep(.45)
        if not site_search:
            return entries, search_runs
        for topic, query in search_tasks(source):
            try:
                if "parameter" in site_search:
                    destination = urljoin(base_url, site_search["path"])
                    destination += "?" + urlencode({site_search["parameter"]: query})
                    received = await page.goto(destination, wait_until="domcontentloaded")
                else:
                    await page.goto(base_url, wait_until="domcontentloaded")
                    await page.locator(site_search["input"]).fill(query, timeout=3500)
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=5000) as navigation:
                        await page.locator(site_search["submit"]).click(timeout=3500)
                    received = await navigation.value
                if not received or not received.ok or not allowed_url(page.url, hosts):
                    search_runs.append({"topic": topic, "query": query, "status": "unavailable", "entries": []})
                    continue
                matches = []
                for _ in range(3):
                    matches = parse_search(await page.content(), page.url, hosts, source, topic, query)
                    if matches:
                        break
                    await asyncio.sleep(.45)
                search_runs.append({"topic": topic, "query": query, "status": "results" if matches else "no_matches",
                                    "url": page.url if "parameter" in site_search else None, "entries": matches})
            except Error:
                search_runs.append({"topic": topic, "query": query, "status": "unavailable", "entries": []})
        return entries, search_runs


async def check_source(source):
    hosts = approved_hosts(source)
    settings = get_settings()
    url = source["url"]
    try:
        async with SOURCE_SLOTS, asyncio.timeout(75), make_client() as client, make_client() as robot_client:
            async def check_robot_host(request):
                if not allowed_url(str(request.url), hosts):
                    raise httpx.RequestError("Robots redirects to an unrelated host.", request=request)

            robot_client.follow_redirects = True
            robot_client.event_hooks["request"].append(check_robot_host)
            robots = RobotsGate(robot_client, settings.user_agent, settings.respect_robots)
            body, url, mime = await checked_document(client, robots, source["url"], hosts)
            pdf = isinstance(body, bytes) and (body.startswith(b"%PDF") or "pdf" in mime.lower())
            if pdf:
                entries = await asyncio.to_thread(parse_pdf, body, url, source)
                return result(source, status="ready" if entries else "no_matches", method="Published PDF",
                              url=url, entries=entries,
                              message=None if entries else "No relevant text could be extracted from this PDF.")
            if "html" not in mime.lower():
                return result(source, status="no_matches", url=url,
                              message="The source returned a format the app could not read.")
            entries = parse_html(body, url, source)
            portal = PORTAL_SEARCH.get(source["id"])
            searches = []
            browser_note = None
            method = "Published page"
            if portal and not await robots.allowed(urljoin(url, portal["path"])):
                portal = None
                browser_note = "The publisher’s access rules prevent automated searches on this portal."
            if portal or (not entries and (source["id"] in BROWSER_RENDER or source["id"].startswith(("SE-", "SG-")))):
                async with BROWSER_SLOTS:
                    try:
                        browser_entries, searches = await asyncio.to_thread(
                            run_browser, rendered_source, source, url, hosts, settings.user_agent, portal,
                        )
                        if not entries and browser_entries:
                            entries = browser_entries
                            method = "Browser-rendered page"
                    except Exception as error:
                        logger.info("Incorporation browser failed for %s (%s)", source["id"], type(error).__name__)
                        browser_note = browser_issue(error)
            has_matches = bool(entries or any(run["entries"] for run in searches))
            return result(
                source, status="ready" if has_matches else "no_matches",
                method=("Portal search and published page" if entries else "Portal search") if searches else method,
                url=url, entries=entries, searches=searches,
                message=browser_note or (None if has_matches else "No relevant entries could be read from the public page."),
            )
    except DirectoryError as error:
        return result(source, status="unavailable", url=url, message=str(error).replace("The directory", "The source", 1))
    except (TimeoutError, httpx.TimeoutException):
        return result(source, status="unavailable", url=url, message="The source took too long to respond.")
    except (httpx.HTTPError, ValueError):
        return result(source, status="unavailable", url=url, message="The source could not be reached or read.")
    except Exception:
        logger.exception("Incorporation source %s could not be read", source["id"])
        return result(source, status="unavailable", url=url, message="The app could not process the publisher’s response.")


@router.get("/sources/{code}/{source_id}")
async def source_live(code: str, source_id: str):
    country = country_or_404(code)
    source = next((item for item in country["sources"] if item["id"] == source_id), None)
    if source is None:
        raise HTTPException(404, "Source not found for this country.")
    key = (country["code"], source_id)
    saved = CACHE.get(key)
    if saved and time.monotonic() - saved[0] < (1800 if saved[1]["status"] == "ready" else 120):
        return saved[1]

    async def load():
        try:
            output = await check_source(source)
            CACHE[key] = (time.monotonic(), output)
            return output
        finally:
            IN_FLIGHT.pop(key, None)

    if key not in IN_FLIGHT:
        IN_FLIGHT[key] = asyncio.create_task(load())
    return await asyncio.shield(IN_FLIGHT[key])
