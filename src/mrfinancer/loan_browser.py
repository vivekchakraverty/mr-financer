"""Open the workbook's interactive sources in an isolated headless session."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager
from bs4 import BeautifulSoup
from urllib.parse import urlencode, urljoin, urlsplit

from .loan_parse import html_records, named_records, safe_link, unique_records
from .loan_sources_config import FSS_PATHS, FILTERS, NAME_SELECTORS, SEARCH


def run_browser(worker, *args):
    # Windows uvicorn may use a selector loop, which cannot launch Playwright.
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
            return runner.run(worker(*args))
    return asyncio.run(worker(*args))


def browser_results(source, url, loan_types, hosts, user_agent):
    return run_browser(collect, source, url, loan_types, hosts, user_agent)


def extract(html, url, source_id):
    if source_id in NAME_SELECTORS:
        rows = named_records(html, url, NAME_SELECTORS[source_id])
        if rows:
            return rows
    return html_records(html, url, source_id=source_id)


@asynccontextmanager
async def public_page(hosts, user_agent, proxy_variable="MRF_LOAN_BROWSER_PROXY"):
    from playwright.async_api import async_playwright

    async with async_playwright() as runtime:
        # Third-party analytics and unrelated browser requests are unnecessary.
        host_rules = ", ".join(["MAP * ~NOTFOUND", *[f"EXCLUDE {h}" for h in sorted(hosts)]])
        proxy_url = os.getenv(proxy_variable) or os.getenv("MRF_LOAN_BROWSER_PROXY")
        browser = await runtime.chromium.launch(
            headless=True, args=[f"--host-resolver-rules={host_rules}"],
            proxy={"server": proxy_url} if proxy_url else None,
        )
        try:
            context = await browser.new_context(user_agent=user_agent, service_workers="block")
            async def restrict(route):
                address = urlsplit(route.request.url)
                if (address.scheme != "https" or address.hostname not in hosts or address.port not in (None, 443) or
                        route.request.resource_type in {"image", "media", "font"}):
                    await route.abort()
                else:
                    await route.continue_()
            await context.route("**/*", restrict)
            page = await context.new_page()
            page.set_default_timeout(4500)
            page.set_default_navigation_timeout(8500)
            yield page
        finally:
            await browser.close()


async def collect(source, url, loan_types, hosts, user_agent):
    from playwright.async_api import Error

    source_id = source["id"]
    async with public_page(hosts, user_agent) as page:
        results = []
        search_applied = False
        executed = set()
        for loan_type in loan_types:
            target = (f"https://{urlsplit(url).netloc}{FSS_PATHS[loan_type]}"
                      if source_id == "fss" else url)
            selected_filter = FILTERS.get(source_id)
            if selected_filter and (value := selected_filter[1 if loan_type == "personal" else 2]) in executed:
                continue
            if selected_filter:
                executed.add(value)
            try:
                response = await page.goto(target, wait_until="domcontentloaded")
                if response and response.status in {401, 403, 429}:
                    raise PermissionError(f"HTTP {response.status}")
                if not response or response.status >= 400:
                    continue
                if source_id == "fss":
                    await page.locator("button.ajaxFormSearch:visible").first.click()
                    search_applied = True
                elif source_id == "fi":
                    form = page.locator("#foretagSearchAdvanced")
                    action = await form.get_attribute("action")
                    if action:
                        category = ("Konsumentkreditinstitut" if loan_type == "personal" else "Kreditmarknadsföretag")
                        # Submit the public advanced form's category and wait for its result page.
                        destination = urljoin(page.url, action).split("#", 1)[0]
                        await page.goto(destination + "?" + urlencode({"huvudkategori": category}),
                                        wait_until="domcontentloaded")
                        search_applied = True
                elif selected_filter:
                    field, _, _, submit = selected_filter
                    await page.locator(field).select_option(value)
                    await page.locator(submit).first.click()
                    search_applied = True
                elif source_id in SEARCH:
                    field, submit, personal, business = SEARCH[source_id]
                    search = page.locator(field).first
                    await search.fill(personal if loan_type == "personal" else business)
                    if submit:
                        await page.locator(submit).first.click()
                    else:
                        await search.press("Enter")
                    search_applied = True
                elif source_id == "cbi-downloads":
                    name = "Register of Retail Credit Firms" if loan_type == "personal" else "Register of Credit Institutions"
                    async with page.expect_download(timeout=6500) as event:
                        await page.get_by_role("link", name=re.compile("^" + name, re.I)).first.click()
                    download = await event.value
                    from .loan_parse import exported_records
                    path = await download.path()
                    if path.stat().st_size <= 12_000_000:
                        results.extend(exported_records(path.read_bytes(), download.url, "application/pdf"))
                    search_applied = True
                    continue
                elif source_id == "fdic":
                    active = page.get_by_text("Active", exact=True)
                    if await active.count() == 1:
                        await active.click(timeout=1400)
                    submit = page.get_by_role("button", name=re.compile(r"^search$", re.I))
                    if await submit.count():
                        await submit.first.click()
                        search_applied = True
                await page.wait_for_load_state("domcontentloaded")
                rows = []
                for _ in range(8):
                    html = await page.content()
                    if source_id == "nfra":
                        attachment = BeautifulSoup(html, "lxml").select_one('a[href*="/chinese/docfile/"][href$=".pdf"]')
                        if attachment:
                            return [], safe_link(page.url, attachment["href"]), False
                    rows = extract(html, page.url, source_id)
                    if rows:
                        break
                    await asyncio.sleep(.55)
                if source_id == "fss":
                    for row in rows:
                        row["fields"]["Loan type"] = loan_type.title()
                        row["source_url"] = target
                results.extend(rows)
            except Error:
                continue
            if source_id not in FILTERS and source_id not in SEARCH and source_id not in {"fss", "fi", "cbi-downloads"}:
                break
        return unique_records(results), page.url if page.url.startswith("https://") else url, search_applied
