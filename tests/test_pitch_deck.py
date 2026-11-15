from fastapi.testclient import TestClient

from mrfinancer.api import app
from mrfinancer.pitch_deck import BASE, TOPICS, lineup
from test_business_plan import amount, example


def test_slide_budget_keeps_core_topics_and_uses_evidence_for_extra_slides():
    core = set(sum(BASE, ()))
    for count in range(6, 26):
        slides = lineup(count, {"sources"}, "investor")
        flat = [topic for group in slides for topic in group]
        assert len(slides) == count
        assert core <= set(flat)
        assert len(flat) == len(set(flat))
        assert flat[0] == "cover"
    assert ("sources",) in lineup(11, {"sources"})
    assert set(sum(lineup(25), ())) == TOPICS.keys()


def test_pitch_endpoints_preserve_evidence_status_and_do_not_draw_charts_with_ai():
    client = TestClient(app)
    assert client.get("/business-plan/pitch/questionnaire").status_code == 200
    assert client.get("/assets/pitch-deck.js").status_code == 200
    schema = client.get("/business-plan/pitch/questions").json()
    assert sum(len(t["questions"]) for g in schema["groups"] for t in g["topics"]) == 62
    request = {"slide_count": 11, "visual_subject": "Small bakery counter", "answers": {
        "cover.name": {"text": "Bright Bakery", "status": "founder-reported"},
        "traction.actuals": {"text": "17 paid pilots in July 2026", "status": "documented",
                            "source": "https://example.org/evidence"},
        "market.buyer_count": {"text": "Unknown", "status": "unknown"},
    }}
    response = client.post("/business-plan/pitch/outline", json=request)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["slide_count"] == 11
    by_topic = {topic: slide for slide in data["slides"] for topic in slide["topics"]}
    assert "Small bakery counter" in by_topic["cover"]["image_prompt"]
    assert by_topic["traction"]["image_prompt"] is None
    result = by_topic["traction"]["items"][0]
    assert result["text"] == "17 paid pilots in July 2026"
    assert result["status"] == "documented" and result["source"] == "https://example.org/evidence"
    assert any("eligible buyers" in row for row in by_topic["market"]["missing"])


def test_financial_exhibits_and_filing_references_are_reused():
    plan = example()
    plan["opening"] = amount(1000)
    plan["peers"] = [
        {"company": "Comparable Ltd", "metric": "turnover", "amount": "1200", "currency": "INR",
         "period_start": "2025-01-01", "period_end": "2025-12-31", "scope": "standalone",
         "source": "Annual filings", "url": "https://example.org/filing", "status": "reported"}
    ]
    client = TestClient(app)
    res = client.post("/business-plan/pitch/outline", json={"slide_count": 25, "plan": plan})
    assert res.status_code == 200, res.text
    data = res.json()
    items = [item for slide in data["slides"] for item in slide["items"]]
    filing = next(row for row in items if "Comparable Ltd" in row["label"])
    assert filing["source"] == "https://example.org/filing"
    assert filing["text"] == "INR 1200"
    assert any(row["label"] == "Year-one revenue" and row["status"] == "Base forecast" for row in items)


def test_pitch_validation_rejects_invalid_counts_and_documented_answers_without_source():
    client = TestClient(app)
    endpoint = "/business-plan/pitch/outline"
    assert client.post(endpoint, json={"slide_count": 26}).status_code == 422
    assert client.post(endpoint, json={"slide_count": 6, "answers": {
        "cover.name": {"text": "Bright Bakery", "status": "documented"}
    }}).status_code == 422
    assert client.post(endpoint, json={"slide_count": 6, "answers": {
        "demo.demo_link": {"text": "javascript:alert(1)"}
    }}).status_code == 422

