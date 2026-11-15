import gzip
import json
from collections import Counter

import pytest

from fastapi.testclient import TestClient

from mrfinancer.api import app
from mrfinancer.config import get_settings
from mrfinancer.investors import load_directory, suggested_sectors


def test_sector_rules_keep_specific_verticals():
    actual = suggested_sectors("An educational video game for learning history")
    assert {item["sector"] for item in actual} == {"EdTech & Education", "Media & Gaming"}
    assert [item["sector"] for item in suggested_sectors("Software for invoice financing and business loans")] == [
        "Fintech & Finance"
    ]
    assert suggested_sectors("A new idea") == []


@pytest.fixture
def investor_directory(tmp_path, monkeypatch):
    records = [
        {
            "firm": f"Example Investor {number:02d}",
            "title": "Angel Investor",
            "sectors": ["EdTech & Education", "Tech & Software"],
            "sheet": "EdTech & Education" if number < 24 else "Tech & Software",
            "row": number + 2,
        }
        for number in range(26)
    ]
    records.append({"firm": "Example Generalist", "title": "Angel Investor",
                    "sectors": ["General & Unspecified"], "sheet": "General & Unspecified", "row": 2})
    path = tmp_path / "investors.json.gz"
    path.write_bytes(gzip.compress(json.dumps({"workbook": "example.xlsx", "records": records}).encode()))
    monkeypatch.setattr(get_settings(), "investor_directory", path)
    load_directory.cache_clear()
    yield
    load_directory.cache_clear()


def test_directory_matches_all_workbook_sector_labels(investor_directory):
    directory, _ = load_directory()
    records = directory["records"]
    assert len(records) == 27
    assert len({(row["sheet"], row["row"]) for row in records}) == len(records)
    expected = Counter(sector for row in records for sector in row["sectors"])
    with TestClient(app) as client:
        response = client.get("/investors/sectors")
        assert response.headers["cache-control"] == "no-store"
        assert {row["name"]: row["count"] for row in response.json()["sectors"] if row["count"]} == dict(expected)
        selection = ["EdTech & Education"]
        first = client.post("/investors/search", json={"sectors": selection}).json()
        last = client.post("/investors/search", json={"sectors": selection, "page": 2}).json()
        whole_directory = first["rows"] + last["rows"]
        assert first["total"] == last["total"] == 26 == len(whole_directory)
        actual = {(row["sheet"], row["row"]) for row in whole_directory}
        assert actual == {(row["sheet"], row["row"]) for row in records if selection[0] in row["sectors"]}
        assert any(row["sheet"] != selection[0] for row in whole_directory)
        assert all(row["matching_sectors"] == selection for row in whole_directory)
        assert client.post("/investors/search", json={"sectors": ["wrong"]}).status_code == 422
        assert client.post("/investors/search", json={"idea": "something new"}).json()["total"] == 0
        assert client.get("/assets/investors.js").status_code == 200
        assert client.get("/assets/navigation.js").status_code == 200
        assert client.get("/assets/angel-investors.json.gz").status_code == 404


def test_missing_directory_reports_local_import(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "investor_directory", tmp_path / "missing.json.gz")
    load_directory.cache_clear()
    try:
        with TestClient(app) as client:
            response = client.get("/investors/sectors")
        assert response.status_code == 503
        assert "Import an investor workbook" in response.json()["detail"]
    finally:
        load_directory.cache_clear()
