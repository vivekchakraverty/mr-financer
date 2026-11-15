"""Extract attributable provider records from public directories and exports."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

MAX_ROWS = 100
# Headers record how the publisher identified the institution or loan product.
NAME_HEADERS = (
    "name", "institution", "name of bank", "name of nbfc", "name of nbfc/arc", "name of nbfc / arc", "lender name", "lendername", "bank", "company", "lender", "financial entity",
    "entity", "provider", "nbfc name", "firm", "finance company", "product", "trading name",
    "website", "registered name", "registrant", "licence holder", "licensee", "the name of a financial institution", "name(english)", "name (english)",
    "nombre", "denominacion", "razon social", "entidad", "entidade", "nome",
    "instituicao", "banco", "bezeichnung", "institutsname", "unternehmen",
    "institut", "firma", "navn", "foretak", "foretagsnamn", "naam", "instelling",
    "nom", "etablissement", "denomination", "raison sociale", "societe", "bezeichnung des instituts", "name des instituts", "firmenname",
    "nazwa", "kurulus", "unvan", "denominazione", "banca", "ragione sociale",
    "наименование", "организация", "金融機関", "機関名", "机构名称", "銀行名稱",
    "銀行名稱及地址", "금융회사", "금융 회사", "금융회사명", "금융상품명", "상품명", "ศูนย์", "اسم",
)
NAME_PREFIXES = ("name of ", "the name of ", "name of nbfc", "institution name", "bank name", "company name", "financial institution name",
                 "entity name", "firm name", "lender name", "product name", "name /", "nome da ",
                 "nome de ", "nome empresarial", "nombre de ", "nom de ", "naam van ", "nazwa ")
INSTITUTION = re.compile(
    r"\b(?:bank(?:s|ing|a|en)?|banc[aoe]|banque|banques|banca|credit|credito|creditunion|"
    r"lending|loans?|moneylenders?|financ(?:e|ial|iera|iero|iere|iera|iele|eira)|"
    r"gesellsch[a-z]+|sparkasse[n]?|finans|banco|sparbank|bancorp)\b"
    r"|銀行|银行|信用金庫|信用組合|금융|은행|банк|مصرف", re.I,
)
CATEGORY = re.compile(
    r"\b(?:all|list|register|directory|search|download|category|types?|coverage|licensing|licence|"
    r"license|licensed|authorised|authorized|regulated|supervised|entities|institutions|statistics)\b",
    re.I,
)
EXPORTED = re.compile(r"\.(?:pdf|xlsx|csv)(?:$|\?)", re.I)


def clean(value, limit=240):
    return " ".join(str(value if value is not None else "").split())[:limit]


def normalize(value):
    folded = "".join(c for c in unicodedata.normalize("NFKD", clean(value).casefold())
                     if not unicodedata.combining(c))
    return unicodedata.normalize("NFKC", folded).replace("’", "'")


def safe_link(base, value):
    if not isinstance(value, str) or not value or value.startswith(("#", "?")):
        return None
    try:
        result = urljoin(base, value)
        parsed = urlsplit(result)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return result
    except ValueError:
        pass
    return None


def content_area(soup, selector=None):
    if selector:
        selected = soup.select_one(selector)
        if selected:
            return selected
    return soup.select_one("main, [role=main]") or soup.select_one(
        "#main-content, #mainContent, #content, #main, .main-content, article"
    ) or soup.body or soup


def name_index(labels):
    return next((i for i, label in enumerate(labels)
                 if normalize(label).strip(" .:*") in NAME_HEADERS
                 or normalize(label).startswith(NAME_PREFIXES)), None)


def valid_name(text):
    return 2 <= len(text) <= 180 and any(char.isalpha() for char in text) and normalize(text) not in {
        "name", "nom", "nama", "number", "no", "institution", "entity name", "bank name",
        "website", "details", "view details", "banks", "bank", "credit institution", "credit institutions", "non-banks", "credit associations", "digital banks", "finance companies", "banking business", "total", "totals", "search", "loading",
        "no results", "no results found", "no records found",
    }


def record(name, fields=None, url=None):
    return {"name": name, "fields": fields or {}, "url": url}


def tabular_records(matrix):
    for i, (labels, _) in enumerate(matrix[:25]):
        column = name_index(labels)
        if column is not None and column < len(labels):
            break
    else:
        return []
    records = []
    for values, href in matrix[i + 1:]:
        if column >= len(values) or len(values) != len(labels):
            continue
        name = values[column]
        if not valid_name(name) or normalize(name) == normalize(labels[column]):
            continue
        fields = {key: value for j, (key, value) in enumerate(zip(labels, values))
                  if j != column and key and value and key not in {"#", "No."}}
        records.append(record(name, dict(list(fields.items())[:9]), href))
    return records


def unique_records(rows):
    found = {}
    for row in rows:
        key = (normalize(row["name"]), tuple(normalize(text) for text in row["fields"].values()))
        found[key] = row
        if len(found) >= MAX_ROWS:
            break
    return list(found.values())


def named_records(html, url, selector):
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for node in soup.select(selector):
        for decoration in node.select(".satirNo, svg, script, style"):
            decoration.decompose()
        text = clean(node.get_text(" ", strip=True))
        anchor = node if node.name == "a" else node.find("a", href=True) or node.find_parent("a", href=True)
        href = safe_link(url, anchor.get("href") if anchor else None)
        if valid_name(text):
            rows.append(record(text, url=href))
    return unique_records(rows)


def html_records(html, url, *, selector=None, source_id=""):
    soup = BeautifulSoup(html, "lxml")
    area = content_area(soup, selector)
    rows = []
    if source_id in {"sama-finance", "sama-banks"}:
        for card in soup.select(".details-card__body"):
            fields = {}
            for item in card.select(".readonly-data"):
                label, value = item.select_one(".title"), item.select_one(".value")
                if label and value:
                    fields[clean(label.get_text(" ", strip=True))] = clean(value.get_text(" ", strip=True))
            name = fields.pop("Company Name", "") or fields.pop("Bank Name", "")
            anchor = card.find("a", href=True)
            if valid_name(name):
                rows.append(record(name, fields, safe_link(url, anchor["href"]) if anchor else None))
        return unique_records(rows)
    if source_id == "fss":
        for tr in soup.select("tr[data-finprdtcd], tr[data-fincono]"):
            table = tr.find_parent("table")
            cells = tr.find_all("td", recursive=False)
            if not table or len(cells) < 3:
                continue
            headings = table.select("thead tr:first-child th")
            labels = [clean(item.get_text(" ", strip=True)) for item in headings]
            column = name_index(labels)
            if column is None or column >= len(cells):
                continue
            name = clean(cells[column].get_text(" ", strip=True))
            if not valid_name(name):
                continue
            fields = {}
            product = clean(tr.get("data-finprdtnm") or tr.get("data-crdtprdttypenm"))
            if product:
                fields["Product"] = product
            month = tr.get("data-dclsmonth", "")
            if re.fullmatch(r"\d{6}", month):
                fields["Reporting month"] = month[:4] + "-" + month[4:]
            for header, cell in zip(labels, cells):
                if "평균금리" in header:
                    rate = clean(cell.get_text(" ", strip=True))
                    if rate and rate != "-":
                        fields["Published average rate"] = rate
            if rate_type := tr.get("data-crdtlendratenm"):
                fields["Rate type"] = clean(rate_type)
            rows.append(record(name, fields))
        return unique_records(rows)
    if source_id == "knf":
        for table in area.select("table"):
            details = {}
            for tr in table.select("tr"):
                cells = [clean(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"], recursive=False)]
                if len(cells) == 2 and all(cells):
                    details[cells[0]] = cells[1]
            name = details.pop("Nazwa", "")
            if valid_name(name):
                rows.append(record(name, dict(list(details.items())[:9])))
        return unique_records(rows)
    for block in area.select("nav, header, footer, script, style, noscript, [role=navigation], [aria-hidden=true]"):
        block.decompose()
    for table in area.select("table"):
        matrix = []
        headings = [clean(tag.get_text(" ", strip=True)) for tag in table.select("thead tr:last-child th")]
        col = name_index(headings)
        for tr in table.select("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if not cells:
                continue
            for cell in cells:
                for decoration in cell.select(".download-tile, svg, button, .tooltip"):
                    decoration.decompose()
            if source_id == "canada-banks":
                for cell in cells:
                    for decoration in cell.select(".sr-only, .visually-hidden"):
                        decoration.decompose()
            values = [clean(cell.get_text(" ", strip=True)) for cell in cells]
            if source_id == "canada-banks" and values:
                values[0] = re.sub(r"\s+profile$", "", values[0], flags=re.I)
            if source_id == "apra" and len(cells) == 1 and cells[0].find("p"):
                values[0] = clean(cells[0].find("p").get_text(" ", strip=True))
            if col is None and any(cell.name == "th" for cell in cells):
                col = name_index(values)
            anchor = cells[col].find("a", href=True) if col is not None and col < len(cells) else tr.find("a", href=True)
            href = safe_link(url, anchor.get("href") if anchor else None)
            if href and EXPORTED.search(href):
                href = None
            matrix.append((values, href))
        rows.extend(tabular_records(matrix))
    for item in area.select("[role=row]"):
        table = item.find_parent(attrs={"role": "table"}) or item.find_parent(attrs={"role": "grid"})
        if table and table.get("data-mrf-parsed") is None:
            matrix = []
            for tr in table.select("[role=row]"):
                cells = tr.select("[role=columnheader], [role=cell], [role=gridcell]")
                anchor = tr.find("a", href=True)
                matrix.append(([clean(cell.get_text(" ", strip=True)) for cell in cells],
                               safe_link(url, anchor.get("href") if anchor else None)))
            rows.extend(tabular_records(matrix))
            table["data-mrf-parsed"] = "1"
    if rows:
        return unique_records(rows)
    if source_id in {"osfi", "apra", "dnb", "norway", "sama-banks", "sama-finance", "regafi", "bafin", "fi", "fca", "cnbv", "bcra", "rbi", "rbi-nbfc", "nfra", "bcb", "cbi-downloads", "bde", "sba", "finma", "fsa-japan", "fss", "bank-italy"}:
        return []
    for node in area.select("li, .institution-name, .lender-name, .provider-name, .company-name"):
        if node.find(["ul", "ol", "table"]):
            continue
        anchor = node.find("a", href=True)
        text = clean(node.get_text(" ", strip=True))
        href = safe_link(url, anchor.get("href") if anchor else None)
        if valid_name(text) and INSTITUTION.search(normalize(text)) and not CATEGORY.search(normalize(text)):
            if not href or not EXPORTED.search(href):
                rows.append(record(text, url=href))
    return unique_records(rows)


def register_pdf_rows(lines, url):
    """Read the numbered rows and continuation lines in these two public registers."""
    rows = []
    current = None
    for line in lines:
        text = line.strip()
        if not text or text.startswith(("S/No.", "Page", "Number")):
            continue
        parts = re.split(r"\s{2,}", text)
        if len(parts) > 1 and parts[0].isdigit() and valid_name(parts[1]):
            if current:
                rows.append(current)
            licence = re.search(r"\b\d{1,4}/\d{4}\b", line)
            link = re.search(r"(?:https?://|www\.)[^\s]+", line)
            href = safe_link(url, ("https://" if link[0].startswith("www.") else "") + link[0]) if link else None
            current = record(clean(parts[1]), {"Licence number": licence[0]} if licence else {}, href)
        elif current and re.match(r"^\s{8,}[A-Za-z]", line):
            continuation = parts[0]
            if re.fullmatch(r"[A-Za-z][A-Za-z.'() &,/-]{0,45}", continuation) and re.search(
                r"\b(?:LTD|PTE|Limited|LLC|PLC|INC)\b", continuation, re.I
            ):
                current["name"] = clean(f'{current["name"]} {continuation}')
    if current:
        rows.append(current)
    return rows


def emirates_pdf_rows(lines):
    rows = []
    licence_pattern = r"\b\d{2}\.\d{2}\.\d{2}\.\d{3}\.\d{4}\.\d{2}\b"
    for line in lines:
        # The English institution-name column ends before the licence type.
        number = re.match(r"^\s{2,12}(\d{1,4})(?:\s{2,}|$)", line[:16])
        name = clean(line[15:39])
        if number and name and name.isascii() and valid_name(name):
            rows.append((name, re.search(licence_pattern, line), []))
            continue
        if not name or not name.isascii() or not valid_name(name) or name in {"Name", "Institution"}:
            continue
        if rows:
            rows[-1][2].append((name, re.search(licence_pattern, line), clean(line[40:62])))
        elif re.search(r"\b(?:Foreign|National)\b", line[38:62]):
            rows.append((name, None, []))
    output = []
    pending = ""
    for name, licence, tail in rows:
        if pending:
            name = pending + " " + name
            pending = ""
        for extra, tail_licence, register_type in tail:
            if not tail_licence and re.search(r"^(?:foreign|national)\b", register_type, re.I):
                pending = extra
            else:
                name += " " + extra
                licence = licence or tail_licence
        if licence:
            output.append(record(clean(name), {"Licence number": licence[0]}))
        elif not tail and not pending:
            pending = name
    return output


def exported_records(body, url, mime):
    path = urlsplit(url).path.lower()
    if body.startswith(b"%PDF") or path.endswith(".pdf") or "pdf" in mime:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(body))
        rows = []
        if urlsplit(url).hostname == "www.nfra.gov.cn":
            rows = []
            for page in reader.pages[:18]:
                for line in (page.extract_text(extraction_mode="layout") or "").splitlines():
                    match = re.match(r"^\s*\d+\s+([\u3400-\u9fff][\u3400-\u9fff（）()\s]*?)\s{2,}(.+?)\s*([A-Z]\d{4}[A-Z]\d{9})\s+(.+)", line)
                    if match:
                        name, english, code, details = match.groups()
                        institution_type = re.split(r"\s{2,}", details.strip())[0]
                        rows.append(record(clean(name), {"English name": clean(english), "Institution code": code,
                                                         "Institution type": clean(institution_type)}))
            return unique_records(rows)
        singapore = "/mlaw-rom-list-licensed-moneylenders" in path
        emirates = "/cb-register-" in path
        for page in reader.pages[:35]:
            lines = (page.extract_text(extraction_mode="layout") or "").splitlines()
            if singapore or emirates:
                rows.extend(register_pdf_rows(lines, url) if singapore else emirates_pdf_rows(lines))
                continue
            for line in lines:
                parts = [clean(part) for part in re.split(r"\s{2,}", line.strip()) if clean(part)]
                if parts and parts[0].rstrip(".").isdigit():
                    parts.pop(0)
                elif parts and re.match(r"^\d+\s+\D", parts[0]):
                    parts[0] = re.sub(r"^\d+\s+", "", parts[0])
                if parts and valid_name(parts[0]) and INSTITUTION.search(normalize(parts[0])) and not CATEGORY.search(normalize(parts[0])):
                    rows.append(record(parts[0], {"Register details": " · ".join(parts[1:])[:240]} if len(parts) > 1 else {}))
        return unique_records(rows)
    if body.startswith(b"PK") and (path.endswith(".xlsx") or "spreadsheet" in mime):
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(body), read_only=True, data_only=True)
        try:
            rows = []
            for sheet in book.worksheets[:10]:
                matrix = [([clean(cell) for cell in row], None)
                          for row in sheet.iter_rows(max_row=MAX_ROWS + 35, max_col=min(sheet.max_column or 30, 30), values_only=True)]
                rows.extend(tabular_records(matrix))
            return unique_records(rows)
        finally:
            book.close()
    if path.endswith(".csv") or "text/csv" in mime:
        try:
            text = body.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = body.decode("cp1252", errors="replace")
        lines = [line for line in text.splitlines() if line.strip()][:MAX_ROWS + 35]
        try:
            dialect = csv.Sniffer().sniff("\n".join(lines[:12]), delimiters=",;\t|")
        except csv.Error:
            dialect = "excel"
        return unique_records(tabular_records([([clean(cell) for cell in row], None)
                                                for row in csv.reader(lines, dialect)]))
    return []
