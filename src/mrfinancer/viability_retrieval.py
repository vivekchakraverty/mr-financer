"""CPU-only lexical retrieval over fetched evidence and a reviewed guidance corpus."""
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit

from .viability_contract import BriefPacket

GUIDANCE = [
    {"id": "B1", "kind": "guidance", "title": "SBA: market research and competitive analysis",
     "url": "https://www.sba.gov/counseling/plan-your-business/",
     "text": "Assess demand, reachable customers, location, competing alternatives and prices. Combine existing information with direct customer research such as interviews or surveys. Compare competitors by product and market segment, including strengths, weaknesses and barriers to entry.",
     "geography": "US publisher; general business method, not local law or local-market evidence", "retrieved_at": "2026-11-19"},
    {"id": "B2", "kind": "guidance", "title": "SBA: calculate startup costs",
     "url": "https://www.sba.gov/counseling/plan-your-business/",
     "text": "Identify startup expenses before launch. Separate one-time spending from recurring monthly expenses, and estimate the money needed to operate while the business develops. Use these costs to assess funding needs and potential profit.",
     "geography": "US publisher; general business method, not local law or local-market evidence", "retrieved_at": "2026-11-19"},
    {"id": "B3", "kind": "guidance", "title": "SBA: break-even analysis",
     "url": "https://legacy.sba.gov/business-guide/plan-your-business/calculate-your-startup-costs/break-even-point",
     "text": "For a single product or service, divide fixed costs by selling price minus variable cost per unit to find the sales volume needed to cover costs. Keep all inputs on the same time basis.",
     "geography": "US publisher; general business method, not local law or local-market evidence", "retrieved_at": "2026-11-19"},
]
STOP = set("a an the of and or to for in on with is are this that from by as at your business idea".split())


def terms(text):
    return set(re.findall(r"[\w]+", text.casefold())) - STOP


def safe_url(value):
    try:
        parsed = urlsplit(value or "")
        return value if parsed.scheme in ("https", "http") and parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:
        return None


def geography(target, market):
    if not market:
        return "Location unstated; local relevance unverified"
    if target and terms(target) <= terms(market):
        return f"Source lists {market}; target text matches, coverage unverified"
    return f"Source lists {market}; broader or other-market context, local relevance unverified"


def documents(matches, context, target):
    docs, seen = [], set()
    now = datetime.now(timezone.utc).isoformat()
    def add(title, text, url=None, geo="Location unstated; local relevance unverified", retrieved=now, category="listing"):
        text = str(text)[:1800]
        key = (title, text, url)
        if not text or key in seen:
            return
        seen.add(key)
        docs.append({"id": f"E{len(docs)+1}", "kind": "evidence", "title": str(title)[:300],
                     "text": text, "url": safe_url(url), "geography": geo,
                     "retrieved_at": str(retrieved)[:60], "category": category})
    for product in [*matches, *context["evidence"]]:
        p = product.provenance
        add(product.name, json.dumps({"description": product.description or product.tagline,
            "kind": product.kind, "match_type": product.match_type, "match_reasons": product.score_reasons,
            "signals": product.signals, "publisher": p.source_name, "trust": p.trust.value}, ensure_ascii=False),
            p.url or product.homepage, geography(target, product.market), p.retrieved_at.isoformat(),
            "demand" if product.kind == "demand_signal" else "listing")
    for index, scan in context["research"].items():
        geo = geography(target, matches[index].market)
        for offer in scan.get("offerings", [])[:15]:
            add(f"{scan['company']}: {offer.get('name', 'offering')}",
                json.dumps({k: offer.get(k) for k in ("name", "detail", "prices", "kind")}, ensure_ascii=False),
                next(iter(offer.get("sources", [])), None) or scan.get("website"), geo,
                scan.get("retrieved_at", now), category="website")
        feeds = list(scan.get("reviews", []))
        if context["g2"].get(index):
            feeds.append(context["g2"][index])
        for feed in feeds:
            if feed.get("status") != "ok":
                continue
            for review in feed.get("reviews", [])[:6]:
                # The sampling note travels with every chunk, including negative-first feeds.
                add(f"{scan['company']}: {feed.get('name', 'customer')} review",
                    json.dumps({"opinion": (review.get("title", "") + " " + review.get("text", ""))[:1150],
                        "rating": review.get("rating"), "scale": feed.get("scale"), "review_date": review.get("date"),
                        "sampling": "Selected provider sample; may prioritize negative or most relevant reviews; not representative of all customers.",
                        "provider_note": str(feed.get("note", ""))[:200]}, ensure_ascii=False),
                    review.get("url") or feed.get("url"), geo, scan.get("retrieved_at", now), category="review")
    for index, profile in context["financials"].items():
        company = profile.company
        if not company or company.match_confidence < .8:
            continue
        geo = f"Company registry: {company.jurisdiction.value}; company-wide context, not proof of target-market sales"
        groups = {}
        for fact in profile.financials:
            if fact.rejected or fact.value is None or not math.isfinite(fact.value) or not fact.period_end or not (fact.currency or fact.unit):
                continue
            p = fact.provenance
            add(f"{company.name}: {fact.metric.value}", json.dumps({"metric": fact.metric.value,
                "value": str(fact.value), "currency": fact.currency, "unit": fact.unit,
                "period_start": str(fact.period_start) if fact.period_start else None, "period_end": str(fact.period_end),
                "scope": fact.statement_scope, "as_filed": fact.as_filed,
                "warnings": [issue.message for issue in fact.issues], "source": p.source_name}, ensure_ascii=False),
                p.url, geo, p.retrieved_at.isoformat(), "financial")
            if fact.as_filed and not fact.suspect and fact.currency and fact.period_start and fact.statement_scope:
                key = (fact.period_start, fact.period_end, fact.statement_scope, fact.currency)
                groups.setdefault(key, {}).setdefault(fact.metric.value, []).append(fact)
        for key, group in groups.items():
            revenue = group.get("turnover", [])
            if len(revenue) != 1 or revenue[0].value <= 0:
                continue
            for name in ("net_profit", "operating_profit"):
                profit = group.get(name, [])
                if len(profit) != 1:
                    continue
                percent = Decimal(str(profit[0].value)) / Decimal(str(revenue[0].value)) * 100
                add(f"{company.name}: calculated {name.replace('_', ' ')} margin",
                    json.dumps({"percent": str(percent.quantize(Decimal('.01'))), "currency": key[3],
                        "period_start": str(key[0]), "period_end": str(key[1]), "scope": key[2],
                        "formula": "profit divided by turnover times one hundred", "profit": str(profit[0].value),
                        "turnover": str(revenue[0].value), "profit_source": safe_url(profit[0].provenance.url),
                        "turnover_source": safe_url(revenue[0].provenance.url)}, ensure_ascii=False),
                    profit[0].provenance.url, geo, profit[0].provenance.retrieved_at.isoformat(), "financial")
    return docs


def retrieve(docs, query, limit=22):
    """TF-IDF ranking with category coverage; no embedding model or GPU required."""
    query_terms = terms(query)
    tokens = [terms(row["title"] + " " + row["text"]) for row in docs]
    counts = Counter(word for words in tokens for word in words)
    ranked = sorted(zip(docs, tokens), key=lambda pair: sum(math.log(1 + len(docs) / counts[word])
                    for word in pair[1] & query_terms) / math.sqrt(max(1, len(pair[1]))), reverse=True)
    chosen = []
    # Reserve coverage for fetched prices, filings, complaints and demand signals.
    for category, count in (("financial", 6), ("website", 4), ("review", 4), ("demand", 2), ("listing", 6)):
        chosen.extend([row for row, _ in ranked if row["category"] == category][:count])
    return [{k: v for k, v in row.items() if k != "category"} for row in chosen[:limit]]


def build_packet(matches, context, exhibits):
    idea = context["idea"]
    target = idea.location or "Geographic market not specified"
    notice = (f"Findings are assessed for the stated target market: {target}. Evidence from other or unspecified locations is labeled. "
              "The fetched sources do not establish complete local-market coverage. General business guidance is not local legal or tax advice.")
    if not idea.location:
        notice = "No geographic market was specified. These findings are exploratory and cannot establish local demand, prices or profitability. Add a city or country and run a new search for a geographic assessment."
    notice += " Missing evidence means it was not fetched for this brief, not that it is unavailable elsewhere."
    docs = documents(matches, context, idea.location)
    selected = retrieve(docs, idea.raw + " " + target + " customers demand pricing competitors profit costs complaints risks validation")
    now = datetime.now(timezone.utc).isoformat()
    user = [{"id": row["id"], "kind": "input", "title": row["label"], "text": json.dumps(row),
             "url": None, "geography": target + "; user-supplied assumption, unverified", "retrieved_at": now} for row in exhibits["inputs"]]
    calculations = [{"id": row["id"], "kind": "calculation", "title": row["scenario"] + ": " + row["label"],
        "text": json.dumps({**row, "currency": exhibits["currency"], "paid_unit": exhibits["unit"]}),
        "url": None, "geography": target + "; calculated scenario from user inputs", "retrieved_at": now} for row in exhibits["figures"]]
    coverage = [f"{run.adapter}: {run.results} candidates; {run.reason or ('completed' if run.ok else 'unavailable')}"
                for run in context["sources"]]
    return BriefPacket(idea=idea.raw, target_market=target, geography_notice=notice,
        economic_signal=exhibits["economic_signal"], missing=exhibits["missing"], assumptions=exhibits["assumptions"],
        citations=[*selected, *GUIDANCE, *user, *calculations], retrieval={"method": "TF-IDF with category coverage",
        "candidate_chunks": len(docs), "retrieved_evidence_chunks": len(selected), "guidance_reviewed": "2026-11-19",
        "source_coverage": coverage, "financial_companies_reviewed": len(context["financials"]),
        "websites_reviewed": len(context["research"]),
        "limitations": "Retrieval is relevance selection, not factual verification. Citations link to supplied evidence; inferred opportunities still need testing."})
