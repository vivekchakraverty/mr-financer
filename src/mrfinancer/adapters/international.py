"""Official filing lookups, with registry identifiers and source documents."""

from __future__ import annotations

import asyncio
import math
import re
from calendar import monthrange
from datetime import date
from io import BytesIO
from urllib.parse import quote
from xml.etree import ElementTree
from zipfile import ZipFile

from ..models import CompanyIdentity, FilingDocument, FinancialFact, Jurisdiction, Metric
from ..net import FetchError, Fetcher
from ..provenance import AccessMethod, Gap, Provenance, ReasonCode, Trust
from ..textutil import normalize_company_name

M = Metric
NO_REG = "https://data.brreg.no/enhetsregisteret/api/enheter"
NO_ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap"
SK = "https://www.registeruz.sk/cruz-public"
KR = "https://engopendart.fss.or.kr/engapi"
FR = "https://registre-national-entreprises.inpi.fr/api"
FR_SEARCH = "https://recherche-entreprises.api.gouv.fr/search"
SEC = "https://data.sec.gov"
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"


def parsed_date(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw or "")
    except ValueError:
        return None


def month_boundary(raw: str | None, *, end: bool = False) -> date | None:
    first = parsed_date(f"{raw}-01")
    if end and first:
        return first.replace(day=monthrange(first.year, first.month)[1])
    return first


class FilingAdapter:
    is_scraper = False

    def available(self) -> Gap | None:
        return None

    def evidence(self, url: str, note: str | None = None) -> Provenance:
        return Provenance(source=self.id, source_name=self.name, url=url, note=note,
                          method=AccessMethod.OFFICIAL_API, trust=Trust.STATUTORY)

    def identity(self, name: str, registry_id: str, url: str) -> CompanyIdentity:
        return CompanyIdentity(name=name, jurisdiction=self.jurisdictions[0],
                               registry_id=registry_id, match_confidence=1,
                               match_basis="Registry identifier or unique legal name",
                               provenance=self.evidence(url))

    def unique_name(self, name: str, rows: list[CompanyIdentity]):
        normalized = normalize_company_name(name)
        exact = {row.registry_id: row for row in rows
                 if normalized and normalize_company_name(row.name) == normalized}
        candidates = list(exact.values())
        return (candidates[0], []) if len(candidates) == 1 else (None, candidates)

    def result(self, facts, documents):
        gaps = [] if facts or documents else [
            Gap(field="financials", source=self.id, reason=ReasonCode.NOT_FOUND,
                detail="No usable public annual statements were returned for this entity.")
        ]
        return facts, documents, gaps

    def fact(self, metric, raw, currency, start, end, scope, evidence) -> FinancialFact | None:
        if raw is None or not end or not currency or isinstance(raw, bool):
            return None
        try:
            value = float(str(raw).strip().replace(",", "").replace("\u00a0", ""))
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        return FinancialFact(metric=metric, value=value, currency=currency, period_start=start,
                             period_end=end, statement_scope=scope, as_filed=True,
                             source_text=str(raw), provenance=evidence)

    async def funding(self, company, fetcher):
        return [], []


class NorwayAdapter(FilingAdapter):
    id = "brreg"
    name = "Norway Register of Company Accounts"
    jurisdictions = (Jurisdiction.NO,)

    async def identify(self, company_name, domain, fetcher):
        payload = await fetcher.get_json(NO_REG, params={"navn": company_name, "size": 25})
        rows = payload.get("_embedded", {}).get("enheter", [])
        candidates = [self.identity(row["navn"], row["organisasjonsnummer"],
                                    f"{NO_REG}/{row['organisasjonsnummer']}")
                      for row in rows if row.get("navn") and row.get("organisasjonsnummer")]
        identity, ambiguous = self.unique_name(company_name, candidates)
        if identity and payload.get("page", {}).get("totalPages", 1) > 1:
            return None, [identity]
        return identity, ambiguous

    async def lookup_id(self, registry_id, fetcher):
        row = await fetcher.get_json(f"{NO_REG}/{registry_id}")
        return self.identity(row["navn"], registry_id, f"{NO_REG}/{registry_id}")

    async def records(self, company, fetcher):
        url = f"{NO_ACCOUNTS}/{company.registry_id}"
        rows = await fetcher.get_json(url)
        facts, documents = [], []
        latest = {}
        for row in sorted(rows, key=lambda item: item.get("journalnr") or "", reverse=True):
            if row.get("regnskapstype") != "SELSKAP":
                continue
            period = row.get("regnskapsperiode", {})
            if period.get("tilDato"):
                latest.setdefault(period["tilDato"], row)
        for closing in sorted(latest, reverse=True)[:3]:
            row = latest[closing]
            start = parsed_date(row["regnskapsperiode"].get("fraDato"))
            end = parsed_date(closing)
            if not end:
                continue
            result = row.get("resultatregnskapResultat") or {}
            assets = row.get("eiendeler") or {}
            balance = row.get("egenkapitalGjeld") or {}
            operating = result.get("driftsresultat") or {}
            incomes = operating.get("driftsinntekter") or {}
            fields = {
                M.TURNOVER: incomes.get("salgsinntekter"),
                M.OPERATING_PROFIT: operating.get("driftsresultat"),
                M.PROFIT_BEFORE_TAX: result.get("ordinaertResultatFoerSkattekostnad"),
                M.NET_PROFIT: result.get("aarsresultat"),
                M.TOTAL_ASSETS: assets.get("sumEiendeler"),
                M.CASH: assets.get("sumBankinnskuddOgKontanter"),
                M.TOTAL_LIABILITIES: (balance.get("gjeldOversikt") or {}).get("sumGjeld"),
                M.NET_ASSETS: (balance.get("egenkapital") or {}).get("sumEgenkapital"),
            }
            evidence = self.evidence(url)
            for metric, raw in fields.items():
                fact = self.fact(metric, raw, row.get("valuta"), start, end, "standalone", evidence)
                if fact:
                    facts.append(fact)
            documents.append(FilingDocument(title="Annual accounts", period_end=end,
                provenance=self.evidence(f"{NO_ACCOUNTS}/aarsregnskap/kopi/{company.registry_id}/{end.year}")))
        return self.result(facts, documents)


class SlovakiaAdapter(FilingAdapter):
    id = "registeruz"
    name = "Slovakia Register of Financial Statements"
    jurisdictions = (Jurisdiction.SK,)

    async def identify(self, company_name, domain, fetcher):
        return None, []

    async def lookup_id(self, registry_id, fetcher):
        ids = await fetcher.get_json(f"{SK}/api/uctovne-jednotky",
                                     params={"ico": registry_id, "zmenene-od": "2000-01-01"})
        units = await asyncio.gather(*(self.json_detail("uctovna-jednotka", i, fetcher)
                                      for i in ids.get("id", [])[:12]))
        units = [unit for unit in units if unit.get("ico") == registry_id]
        if not units:
            return None
        identity = self.identity(units[0]["nazovUJ"], registry_id,
                                 f"{SK}/api/uctovna-jednotka?id={units[0]['id']}")
        return identity

    async def json_detail(self, endpoint, object_id, fetcher):
        return await fetcher.get_json(f"{SK}/api/{endpoint}", params={"id": object_id})

    async def records(self, company, fetcher):
        ids = await fetcher.get_json(f"{SK}/api/uctovne-jednotky",
                                     params={"ico": company.registry_id, "zmenene-od": "2000-01-01"})
        units = await asyncio.gather(*(self.json_detail("uctovna-jednotka", i, fetcher)
                                      for i in ids.get("id", [])[:12]))
        filing_ids = {filing for unit in units if unit.get("ico") == company.registry_id
                      for filing in unit.get("idUctovnychZavierok", [])}
        filings = await asyncio.gather(*(self.json_detail("uctovna-zavierka", i, fetcher)
                                        for i in sorted(filing_ids, reverse=True)[:12]))
        annual = [row for row in filings if row.get("typ") == "Riadna" and row.get("obdobieDo")]
        standalone = [row for row in annual if not row.get("konsolidovana")]
        by_period = {}
        for row in sorted(standalone or annual,
                          key=lambda item: (item.get("datumPoslednejUpravy") or "", item["id"]),
                          reverse=True):
            by_period.setdefault((row.get("obdobieOd"), row["obdobieDo"]), row)
        documents, facts = [], []
        for (opening, closing), filing in sorted(by_period.items(), key=lambda item: item[0][1], reverse=True)[:3]:
            end = month_boundary(closing, end=True)
            documents.append(FilingDocument(title="Annual filing details", period_end=end,
                provenance=self.evidence(f"{SK}/api/uctovna-zavierka?id={filing['id']}")))
            reports = await asyncio.gather(*(self.json_detail("uctovny-vykaz", i, fetcher)
                                           for i in filing.get("idUctovnychVykazov", [])))
            for report in reports:
                tables = (report.get("obsah") or {}).get("tabulky") or []
                if tables:
                    documents.append(FilingDocument(title="Structured statement (JSON)", period_end=end,
                        provenance=self.evidence(f"{SK}/api/uctovny-vykaz?id={report['id']}")))
                if report.get("idSablony") == 699 and [len(table.get("data", [])) for table in tables] == [312, 134, 122]:
                    template = await self.json_detail("sablona", 699, fetcher)
                    facts.extend(self.template_699(tables, template, opening, end, filing, report["id"]))
                for attachment in report.get("prilohy", []):
                    if attachment.get("id") is None:
                        continue
                    documents.append(FilingDocument(title=attachment.get("nazov") or "Filed attachment",
                        period_end=end, provenance=self.evidence(
                            f"{SK}/domain/financialreport/attachment/{attachment['id']}")))
        return self.result(facts, documents)

    def template_699(self, tables, template, opening, end, filing, report_id):
        """Template 699 publishes amounts in euros with row-major current/prior columns."""
        fields = (
            {1: (M.TOTAL_ASSETS, 2, "spolu majetok")},
            {80: (M.NET_ASSETS, 0, "vlastne imanie"), 101: (M.TOTAL_LIABILITIES, 0, "zavazky")},
            {1: (M.TURNOVER, 0, "cisty obrat"),
             27: (M.OPERATING_PROFIT, 0, "vysledok hospodarenia z hospodarskej cinnosti"),
             56: (M.PROFIT_BEFORE_TAX, 0, "vysledok hospodarenia za uctovne obdobie pred zdanenim"),
             61: (M.NET_PROFIT, 0, "vysledok hospodarenia za uctovne obdobie po zdaneni")},
        )
        scope = "consolidated" if filing.get("konsolidovana") else "standalone"
        evidence = self.evidence(f"{SK}/api/uctovny-vykaz?id={report_id}", "RÃšZ statement template 699")
        result = []
        for table, definition, selection in zip(tables, template.get("tabulky", []), fields):
            rows = definition.get("riadky") or []
            if not rows or table.get("nazov", {}).get("sk") != definition.get("nazov", {}).get("sk"):
                continue
            width, remainder = divmod(len(table["data"]), len(rows))
            if remainder:
                continue
            for position, row in enumerate(rows):
                spec = selection.get(row.get("cisloRiadku"))
                if not spec:
                    continue
                metric, column, prefix = spec
                label = normalize_company_name(row.get("text", {}).get("sk") or "")
                if column >= width or not label.startswith(prefix):
                    continue
                raw = table["data"][position * width + column]
                fact = self.fact(metric, raw, "EUR", month_boundary(opening), end, scope, evidence)
                if fact:
                    result.append(fact)
        return result


class SecAdapter(FilingAdapter):
    id = "sec_edgar"
    name = "US SEC EDGAR"
    jurisdictions = (Jurisdiction.US,)

    def __init__(self, contact: str | None):
        self.contact = contact

    def available(self):
        if not self.contact:
            return Gap(field="financials", source=self.id, reason=ReasonCode.ADAPTER_DISABLED,
                       detail="Set MRF_SEC_CONTACT to the app operator email for SEC fair access.")
        return None

    async def get(self, url, fetcher):
        return await fetcher.get_json(url, headers={"User-Agent": f"MrFinancer {self.contact}"})

    async def lookup_id(self, registry_id, fetcher):
        identifier = registry_id.zfill(10)
        row = await self.get(f"{SEC}/submissions/CIK{identifier}.json", fetcher)
        return self.identity(row["name"], identifier, f"https://www.sec.gov/edgar/browse/?CIK={identifier}")

    async def identify(self, company_name, domain, fetcher):
        data = await self.get("https://www.sec.gov/files/company_tickers.json", fetcher)
        candidates = [self.identity(row["title"], str(row["cik_str"]).zfill(10),
                                    f"https://www.sec.gov/edgar/browse/?CIK={row['cik_str']}")
                      for row in data.values()
                      if normalize_company_name(row["title"]) == normalize_company_name(company_name)]
        return self.unique_name(company_name, candidates)

    async def records(self, company, fetcher):
        identifier = company.registry_id.zfill(10)
        url = f"{SEC}/api/xbrl/companyfacts/CIK{identifier}.json"
        data = await self.get(url, fetcher)
        tags = {
            M.TURNOVER: ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "Revenue"),
            M.NET_PROFIT: ("NetIncomeLoss", "ProfitLoss"),
            M.OPERATING_PROFIT: ("OperatingIncomeLoss", "ProfitLossFromOperatingActivities"),
            M.PROFIT_BEFORE_TAX: ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                                   "ProfitLossBeforeTax"),
            M.TOTAL_ASSETS: ("Assets",),
            M.TOTAL_LIABILITIES: ("Liabilities",),
            M.NET_ASSETS: ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                           "StockholdersEquity", "Equity"),
            M.CASH: ("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents"),
        }
        duration = {M.TURNOVER, M.NET_PROFIT, M.OPERATING_PROFIT, M.PROFIT_BEFORE_TAX}
        facts, documents = [], {}
        for metric, alternatives in tags.items():
            figures = {}
            for taxonomy in ("us-gaap", "ifrs-full"):
                block = data.get("facts", {}).get(taxonomy, {})
                for tag in alternatives:
                    for currency, items in block.get(tag, {}).get("units", {}).items():
                        if not re.fullmatch(r"[A-Z]{3}", currency):
                            continue
                        for item in items:
                            if item.get("form") not in {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
                                continue
                            start, end = parsed_date(item.get("start")), parsed_date(item.get("end"))
                            if not end or (metric in duration and (not start or not 330 <= (end-start).days <= 400)):
                                continue
                            accession = item.get("accn", "")
                            if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
                                continue
                            key = (end, currency)
                            candidate = figures.get(key)
                            if candidate and candidate[0] >= item.get("filed", ""):
                                continue
                            index = f"{SEC_ARCHIVE}/{int(identifier)}/{accession.replace('-', '')}/{accession}-index.htm"
                            evidence = self.evidence(index, f"XBRL tag {taxonomy}:{tag}")
                            fact = self.fact(metric, item.get("val"), currency, start, end, None, evidence)
                            if fact:
                                figures[key] = (item.get("filed", ""), fact)
            for _, fact in [figures[key] for key in sorted(figures, reverse=True)[:3]]:
                facts.append(fact)
                documents.setdefault(fact.provenance.url, FilingDocument(title="SEC annual filing",
                    period_end=fact.period_end, provenance=fact.provenance.model_copy(update={"note": None})))
        return self.result(facts, list(documents.values()))


class OpenDartAdapter(FilingAdapter):
    id = "opendart"
    name = "South Korea OpenDART"
    jurisdictions = (Jurisdiction.KR,)

    def __init__(self, key):
        self.key = key
        self._directory = None
        self._lock = asyncio.Lock()

    def available(self):
        if not self.key:
            return Gap(field="financials", source=self.id, reason=ReasonCode.CREDENTIALS_MISSING,
                       detail="Set MRF_OPENDART_KEY.")
        return None

    async def directory(self, fetcher):
        async with self._lock:
            if self._directory is None:
                payload = await fetcher.get_bytes(f"{KR}/corpCode.xml", params={"crtfc_key": self.key})
                if not payload.startswith(b"PK"):
                    raise FetchError(ReasonCode.UPSTREAM_ERROR, "OpenDART could not return the company-code ZIP.")
                with ZipFile(BytesIO(payload)) as archive:
                    file = next(item for item in archive.infolist() if item.filename.lower().endswith(".xml"))
                    if file.file_size > 80_000_000:
                        raise FetchError(ReasonCode.PARSE_FAILED, "OpenDART directory exceeds the supported size.")
                    with archive.open(file) as xml:
                        rows = ElementTree.iterparse(xml, events=("end",))
                        self._directory = {}
                        for _, row in rows:
                            if row.tag == "list":
                                identifier = row.findtext("corp_code", "").strip()
                                if re.fullmatch(r"\d{8}", identifier):
                                    self._directory[identifier] = (row.findtext("corp_name", "").strip(),
                                                                   row.findtext("corp_eng_name", "").strip())
                                row.clear()
        return self._directory

    def company(self, code, names):
        return self.identity(names[1] or names[0], code,
                             f"https://englishdart.fss.or.kr/dsbc001/selectPopup.ax?selectKey={code}")

    async def lookup_id(self, registry_id, fetcher):
        names = (await self.directory(fetcher)).get(registry_id)
        return self.company(registry_id, names) if names else None

    async def identify(self, company_name, domain, fetcher):
        normalized = normalize_company_name(company_name)
        matches = [self.company(code, names) for code, names in (await self.directory(fetcher)).items()
                   if normalized and any(normalize_company_name(name) == normalized for name in names)]
        return (matches[0], []) if len(matches) == 1 else (None, matches)

    async def request(self, endpoint, fetcher, **params):
        data = await fetcher.get_json(f"{KR}/{endpoint}", params={"crtfc_key": self.key, **params},
                                      use_cache=False)
        status = data.get("status")
        if status not in ("000", "013", "014"):
            reason = ReasonCode.RATE_LIMITED if status == "020" else ReasonCode.UPSTREAM_ERROR
            raise FetchError(reason, f"OpenDART status {status}")
        return data.get("list", [])

    async def records(self, company, fetcher):
        code = company.registry_id
        current_year = date.today().year
        rows = await self.request("list.json", fetcher, corp_code=code,
                                  bgn_de=f"{current_year - 4}0101", pblntf_detail_ty="A001",
                                  last_reprt_at="Y", page_count=30)
        reports = {}
        for row in rows:
            match = re.search(r"\((\d{4})\.(\d{2})\)", row.get("report_nm") or "")
            if match:
                end = month_boundary(f"{match[1]}-{match[2]}", end=True)
                if end and re.fullmatch(r"\d{14}", str(row.get("rcept_no") or "")):
                    reports.setdefault(end, row)
        mapping = {
            "ifrs-full_Revenue": M.TURNOVER, "dart_OperatingIncomeLoss": M.OPERATING_PROFIT,
            "ifrs-full_ProfitLossFromOperatingActivities": M.OPERATING_PROFIT,
            "ifrs-full_ProfitLossBeforeTax": M.PROFIT_BEFORE_TAX, "ifrs-full_ProfitLoss": M.NET_PROFIT,
            "ifrs-full_Assets": M.TOTAL_ASSETS, "ifrs-full_Liabilities": M.TOTAL_LIABILITIES,
            "ifrs-full_Equity": M.NET_ASSETS, "ifrs-full_CashAndCashEquivalents": M.CASH,
        }
        facts, documents = [], []
        scope = "CFS"
        for end in sorted(reports, reverse=True)[:3]:
            receipt = reports[end]["rcept_no"]
            url = f"https://englishdart.fss.or.kr/dsbh001/main.do?rcpNo={receipt}"
            evidence = self.evidence(url)
            documents.append(FilingDocument(title="Annual report", period_end=end, provenance=evidence))
            params = {"corp_code": code, "bsns_year": str(end.year), "reprt_code": "11011"}
            statement = await self.request("fnlttSinglAcntAll.json", fetcher, fs_div=scope, **params)
            if not statement and not facts and scope == "CFS":
                statement = await self.request("fnlttSinglAcntAll.json", fetcher, fs_div="OFS", **params)
                if statement:
                    scope = "OFS"
            seen = set()
            for row in statement:
                metric = mapping.get(row.get("account_id"))
                if not metric or metric in seen or row.get("sj_div") not in ("BS", "IS", "CIS"):
                    continue
                reported_receipt = str(row.get("rcept_no") or receipt)
                if not re.fullmatch(r"\d{14}", reported_receipt):
                    continue
                evidence = self.evidence(f"https://englishdart.fss.or.kr/dsbh001/main.do?rcpNo={reported_receipt}")
                fact = self.fact(metric, row.get("thstrm_amount"), row.get("currency"), None, end,
                                 "consolidated" if scope == "CFS" else "standalone", evidence)
                if fact:
                    facts.append(fact)
                    seen.add(metric)
        return self.result(facts, documents)


class InpiAdapter(FilingAdapter):
    id = "inpi_rne"
    name = "France INPI National Business Register"
    jurisdictions = (Jurisdiction.FR,)

    def __init__(self, username, password):
        self.username, self.password = username, password
        self._token = None
        self._lock = asyncio.Lock()

    def available(self):
        if not (self.username and self.password):
            return Gap(field="financials", source=self.id, reason=ReasonCode.CREDENTIALS_MISSING,
                       detail="Configure INPI credentials after annual-accounts access is enabled.")
        return None

    def company(self, row):
        identifier = row["siren"]
        name = row.get("nom_raison_sociale") or row.get("nom_complet")
        return self.identity(name, identifier, f"https://annuaire-entreprises.data.gouv.fr/entreprise/{identifier}")

    async def lookup_id(self, registry_id, fetcher):
        data = await fetcher.get_json(FR_SEARCH, params={"q": registry_id, "per_page": 10})
        return next((self.company(row) for row in data.get("results", [])
                     if row.get("siren") == registry_id), None)

    async def identify(self, company_name, domain, fetcher):
        data = await fetcher.get_json(FR_SEARCH, params={"q": company_name, "per_page": 25})
        rows = [self.company(row) for row in data.get("results", [])
                if row.get("siren") and (row.get("nom_raison_sociale") or row.get("nom_complet"))]
        identity, ambiguous = self.unique_name(company_name, rows)
        if identity and data.get("total_pages", 1) > 1:
            return None, [identity]
        return identity, ambiguous

    async def token(self, fetcher, rejected=None):
        async with self._lock:
            if not self._token or self._token == rejected:
                data = await fetcher.post_json(f"{FR}/sso/login",
                    {"username": self.username, "password": self.password}, use_cache=False)
                self._token = data.get("token")
                if not self._token:
                    raise FetchError(ReasonCode.UPSTREAM_ERROR, "INPI login did not return a token.")
            return self._token

    async def records(self, company, fetcher):
        token = await self.token(fetcher)
        endpoint = f"{FR}/companies/{company.registry_id}/attachments"
        try:
            data = await fetcher.get_json(endpoint, headers={"Authorization": f"Bearer {token}"}, use_cache=False)
        except FetchError as exc:
            if "HTTP 401" not in exc.detail:
                raise
            token = await self.token(fetcher, rejected=token)
            data = await fetcher.get_json(endpoint, headers={"Authorization": f"Bearer {token}"}, use_cache=False)
        documents, facts = [], []
        structured = {}
        for kind, entries in data.items():
            if kind not in ("bilans", "bilansSaisis") or not isinstance(entries, list):
                continue
            for row in entries:
                if row.get("deleted") or row.get("confidentiality") != "Public":
                    continue
                identifier = quote(str(row.get("id") or ""), safe="")
                if not identifier:
                    continue
                balance = (row.get("bilanSaisi") or {}).get("bilan") or {}
                identity = balance.get("identite") or {}
                end = parsed_date(row.get("dateCloture") or identity.get("dateClotureExercice"))
                path = f"bilans/{identifier}/download" if kind == "bilans" else f"bilans-saisis/{identifier}"
                evidence = self.evidence(f"https://data.inpi.fr/entreprises/{company.registry_id}",
                                         "The annual accounts are available on the companyâ€™s INPI page.")
                documents.append(FilingDocument(title="Annual accounts PDF" if kind == "bilans" else "Annual accounts JSON",
                    period_end=end, provenance=self.evidence(f"{FR}/{path}", "INPI login and access required.")))
                if kind == "bilansSaisis" and end and row.get("siren") == company.registry_id:
                    stamp = row.get("dateDepot") or ""
                    if end not in structured or structured[end][0] < stamp:
                        structured[end] = (stamp, row, evidence)
        documents.sort(key=lambda item: item.period_end or date.min, reverse=True)
        for closing in sorted(structured, reverse=True)[:3]:
            _, row, evidence = structured[closing]
            facts.extend(self.parse_balance(row, closing, evidence))
        return self.result(facts, documents[:15])

    def parse_balance(self, row, end, evidence):
        balance = (row.get("bilanSaisi") or {}).get("bilan") or {}
        identity = balance.get("identite") or {}
        if identity.get("codeTypeBilan") != "C" or identity.get("codeDevise") != "EUR":
            return []
        fields = {(1, "CO"): (M.TOTAL_ASSETS, "m3"), (1, "CF"): (M.CASH, "m3"),
                  (2, "DL"): (M.NET_ASSETS, "m1"), (2, "DI"): (M.NET_PROFIT, "m1")}
        fields.update({(3, "FJ"): (M.TURNOVER, "m3"), (3, "GG"): (M.OPERATING_PROFIT, "m3")})
        result = []
        for page in (balance.get("detail") or {}).get("pages", []):
            for line in page.get("liasses", []):
                page_number = str(page.get("numero", "")).lstrip("0")
                spec = fields.get((int(page_number), line.get("code"))) if page_number.isdecimal() else None
                if spec:
                    metric, column = spec
                    fact = self.fact(metric, line.get(column), "EUR", None, end, "standalone", evidence)
                    if fact:
                        result.append(fact)
        return result

