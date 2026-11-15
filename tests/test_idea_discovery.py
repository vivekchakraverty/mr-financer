from __future__ import annotations

import csv
import json

import httpx
import pytest

from mrfinancer.adapters.places import FoursquareSnapshotAdapter
from mrfinancer.adapters.producthunt import ProductHuntAdapter
from mrfinancer.api import app
from mrfinancer.config import Settings
from mrfinancer.foursquare_import import COLUMNS, import_foursquare
from mrfinancer.idea import normalize_idea
from mrfinancer.models import ProductMatch
from mrfinancer.net import Fetcher
from mrfinancer.pipeline import _dedupe, analyze_idea
from mrfinancer.provenance import AccessMethod, Provenance, Trust
from mrfinancer.ranking import rank_matches


def item(name, tagline, kind="product", market=None):
    return ProductMatch(
        name=name, tagline=tagline, kind=kind, market=market,
        provenance=Provenance(source="fixture", source_name="Fixture",
                              method=AccessMethod.OFFICIAL_API, trust=Trust.OFFICIAL),
    )


def test_relevance_prefers_the_job_and_intended_audience():
    idea = normalize_idea("An app to chase overdue invoices for freelancers")
    relevant = item("Debtor Buddy", "Automatic payment reminders for independent contractors")
    adjacent = item("Invoice Designer", "Create invoice templates for freelancers")
    repository = item("dunning-cli", "Overdue invoice reminders for freelancers", "technical_analogue")
    unrelated = item("Freelancer Weather", "Forecast for freelancers")
    ranked = rank_matches(idea, [unrelated, adjacent, relevant, repository])
    assert ranked[0] is relevant
    assert relevant.match_type == "direct"
    assert repository.match_type == "related"
    assert unrelated.similarity == 0
    assert any("freelancer" in reason for reason in relevant.score_reasons)


def test_listing_in_the_wrong_market_is_not_a_direct_competitor():
    idea = normalize_idea("Doorstep laundry and dry cleaning", "Kolkata, India")
    local = item("Clean Lane", "Doorstep laundry and dry cleaning", "local_business", "Kolkata, IN")
    distant = item("Clean Road", "Doorstep laundry and dry cleaning", "local_business", "Pune, IN")
    ranked = rank_matches(idea, [distant, local])
    assert ranked[0] is local
    assert local.match_type == "direct"
    assert distant.match_type == "similar"


def test_same_domain_does_not_merge_different_offerings_or_branches():
    a = item("Acme CRM", "Track leads")
    b = item("Acme Tax", "File returns")
    a.domain = b.domain = "example.test"
    assert len(_dedupe([a, b])) == 2
    a = item("Same Laundry", "Laundry", "local_business", "Kolkata")
    b = item("Same Laundry", "Laundry", "local_business", "Kolkata")
    a.signals["address"], b.signals["address"] = "1 Park Street", "2 Park Street"
    assert len(_dedupe([a, b])) == 2
    b.signals["address"] = "1 Park Street"
    assert len(_dedupe([a, b])) == 1


async def test_product_hunt_uses_client_credentials_and_supported_topic_filter():
    def handler(req: httpx.Request):
        body = json.loads(req.content)
        if req.url.path.endswith("/oauth/token"):
            assert body["grant_type"] == "client_credentials"
            return httpx.Response(200, json={"access_token": "test-token"})
        assert req.headers["authorization"] == "Bearer test-token"
        if "DiscoverTopics" in body["query"]:
            return httpx.Response(200, json={"data": {"topics": {"nodes": [{"slug": "invoicing"}]}}})
        assert "posts(topic:" in body["query"]
        assert "posts(query:" not in body["query"]
        return httpx.Response(200, json={"data": {"posts": {"nodes": [{
            "id": "launch", "name": "Invoice Chaser", "tagline": "Invoice reminders for freelancers",
            "url": "https://www.producthunt.com/posts/invoice-chaser",
        }]}}})

    settings = Settings(requests_per_second=10000, max_retries=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        hits = await ProductHuntAdapter(None, "test-id", "test-secret").discover(
            normalize_idea("Invoice reminders for freelancers"), Fetcher(settings, client=client), 12
        )
    assert [hit.source_record_id for hit in hits] == ["launch"]


async def test_foursquare_snapshot_import_and_discovery(tmp_path):
    csv_path, db_path = tmp_path / "places.csv", tmp_path / "places.sqlite"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows([
            ("1", "Spark Laundry", "Kolkata", "West Bengal", "IN", "", '["Laundry Service"]', ""),
            ("2", "Closed Laundry", "Kolkata", "West Bengal", "IN", "", '["Laundry Service"]', "2023-01-01"),
            ("3", "Other City Laundry", "Pune", "Maharashtra", "IN", "", '["Laundry Service"]', ""),
        ])
    assert import_foursquare(csv_path, db_path) == 2
    settings = Settings()
    async with httpx.AsyncClient() as client:
        hits = await FoursquareSnapshotAdapter(db_path).discover(
            normalize_idea("Doorstep laundry", "Kolkata, India"), Fetcher(settings, client=client), 10
        )
    assert [hit.name for hit in hits] == ["Spark Laundry"]
    assert hits[0].provenance.method == AccessMethod.OFFICIAL_BULK_DATA


async def test_report_survives_a_source_failure_and_keeps_relevance_order():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "hn.algolia.com":
            return httpx.Response(200, json={"hits": [{
                "objectID": "1", "title": "Show HN: Invoice reminders for freelancers",
                "url": "https://invoicetool.example", "points": 4,
            }]})
        if req.url.host == "api.ted.europa.eu":
            return httpx.Response(200, json={"notices": [{
                "publication-number": "1-2026", "notice-title": {"eng": "Invoice reminders for freelancers"},
            }]})
        return httpx.Response(503)

    settings = Settings(max_retries=0, requests_per_second=10000, max_competitors=2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await analyze_idea(
            "An app for invoice reminders for freelancers", settings, Fetcher(settings, client=client)
        )
    assert [p.product.kind for p in report.competitors] == ["product", "demand_signal"]
    github = next(run for run in report.runs if run.adapter == "github")
    assert github.enabled and not github.ok


async def test_normalize_endpoint_accepts_market():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/idea/normalize", json={
            "idea": "Doorstep dry cleaning", "location": "Kolkata, India",
        })
    assert response.status_code == 200
    assert response.json()["location"] == "Kolkata, India"
    assert response.json()["delivery_mode"] == "local"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/idea/analyze.md", json={"idea": "   "})
    assert response.status_code == 422
