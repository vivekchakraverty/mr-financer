"""Regression coverage for the two-stage browser workflow."""

from datetime import date

import httpx

from mrfinancer import workspace
from mrfinancer.api import app
from mrfinancer.config import Settings
from mrfinancer.models import CompetitorProfile, FinancialFact, Metric, ProductMatch, Severity, FactIssue
from mrfinancer.provenance import AccessMethod, Provenance, Trust


async def test_review_only_uses_selected_firms_and_withholds_rejected_figures(tmp_path, monkeypatch):
    settings = Settings(cache_path=tmp_path / "cache.sqlite")
    monkeypatch.setattr(workspace, "get_settings", lambda: settings)
    source = Provenance(source="fixture", source_name="Fixture", method=AccessMethod.OFFICIAL_API,
                        trust=Trust.STATUTORY, url="https://example.com/filing")
    matches = [
        ProductMatch(name=f"Firm {i}", provenance=source, similarity=0.8,
                     match_type="direct" if i == 0 else "similar")
        for i in range(105)
    ]
    calls = []

    async def discovery(*args):
        return matches, []

    async def enrich(product, *args, **kwargs):
        calls.append((product.name, kwargs.get("registry_id")))
        valid = FinancialFact(metric=Metric.TURNOVER, value=900, currency="NOK",
                              period_end=date(2025, 12, 31), provenance=source)
        rejected = valid.model_copy(update={"value": 9000000, "issues": [
            FactIssue(rule="test", severity=Severity.REJECT, message="Incorrect scale")
        ]})
        return CompetitorProfile(product=product, financials=[valid, rejected])

    monkeypatch.setattr(workspace, "collect_discovery", discovery)
    monkeypatch.setattr(workspace, "enrich", enrich)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        page = await client.get("/")
        assert page.status_code == 200
        assert 'data-count="100"' in page.text
        assert (await client.get("/assets/sources.json")).status_code == 200
        search = await client.post("/idea/discover", json={"idea": "Invoice reminders"})
        data = search.json()
        assert search.status_code == 200
        assert data["direct_count"] == 1 and len(data["matches"]) == 100
        assert calls == []
        invalid = await client.post("/financials/review", json={
            "search_id": data["search_id"], "firms": [{"index": 2, "registry_id": "123"}]
        })
        assert invalid.status_code == 422
        response = await client.post("/financials/review", json={
            "search_id": data["search_id"], "firms": [{"index": 2, "jurisdiction": "NO", "registry_id": "989112953"}]
        })
        assert response.status_code == 200
        assert calls == [("Firm 2", "989112953")]
        assert [f["value"] for f in response.json()[0]["profile"]["financials"]] == [900.0]


def test_explicit_manual_sending_rules_out_direct_automation_match():
    from mrfinancer.idea import normalize_idea
    from mrfinancer.ranking import rank_matches

    source = Provenance(source="fixture", source_name="Fixture", method=AccessMethod.OFFICIAL_API,
                        trust=Trust.OFFICIAL)
    listing = ProductMatch(name="Invoice Follow-Up", provenance=source,
                           description="Freelancers send payment reminders, with no automatic sending")
    idea = normalize_idea("An app that sends automatic invoice reminders for freelancers")
    match, = rank_matches(idea, [listing])
    assert match.match_type == "similar"
    assert "Listing explicitly excludes automatic sending" in match.score_reasons


