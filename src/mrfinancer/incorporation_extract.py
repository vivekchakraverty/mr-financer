"""Extract readable passages and public search results from publisher pages."""

from __future__ import annotations

import io
import re
import unicodedata
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup

from .loan_parse import content_area, safe_link

TOPICS = ("Entity types", "Eligibility", "Formation", "Compliance", "Dissolution")
QUERIES = {
    "EN": ("types of business entities", "minimum company incorporation requirements", "company registration",
           "company compliance requirements", "company dissolution procedures"),
    "FR": ("formes juridiques entreprises", "conditions création société capital minimum", "immatriculation société",
           "obligations annuelles société", "dissolution liquidation société"),
    "DE": ("Rechtsformen Unternehmen", "Mindestkapital Voraussetzungen Gesellschaft", "Gesellschaft gründen",
           "Offenlegung Jahresabschluss Gesellschaft", "Auflösung Liquidation Gesellschaft"),
    "IT": ("tipi di società", "requisiti capitale minimo società", "costituzione società",
           "adempimenti societari bilancio", "scioglimento liquidazione società"),
    "ES": ("tipos de sociedades mercantiles", "requisitos capital mínimo sociedad", "constitución sociedad",
           "obligaciones societarias cuentas anuales", "disolución liquidación sociedad"),
    "PT": ("tipos de sociedades empresárias", "requisitos capital mínimo sociedade", "registro empresa",
           "obrigações contábeis empresa", "dissolução liquidação sociedade"),
    "PL": ("rodzaje spółek", "wymagania kapitał zakładowy spółka", "rejestracja spółki",
           "obowiązki sprawozdanie finansowe spółka", "likwidacja rozwiązanie spółki"),
    "RU": ("виды юридических лиц", "требования минимальный уставный капитал", "регистрация юридического лица",
           "обязанности отчетность организации", "ликвидация юридических лиц"),
    "ZH": ("公司类型", "设立公司最低条件注册资本", "公司设立登记", "公司年报信息公示", "公司解散清算注销"),
    "JA": ("会社の種類", "会社設立要件最低資本金", "会社設立登記", "会社の事業報告", "会社解散清算"),
    "KO": ("회사 종류", "법인 설립 최소 자본 요건", "법인 설립 등록", "법인 신고 의무", "회사 해산 청산"),
    "TR": ("şirket türleri", "şirket asgari sermaye şartları", "şirket kuruluş tescil",
           "şirket yıllık yükümlülükler", "şirket tasfiye fesih"),
    "ID": ("jenis badan usaha", "persyaratan modal minimum perseroan", "pendirian pendaftaran perseroan",
           "kewajiban laporan tahunan", "pembubaran likuidasi perseroan"),
    "NL": ("rechtsvormen bedrijven", "voorwaarden minimumkapitaal vennootschap", "bedrijf oprichten inschrijven",
           "verplichtingen jaarrekening", "vennootschap ontbinden vereffenen"),
    "AR": ("أنواع الشركات", "متطلبات تأسيس الشركات الحد الأدنى لرأس المال", "تسجيل الشركة",
           "التزامات الشركات والتقارير السنوية", "حل وتصفية الشركات"),
    "HE": ("סוגי חברות", "דרישות מינימום להקמת חברה", "רישום חברה", "דוח שנתי לחברה", "פירוק חברה"),
    "SV": ("företagsformer", "krav minsta aktiekapital", "bilda registrera aktiebolag", "årsredovisning aktiebolag",
           "likvidation av aktiebolag"),
    "NO": ("organisasjonsformer", "krav minste aksjekapital", "stifte registrere aksjeselskap", "årsregnskap",
           "avvikle aksjeselskap"),
}
EN_TERMS = (
    ("legal form", "types of compan", "business structure", "business type", "company type", "sole propriet", "limited by shares", "partnership"),
    ("director", "shareholder", "requirements", "registered office", "minimum capital", "capital requirement", "minimum share capital", "capital stock", "eligib"),
    ("register", "incorporat", "establish", "formation", "application", "filing fee", "articles of association"),
    ("annual return", "annual report", "annual account", "annual review", "annual statement", "solvency resolution", "tax return", "financial statement", "disclosure", "compliance", "file your"),
    ("dissolv", "dissolution", "strike off", "strike-off", "wind up", "winding up", "liquidat", "deregister", "closure"),
)
LOCAL_TERMS = {
    "FR": ("forme juridique", "capital social", "immatriculation", "comptes annuels", "dissolution"),
    "DE": ("rechtsform", "stammkapital", "gründung", "jahresabschluss", "liquidation"),
    "IT": ("società", "capitale sociale", "costituzione", "bilancio", "liquidazione"),
    "ES": ("tipos de sociedad", "capital social", "constitución", "cuentas anuales", "liquidación"),
    "PT": ("tipos de sociedade", "capital social", "registro", "escrituração", "liquidação"),
    "PL": ("rodzaje spółek", "kapitał zakładowy", "rejestracja", "sprawozdanie finansowe", "likwidacja"),
    "RU": ("юридическ", "уставный капитал", "регистрац", "отчетност", "ликвидац"),
    "ZH": ("公司", "资本", "登记", "年度报告", "清算"),
    "JA": ("会社の種類", "資本金", "登記", "報告", "清算"),
    "KO": ("회사 종류", "자본금", "설립", "신고", "청산"),
    "TR": ("şirket tür", "sermaye", "kuruluş", "yıllık", "tasfiye"),
    "ID": ("badan usaha", "modal", "pendirian", "laporan tahunan", "pembubaran"),
    "NL": ("rechtsvorm", "kapitaal", "inschrijven", "jaarrekening", "ontbinden"),
    "AR": ("أنواع الشركات", "رأس المال", "تأسيس", "السنة المالية", "تصفية"),
    "HE": ("סוגי חברות", "מניות", "רישום", "דוח שנתי", "פירוק"),
    "SV": ("företagsform", "aktiekapital", "registrera", "årsredovisning", "likvidation"),
    "NO": ("organisasjonsform", "aksjekapital", "registrere", "årsregnskap", "avvikle"),
}
MAX_EXCERPTS = 6
RESULT_SECTIONS = (
    '[id*="search-result" i], [class*="search-result" i], [class*="searchResult"], '
    '[id*="resultat" i], [class*="risultat" i], [class*="search-list" i], [id*="wynik" i], '
    '[class*="result-list" i], [class*="result-item" i], #elenco_risultati .boxAtto, main article, main [role="listitem"]'
)


def tidy(text):
    return " ".join(text.split())


def folded(text):
    stripped = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    return unicodedata.normalize("NFKC", stripped)


def language_of(source):
    code = re.match(r"[A-Za-z]+", source["language"] or "")
    return code[0].upper() if code and code[0].upper() in QUERIES else "EN"


def search_tasks(source):
    language = language_of(source)
    # A general-purpose database can answer any of the five workbook topics.
    return [(topic, query) for topic, query in zip(TOPICS, QUERIES[language])]


def terms_for(source):
    lang = language_of(source)
    index = [i for i, topic in enumerate(TOPICS) if topic in source["topics"]] or list(range(len(TOPICS)))
    terms = [word for i in index for word in EN_TERMS[i]]
    if lang in LOCAL_TERMS:
        terms.extend(LOCAL_TERMS[lang][i] for i in index)
        terms.extend(word for i in index for word in QUERIES[lang][i].split() if len(word) > 4)
    if lang == "ZH":
        terms += ["資本", "登記", "年報", "解散", "公司種類"]
    return tuple(dict.fromkeys(folded(term) for term in terms))


def score(text, terms):
    final = folded(text)
    return sum(term in final for term in terms)


def clip(text, limit=340):
    if len(text) <= limit:
        return text
    prefix, _, _ = text[:limit].rpartition(" ")
    return (prefix if len(prefix) >= limit * .7 else text[:limit]) + "…"


def with_fragment(url, locator):
    return url.split("#")[0] + ("#" + quote(locator, safe="-_.") if locator else "")


def keep_ranked(candidates):
    ordered = sorted(candidates, key=lambda item: (-item[0], item[1]))
    used = set()
    output = []
    for weight, _, item in ordered:
        key = folded(item["text"])
        if key not in used and weight > 0:
            output.append(item)
            used.add(key)
        if len(output) >= MAX_EXCERPTS:
            break
    return output


def parse_html(html, url, source):
    soup = BeautifulSoup(html, "lxml")
    main = (soup.select_one("#testoNormalizzato, .law-reg-content") or soup.select_one("main, [role=main]")
            or content_area(soup, "#mainContent, #main-content, #contenedor, article"))
    for menu in main.select("script, style, form, iframe, nav, [role=navigation], footer, "
                            ".breadcrumb, .breadcrumbs, .cookie-banner, .site-header, .sr-only"):
        menu.decompose()
    heading = source["title"]
    anchor = None
    candidates = []
    terms = terms_for(source)
    for i, node in enumerate(main.select("h1, h2, h3, h4, p, li, tr, dt, dd, blockquote, .col-data, .art_text_in_comma")[:3200]):
        text = tidy(node.get_text(" ", strip=True))
        if node.name in {"h1", "h2", "h3", "h4"}:
            if 0 < len(text) < 170:
                heading = text
                anchor = node.get("id") or (node.find("a", id=True) or {}).get("id")
            continue
        if node.name == "li" and node.find(["ul", "ol", "p"]):
            continue
        if len(text) < (14 if node.name in {"li", "tr", "dt", "dd"} else 48) or len(text) > 3500:
            continue
        if text.endswith("?") and node.name == "li":
            continue
        value = score(text, terms)
        heading_value = score(heading, terms) if heading != source["title"] else 0
        if value or heading_value and len(text) >= 60:
            label, fragment = heading, anchor
            if "col-data" in node.get("class", ()):
                section = node.find_parent(class_="row")
                number = section.select_one(".col-no") if section else None
                if number:
                    label = tidy(number.get_text(" ", strip=True)) or heading
                    locator = number.find("a", id=True) or number.find("a", attrs={"name": True}) or {}
                    fragment = locator.get("id") or locator.get("name") or anchor
            candidates.append((value * 4 + heading_value, i, {
                "title": label, "text": clip(text), "url": with_fragment(url, fragment),
            }))
    return keep_ranked(candidates)


def parse_pdf(data, url, source):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        return []
    terms = terms_for(source)
    candidates = []
    for index, page in enumerate(reader.pages[:42]):
        text = tidy(page.extract_text() or "")
        if len(text) < 90:
            continue
        for match in re.finditer(r".{100,320}(?:\s|$)", text):
            passage = tidy(match[0])
            value = score(passage, terms)
            if value:
                page_number = index + 1
                candidates.append((value, index * 10000 + match.start(), {
                    "title": f"Page {page_number}", "text": clip(passage), "url": url.split("#")[0] + f"#page={page_number}",
                }))
    return keep_ranked(candidates)


def parse_search(html, url, hosts, source, topic, query):
    soup = BeautifulSoup(html, "lxml")
    terms = terms_for({**source, "topics": [topic]})
    candidates = []
    for root in soup.select(RESULT_SECTIONS):
        for node in root.select("a[href]"):
            title = tidy(node.get_text(" ", strip=True))
            href = safe_link(url, node.get("href"))
            if len(title) < 12 or not href:
                continue
            address = urlsplit(href)
            if address.scheme != "https" or address.hostname not in hosts or href.split("#")[0] == url.split("#")[0]:
                continue
            parent = node.find_parent(["li", "article"]) or node.find_parent(class_="boxAtto") or node.parent
            excerpt = tidy(parent.get_text(" ", strip=True))
            if len(excerpt) > 1000:
                excerpt = title
            if "boxAtto" in parent.get("class", ()):
                heading = parent.select_one(".collapse-header p + p")
                if heading:
                    excerpt = tidy(heading.get_text(" ", strip=True))
            rank = score(title, terms) * 3 + score(excerpt, terms)
            if rank:
                item = {"title": clip(title, 140), "text": clip(excerpt) if excerpt != title else "",
                        "url": href, "query": query, "topic": topic}
                candidates.append((rank, item))
    unique = {}
    for _, item in sorted(candidates, key=lambda candidate: -candidate[0]):
        unique.setdefault(item["url"], item)
    return list(unique.values())[:3]
