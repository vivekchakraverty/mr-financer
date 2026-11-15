import importlib.util
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from mrfinancer.api import app
from mrfinancer.plan_financials import PlanRequest, build_exhibits


def amount(low, high=None):
    return dict(status="estimate" if high is not None else "actual", low=low, high=high)


def example():
    return dict(idea="Local meal delivery", currency="INR", stage="idea", audience="founder or team",
        opening=amount(1000), fixed=amount(100), receipts_days=30, supplier_days=0,
        offers=[dict(label="Meals", unit="orders", price=amount(20), variable=amount(10),
                     first=amount(10), last=amount(20), capacity=amount(12))])


def test_timing_capacity_break_even_and_unknowns():
    args = example()
    result = build_exhibits(PlanRequest(**args))
    base = result["scenarios"]["base"]
    assert base["unit_economics"][0]["break_even_units"] == 10
    assert base["unit_economics"][0]["contribution_margin_percent"] == "50.00"
    assert base["capacity_limits"] == ["Meals"]
    assert base["rows"][0]["customer_receipts"] == "0.00"
    assert base["rows"][1]["customer_receipts"] == "200.00"
    assert base["rows"][0]["closing_cash"] == "800.00"
    assert base["rows"][11]["revenue"] == "240.00"
    args["opening"] = {"status": "unknown"}
    assert build_exhibits(PlanRequest(**args))["scenarios"]["base"]["funding_gap"] is None


def test_ranges_unconfirmed_funding_and_peer_scope():
    args = example()
    args["offers"][0]["price"] = amount(10, 30)
    args["events"] = [{"label": "Loan", "month": 1, "kind": "unconfirmed funding", "amount": amount(1000)}]
    fact = dict(company="Meal Co", currency="INR", period_start="2025-01-01",
                period_end="2025-12-31", scope="standalone", source="Annual filing",
                url="https://example.org/annual.pdf", status="reported")
    args["peers"] = [dict(fact, metric="turnover", amount="100"), dict(fact, metric="net_profit", amount="25"),
                     dict(fact, metric="operating_profit", amount="40", scope="consolidated")]
    result = build_exhibits(PlanRequest(**args))
    assert result["competitor_margins"] == [{"label": "Net", "percent": "25.00", "source_ids": ["F1", "F2"]}]
    scenarios = result["scenarios"]
    assert scenarios["cautious"]["unit_economics"][0]["break_even_units"] is None
    assert scenarios["base"]["rows"][0]["funding_and_other_inflows"] == "0.00"
    assert scenarios["base_with_unconfirmed_funding"]["rows"][0]["funding_and_other_inflows"] == "1000.00"


def test_frontend_routes_and_guest_token_gate():
    client = TestClient(app)
    assert 'id="business-plan"' in client.get("/").text
    assert client.get("/assets/business-plan.js").status_code == 200
    assert client.get("/business-plan/questionnaire").status_code == 200
    assert client.post("/business-plan/preview", json=example()).json()["ok"]
    resp = client.post("/business-plan/draft", json=example())
    assert resp.status_code == 401
    assert "Hugging Face" in resp.json()["detail"]


def test_generated_prose_cannot_replace_published_numbers():
    path = Path(__file__).resolve().parents[1] / "hf-space" / "narrative.py"
    spec = importlib.util.spec_from_file_location("plan_narrative", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    raw = json.dumps({key: "Cash expected is ₹500 [F1]." for key in script.SECTIONS})
    prose = script.extract_narrative(raw, [{"id": "F1"}])
    assert all("500" not in row["text"] and "[F1]" in row["text"] for row in prose)

