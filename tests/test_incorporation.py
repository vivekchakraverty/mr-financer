from fastapi.testclient import TestClient

from mrfinancer.api import app
from mrfinancer.incorporation import COUNTRIES
from mrfinancer.incorporation_extract import TOPICS, parse_html, parse_search, search_tasks


def test_workbook_coverage_and_route_source_binding():
    assert (len(COUNTRIES), sum(len(row["sources"]) for row in COUNTRIES.values())) == (30, 139)
    assert sum(len(row["entries"]) for row in COUNTRIES.values()) == 218
    with TestClient(app) as client:
        catalog = client.get("/incorporation/countries").json()
        assert len(catalog["countries"]) == 30
        assert catalog["countries"][0]["code"] == "USA"
        assert catalog["topics"] == list(TOPICS)
        usa = client.get("/incorporation/countries/usa").json()
        assert usa["gaps"] and "Delaware" in usa["scope"]
        sources = {source["id"] for source in usa["sources"]}
        assert all(item["source_id"] in sources for item in usa["entries"])
        assert client.get("/incorporation/countries/ZXY").status_code == 404
        assert client.get("/incorporation/sources/USA/GB-SET").status_code == 404
        assert client.get("/assets/incorporation.js").status_code == 200


def test_extracts_relevant_list_text_and_table_sections():
    source = {"title": "Company information", "language": "EN", "topics": ["Entity types", "Compliance"]}
    html = """<main><nav><ul><li>Annual accounts renew site preferences</li></ul></nav>
      <h2 id="legal-forms">Legal forms</h2><ul><li>General partnership and limited company</li></ul>
      <h2 id="accounts">Annual accounts</h2><table><tr><td>Private company</td><td>File annual accounts</td></tr></table>
      <footer>Terms of use and annual returns</footer></main>"""
    entries = parse_html(html, "https://publisher.example/info", source)
    assert {e["url"] for e in entries} == {
        "https://publisher.example/info#legal-forms", "https://publisher.example/info#accounts"
    }
    assert len(entries) == 2
    assert all("preferences" not in e["text"] for e in entries)


def test_search_uses_local_terms_and_keeps_publisher_links():
    source = {"title": "Laws", "language": "IT", "topics": ["Entity types"]}
    assert [topic for topic, _ in search_tasks(source)] == list(TOPICS)
    assert dict(search_tasks(source))["Dissolution"] == "scioglimento liquidazione società"
    html = """<main><section id="search-results">
      <article><a href="/law/company">Liquidazione della società</a><p>Norme sulla liquidazione aziendale</p></article>
      <article><a href="https://unrelated.example/law">Liquidazione di una società</a></article>
    </section></main>"""
    matches = parse_search(html, "https://publisher.example/search", {"publisher.example"},
                           source, "Dissolution", "liquidazione società")
    assert [item["url"] for item in matches] == ["https://publisher.example/law/company"]
