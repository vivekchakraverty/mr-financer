import io

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from mrfinancer.api import app
from mrfinancer.loan_parse import exported_records, html_records
from mrfinancer.loans import COUNTRIES, DirectoryError, checked_document


def test_workbook_catalog_and_route_validation():
    with TestClient(app) as client:
        catalog = client.get("/loans/countries").json()
        assert client.get("/assets/loans.js").status_code == 200
        assert len(catalog) == 30
        assert sum(len(country["sources"]) for country in catalog) == 38
        assert len({country["code"] for country in catalog}) == 30
        assert client.get("/loans/sources/AU/fca").status_code == 404
        assert client.get("/loans/sources/ZZ/apra").status_code == 404
        assert client.get("/loans/sources/AU/apra?kind=other").status_code == 422
        result = client.get("/loans/sources/US/sba?kind=personal").json()
        assert result["rows"] == []
        assert result["message"] == "This directory covers business lending."
        assert client.get("/loans/fintable/AU?q=ab").status_code == 422


def test_provider_table_keeps_publisher_fields_and_safe_links():
    html = """<main><table><tr><th>Lender</th><th>APR Range</th></tr>
        <tr><td><a href='/details/1'>First Example Bank</a></td><td>5–9%</td></tr>
        <tr><td><a href='javascript:alert(1)'>Second Example Bank</a></td><td></td></tr>
        </table></main>"""
    first, second = html_records(html, "https://www.example.com/register")
    assert first == {"name": "First Example Bank", "fields": {"APR Range": "5–9%"},
                     "url": "https://www.example.com/details/1"}
    assert second["url"] is None


def test_xlsx_and_csv_publishers_export_lender_names():
    workbook = Workbook()
    workbook.active.append(["Register as of November"])
    workbook.active.append(["Name of a financial institution", "JCN"])
    workbook.active.append(["Example Bank", 123])
    memory = io.BytesIO()
    workbook.save(memory)
    records = exported_records(memory.getvalue(), "https://www.example.com/bank.xlsx", "")
    assert records[0]["name"] == "Example Bank"
    assert records[0]["fields"]["JCN"] == "123"
    csv = b"Lender;City\nExample Bank;New York\n"
    assert exported_records(csv, "https://www.example.com/banks.csv", "text/csv")[0]["name"] == "Example Bank"


@pytest.mark.asyncio
async def test_directory_fetch_stops_off_site_redirects():
    def respond(request):
        assert request.url.host == "www.example.com"
        return httpx.Response(302, headers={"Location": "https://localhost/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DirectoryError, match="outside its published site"):
            await checked_document(client, None, "https://www.example.com/register", {"www.example.com"})


@pytest.mark.asyncio
async def test_fintable_result_filters_other_countries(monkeypatch):
    from mrfinancer import loans

    async def response(client, robots, url, hosts):
        assert "country=AU" in url
        return (b'{"data":[{"name":"Australian Bank","countries":["AU"]},'
                b'{"name":"Other Bank","countries":["GB"]}],"meta":{"has_more":true}}',
                url, "application/json")

    monkeypatch.setattr(loans, "checked_document", response)
    result = await loans.fetch_fintable(COUNTRIES["AU"], "", 1)
    assert [row["name"] for row in result["rows"]] == ["Australian Bank"]
    assert result["has_more"]
