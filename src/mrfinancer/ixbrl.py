"""Minimal iXBRL fact extractor for UK filed accounts.

Companies House serves small-company accounts as inline XBRL (XHTML with
`ix:nonFraction` elements). This pulls out the handful of concepts a founder
actually wants to see about a competitor, with the period each one covers.

Why hand-rolled rather than a full XBRL library: the full stack (taxonomy
resolution, dimensional contexts, calculation linkbases) is heavy, and we need
five numbers with dates, not a complete financial model. The trade-off is
recorded here honestly -- this reads presented facts only, ignores dimensions,
and makes no attempt at taxonomy validation.

Two correctness details that are easy to get wrong and are handled:

* `scale` -- a value of "3" means the displayed figure is in thousands, so the
  real number is text x 10^3.
* `sign` -- `sign="-"` means negate; the text itself has no minus sign.

Both are per the Inline XBRL 1.1 spec. Getting either wrong silently reports a
number off by 1000x or of the wrong sign, which is worse than reporting nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from bs4 import BeautifulSoup

from .models import Metric

# Concept local-names by metric, lowercased. UK GAAP/FRS taxonomies have used
# several names for the same line over the years, hence the aliases.
CONCEPTS: dict[Metric, set[str]] = {
    Metric.TURNOVER: {
        "turnoverrevenue",
        "turnover",
        "revenue",
        "turnovergrossoperatingrevenue",
        "revenuefromcontractswithcustomers",
    },
    Metric.PROFIT_BEFORE_TAX: {
        "profitlossonordinaryactivitiesbeforetax",
        "profitlossbeforetax",
    },
    Metric.OPERATING_PROFIT: {"operatingprofitloss"},
    Metric.NET_ASSETS: {
        "netassetsliabilities",
        "netassetsliabilitiesincludingpensionassetliability",
    },
    Metric.CASH: {"cashbankonhand", "cashbankinhand", "cashcashequivalents"},
    Metric.EMPLOYEES: {
        "averagenumberemployeesduringperiod",
        "averagenumberofemployees",
        "numberofemployeestotal",
    },
}

_LOOKUP: dict[str, Metric] = {
    name: metric for metric, names in CONCEPTS.items() for name in names
}

_NUM_CLEAN = re.compile(r"[^0-9.\-]")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


@dataclass(frozen=True)
class Fact:
    metric: Metric
    value: float
    currency: str | None
    period_start: date | None
    period_end: date | None
    concept: str
    # Presentation details are kept, not discarded, so validation.py can
    # question them. `scale` and `decimals` are exactly where a silent 1000x
    # error comes from, so the evidence has to survive the parse.
    scale: int | None = None
    decimals: int | None = None
    text: str = ""


def _local_name(qname: str) -> str:
    return qname.split(":")[-1].strip().lower()


def _attr(node, name: str) -> str | None:
    """Case-insensitive attribute read.

    Necessary because the two parsers disagree: BeautifulSoup's XML parser
    preserves `contextRef` as written, while the HTML fallback lowercases it to
    `contextref`. Reading only one spelling silently loses every period and
    unit, which would strip the dates off all the facts.
    """
    value = node.get(name)
    if value is not None:
        return value
    lowered = name.lower()
    for key, val in (node.attrs or {}).items():
        if key.lower() == lowered:
            return val
    return None


def _parse_date(text: str | None) -> date | None:
    if not text:
        return None
    m = _ISO_DATE.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _parse_contexts(soup: BeautifulSoup) -> dict[str, tuple[date | None, date | None]]:
    """context id -> (start, end). An instant context yields (None, instant)."""
    contexts: dict[str, tuple[date | None, date | None]] = {}
    for ctx in soup.find_all(lambda t: _local_name(t.name) == "context"):
        cid = _attr(ctx, "id")
        if not cid:
            continue
        start = end = None
        for child in ctx.find_all(True):
            local = _local_name(child.name)
            if local == "startdate":
                start = _parse_date(child.get_text())
            elif local == "enddate":
                end = _parse_date(child.get_text())
            elif local == "instant":
                end = _parse_date(child.get_text())
        contexts[cid] = (start, end)
    return contexts


def _parse_units(soup: BeautifulSoup) -> dict[str, str]:
    """unit id -> currency code (or 'pure'/'shares' for non-monetary)."""
    units: dict[str, str] = {}
    for unit in soup.find_all(lambda t: _local_name(t.name) == "unit"):
        uid = _attr(unit, "id")
        if not uid:
            continue
        measures = [
            m.get_text(strip=True)
            for m in unit.find_all(lambda t: _local_name(t.name) == "measure")
        ]
        if not measures:
            continue
        code = _local_name(measures[0]).upper()
        units[uid] = code
    return units


def _as_int(value: str | None) -> int | None:
    """iXBRL allows decimals="INF"; anything non-integer becomes None."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _to_number(text: str, scale: str | None, sign: str | None) -> float | None:
    cleaned = _NUM_CLEAN.sub("", text.replace(",", "").strip())
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if scale:
        try:
            value *= 10 ** int(scale)
        except ValueError:
            pass
    if sign == "-":
        value = -value
    return value


def extract_facts(xhtml: str) -> list[Fact]:
    """Pull recognized facts out of an iXBRL document.

    Returns every matching fact, including prior-year comparatives; the caller
    decides which period to show.
    """
    soup = BeautifulSoup(xhtml, "lxml-xml")
    # Some filings come through without namespace prefixes surviving; fall back
    # to the lenient HTML parser so we still see the ix: elements.
    if not soup.find(lambda t: _local_name(t.name) == "nonfraction"):
        soup = BeautifulSoup(xhtml, "lxml")

    contexts = _parse_contexts(soup)
    units = _parse_units(soup)

    facts: list[Fact] = []
    for node in soup.find_all(lambda t: _local_name(t.name) == "nonfraction"):
        concept_qname = _attr(node, "name") or ""
        metric = _LOOKUP.get(_local_name(concept_qname))
        if metric is None:
            continue

        raw_text = node.get_text(strip=True)
        raw_scale = _attr(node, "scale")
        raw_decimals = _attr(node, "decimals")
        value = _to_number(raw_text, raw_scale, _attr(node, "sign"))
        if value is None:
            continue

        start, end = contexts.get(_attr(node, "contextRef") or "", (None, None))
        unit = units.get(_attr(node, "unitRef") or "")
        currency = unit if unit and len(unit) == 3 else None

        facts.append(
            Fact(
                metric=metric,
                value=value,
                currency=currency,
                period_start=start,
                period_end=end,
                concept=concept_qname,
                scale=_as_int(raw_scale),
                decimals=_as_int(raw_decimals),
                text=raw_text,
            )
        )
    return facts


def latest_per_metric(facts: list[Fact]) -> dict[Metric, Fact]:
    """Keep the most recent fact for each metric.

    Accounts always restate the prior year alongside the current one, so taking
    whichever appears first in the document is a common and silent bug.
    """
    best: dict[Metric, Fact] = {}
    for fact in facts:
        current = best.get(fact.metric)
        if current is None:
            best[fact.metric] = fact
            continue
        a = fact.period_end or date.min
        b = current.period_end or date.min
        if a > b:
            best[fact.metric] = fact
    return best
