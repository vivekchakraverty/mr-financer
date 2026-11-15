import gzip
import json
import socket
import time

import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from mrfinancer import company_crawl as crawl, company_reviews as reviews, workspace
from mrfinancer.api import app
from mrfinancer.models import ProductMatch
from mrfinancer.provenance import AccessMethod, Provenance, Trust


def product(**changes):
    args = dict(name="Bright Kitchen", homepage="https://bright-kitchen.example",
                kind="local_business", market="Kolkata",
                provenance=Provenance(source="fixture", source_name="Fixture",
                                      method=AccessMethod.OFFICIAL_API, trust=Trust.OFFICIAL))
    return ProductMatch(**(args | changes))


def test_extracts_people_clients_prices_and_samples_with_sources():
    source = "https://bright-kitchen.example/team"
    dom = BeautifulSoup("""
    <script type="application/ld+json">
    {"@graph":[{"@type":"Organization","name":"Bright Kitchen","founder":{"@id":"#mai"}},
      {"@type":"Person","@id":"#mai","name":"Mai Sen"},
      {"@type":"Product","name":"Meal kit","url":"/products/kit",
       "offers":{"@type":"Offer","price":"499","priceCurrency":"INR"}}]}
    </script>
    <main><div class="clients"><h2>Our clients</h2>
      <img alt="Blue Motors logo"><h3>North House</h3></div>
      <a href="/products/kit">Meal kit</a><a href="/case-studies/north">North House case study</a>
      <a href="https://media.example/watch?v=123">Meal kit demo</a>
      <a href="https://other.example/team">Other team</a></main>
    """, "html.parser")
    findings = crawl.Findings()
    crawl.structured(dom, source, findings)
    links = list(crawl.page_links(dom, source, findings))
    crawl.content_cards(dom, source, findings)
    profile = findings.output()
    assert profile["people"][0]["name"] == "Mai Sen"
    assert profile["people"][0]["detail"] == "Founder"
    assert {item["name"] for item in profile["clients"]} == {"Blue Motors", "North House"}
    assert profile["offerings"][0]["prices"] == [{"label": "INR 499", "source": source}]
    assert "https://media.example/watch?v=123" in {item["url"] for item in profile["samples"]}
    assert all("other.example" not in url for _, url in links)


@pytest.mark.asyncio
async def test_crawl_pins_dns_rejects_cross_domain_and_respects_robots(monkeypatch):
    hits = []

    async def dns(host, port):
        assert crawl.site_host(f"https://{host}") == "bright-kitchen.example"
        return ["93.184.216.34"]

    def dispatch(req):
        assert req.url.host == "93.184.216.34"
        assert req.headers["host"] == "bright-kitchen.example"
        assert req.extensions["sni_hostname"] == "bright-kitchen.example"
        hits.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        if req.url.path == "/team":
            return httpx.Response(302, headers={"Location": "https://unrelated.example/team"})
        return httpx.Response(200, content=gzip.compress(b"<h1>Bright Kitchen</h1>"),
                              headers={"content-type": "text/html", "content-encoding": "gzip"})

    monkeypatch.setattr(crawl, "public_addresses", dns)
    async with httpx.AsyncClient(transport=httpx.MockTransport(dispatch)) as client:
        reader = crawl.WebsiteReader(client, "https://bright-kitchen.example", "Test")
        response = await reader.read("https://bright-kitchen.example/")
        assert response.text == "<h1>Bright Kitchen</h1>"
        with pytest.raises(crawl.WebsiteError, match="robots"):
            await reader.read("https://bright-kitchen.example/private")
        with pytest.raises(crawl.WebsiteError, match="outside its domain"):
            await reader.read("https://bright-kitchen.example/team")
    assert hits == ["/robots.txt", "/", "/team"]


@pytest.mark.asyncio
async def test_dns_rejects_private_and_mixed_network_answers(monkeypatch):
    import asyncio

    async def dns(*args, **kwargs):
        return [
            (socket.AF_INET, 1, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, 1, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    with pytest.raises(crawl.WebsiteError, match="private"):
        await crawl.public_addresses("test.example", 443)
    assert crawl.web_url("https://name:password@domain.example") is None
    assert crawl.official_website(product(homepage=None, url="https://www.producthunt.com/products/test")) is None


@pytest.mark.asyncio
async def test_review_sources_sort_and_keep_limits_and_attribution():
    def dispatch(req):
        if req.url.host == "api.trustpilot.com":
            assert req.headers["apikey"] == "fixture"
            if req.url.path.endswith("/find"):
                assert req.url.params["name"] == "bright-kitchen.example"
                return httpx.Response(200, json={"id": "confirmed", "identifyingName": "bright-kitchen.example"})
            assert req.url.path.endswith("/confirmed/all-reviews")
            if "pageToken" in req.url.params:
                return httpx.Response(200, json={"reviews": [{"id": "two", "stars": 2}]})
            return httpx.Response(200, json={"reviews": [{"id": "five", "stars": 5}], "nextPageToken": "next"})
        if req.url.host == "api.steampowered.com":
            assert json.loads(req.url.params["input_json"])["appid"] == 42
            return httpx.Response(200, json={"response": {"reviews": [
                {"voted_up": True, "review": "Positive"}, {"voted_up": False, "review": "Negative"},
            ]}})
        if req.method == "POST":
            return httpx.Response(200, json={"places": [
                {"id": "wrong", "displayName": {"text": "Bright Kitchen"}, "websiteUri": "https://unrelated.example"},
                {"id": "right", "displayName": {"text": "Bright Kitchen"}, "websiteUri": "https://bright-kitchen.example"},
            ]})
        assert req.url.path.endswith("/right")
        return httpx.Response(200, json={"reviews": [
            {"rating": 5, "googleMapsUri": "https://www.google.com/maps/reviews/top"},
            {"rating": 1, "googleMapsUri": "https://www.google.com/maps/reviews/low",
             "authorAttribution": {"displayName": "Reviewer", "uri": "https://www.google.com/maps/reviewer"},
             "visitDate": {"year": 2026, "month": 2}},
        ], "rating": 4.7, "userRatingCount": 40})

    async with httpx.AsyncClient(transport=httpx.MockTransport(dispatch)) as client:
        tp = await reviews.trustpilot(client, "bright-kitchen.example", "fixture")
        maps = await reviews.google(client, product(), "bright-kitchen.example", "fixture")
        steam = await reviews.steam(client, "42")
    assert [item["rating"] for item in tp["reviews"]] == [2, 5]
    assert [item["rating"] for item in maps["reviews"]] == [1, 5]
    assert maps["reviews"][0]["author"] == "Reviewer"
    assert maps["reviews"][0]["visit_date"] == "2026-02"
    assert [item["rating"] for item in steam["reviews"]] == [0, 1]
    assert steam["scope"] == "Product reviews"


def test_sidebar_route_uses_server_side_search_result(monkeypatch):
    async def crawl_site(website, _user_agent):
        return {"website": website, "people": [{"name": "Mai Sen"}]}

    async def fetch_reviews(found, website, _settings):
        assert found.name == "Bright Kitchen"
        assert website == "https://bright-kitchen.example/"
        return []

    key = "x" * 24
    monkeypatch.setattr(workspace, "searches", {key: (time.monotonic(), [product()])})
    monkeypatch.setattr(workspace, "crawl_company", crawl_site)
    monkeypatch.setattr(workspace, "collect_reviews", fetch_reviews)
    client = TestClient(app)
    assert "company-research-title" in client.get("/").text
    assert client.get("/assets/company-research.js").status_code == 200
    url = "/idea/company-research"
    assert client.post(url, json={"search_id": key, "index": 0}).json()["people"][0]["name"] == "Mai Sen"
    assert client.post(url, json={"search_id": key, "index": 1}).status_code == 422
    assert client.post(url, json={"search_id": "y" * 24, "index": 0}).status_code == 404

