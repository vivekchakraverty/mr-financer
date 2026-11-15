"""Find workbook-listed angel investors by their recorded sector coverage."""

from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .config import get_settings
from .idea import normalize_idea

router = APIRouter(prefix="/investors")
DIRECTORY = Path(__file__).with_name("data") / "angel-investors.json.gz"
PAGE_SIZE = 24

# The labels match the workbook. The terms suggest sectors for a founder to review.
SECTOR_TERMS = {
    "Enterprise & AI": (
        "ai", "artificial intelligence", "machine learning", "llm", "generative ai", "large language model",
        "enterprise software", "enterprise automation", "workflow automation", "erp", "crm", "b2b saas",
        "business intelligence", "computer vision", "predictive analytics", "robotic process automation",
    ),
    "Healthcare & Bio": (
        "healthcare", "health tech", "healthtech", "biotech", "biotechnology", "medical", "clinical",
        "diagnostics", "patient care", "hospital", "hospitals", "pharma", "pharmaceutical", "telemedicine",
        "digital health", "mental health", "therapeutics", "dental", "genomics", "medical device",
    ),
    "Media & Gaming": (
        "game", "games", "gaming", "video game", "video games", "esports", "animation", "media",
        "entertainment", "film", "streaming", "podcast", "publishing", "music", "creator economy",
        "storytelling", "virtual production", "game development", "content creation", "game design", "content marketing", "digital marketing",
    ),
    "Fintech & Finance": (
        "fintech", "finance", "banking", "bank account", "payments", "payment", "lending", "loans", "loan",
        "credit score", "insurance", "insurtech", "accounting", "invoicing", "payroll", "investment",
        "wealth management", "financial planning", "money transfer", "personal finance", "tax filing",
    ),
    "Consumer & Retail": (
        "consumer", "retail", "ecommerce", "e commerce", "online store", "shopping", "direct to consumer",
        "d2c", "fashion", "beauty", "cosmetics", "skincare", "food", "beverage", "restaurant", "grocery",
        "laundry", "meal delivery", "household", "travel booking", "fitness", "consumer goods", "clothing", "apparel",
    ),
    "CleanTech & Energy": (
        "cleantech", "clean tech", "renewable", "solar", "wind energy", "clean energy", "climate tech",
        "carbon capture", "carbon credits", "recycling", "battery storage", "energy storage", "green hydrogen",
        "sustainability", "electric grid", "waste management", "decarbonization", "energy efficiency",
    ),
    "Industrial & Mobility": (
        "industrial", "manufacturing", "factory", "logistics", "warehouse", "warehousing", "supply chain",
        "mobility", "transportation", "vehicle", "vehicles", "fleet", "automotive", "robotics", "hardware",
        "construction equipment", "freight", "electric vehicle", "last mile", "aerospace", "industrial automation",
    ),
    "Real Estate & PropTech": (
        "real estate", "proptech", "prop tech", "property", "property management", "realty", "housing",
        "rental homes", "commercial real estate", "real estate brokerage", "rental management",
        "property listing", "smart building", "construction tech", "tenant screening",
    ),
    "EdTech & Education": (
        "edtech", "ed tech", "education", "educational", "learning platform", "tutoring", "tutor", "classroom",
        "school", "schools", "teacher", "teachers", "literacy", "e learning", "online courses", "learning app",
        "student", "students", "curriculum", "learning management", "training platform",
    ),
    "Tech & Software": (
        "saas", "software", "cybersecurity", "cyber security", "developer tools", "api", "cloud computing",
        "data platform", "data infrastructure", "mobile app", "web app", "digital platform", "technology",
        "devops", "computer software", "database", "network infrastructure", "software development",
    ),
}
GENERAL = "General & Unspecified"
LABELS = tuple(SECTOR_TERMS) + (GENERAL,)
SearchText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class InvestorSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idea: SearchText = ""
    sectors: list[str] | None = Field(default=None, max_length=len(LABELS))
    query: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    page: int = Field(default=1, ge=1, le=1000)


def normalized(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.casefold()).strip()


def suggested_sectors(idea: str) -> list[dict]:
    padded = f" {normalized(idea)} "
    suggestions = []
    for sector, terms in SECTOR_TERMS.items():
        matched = [term for term in terms if f" {term} " in padded]
        if matched:
            weight = sum(2 if " " in term else 1 for term in matched)
            suggestions.append((weight, {"sector": sector, "terms": matched}))
    # Treat generic technology wording as a fallback when the idea has clearer
    # industry signals, unless the idea independently names a specific tech area.
    generic = {"software", "technology", "saas", "api", "mobile app", "web app", "digital platform"}
    if any(item["sector"] != "Tech & Software" for _, item in suggestions):
        suggestions = [(weight, item) for weight, item in suggestions
                       if item["sector"] != "Tech & Software" or not generic.issuperset(item["terms"])]
    suggestions.sort(key=lambda pair: -pair[0])
    return [item for _, item in suggestions]


@lru_cache(maxsize=1)
def load_directory():
    try:
        path = get_settings().investor_directory or DIRECTORY
        with gzip.open(path, mode="rt", encoding="utf-8") as directory:
            payload = json.load(directory)
    except FileNotFoundError:
        raise HTTPException(503, "Import an investor workbook to enable this tab. See the setup guide.") from None
    except (OSError, ValueError):
        raise HTTPException(503, "The investor directory could not be read. Rebuild it from the workbook.") from None
    counts = Counter(sector for row in payload["records"] for sector in row["sectors"])
    return payload, counts


def directory_response(response):
    response.headers["Cache-Control"] = "no-store"
    payload, counts = load_directory()
    return payload, counts


@router.get("/sectors")
def sectors(response: Response):
    payload, counts = directory_response(response)
    return {"workbook": payload["workbook"], "total_records": len(payload["records"]),
            "sectors": [{"name": name, "count": counts[name]} for name in LABELS]}


@router.post("/search")
def search(body: InvestorSearch, response: Response):
    payload, _ = directory_response(response)
    suggestions = suggested_sectors(body.idea)
    selected = list(dict.fromkeys(body.sectors)) if body.sectors is not None else [
        suggestion["sector"] for suggestion in suggestions
    ]
    if any(sector not in LABELS for sector in selected):
        raise HTTPException(422, "Choose sectors from the workbook list.")
    matched = []
    query = normalized(body.query)
    idea_terms = normalize_idea(body.idea).keywords if body.idea else []
    if selected:
        for row in payload["records"]:
            matching_sectors = [sector for sector in selected if sector in row["sectors"]]
            if not matching_sectors:
                continue
            contact = normalized(f'{row.get("contact", "")} {row.get("firm", "")}')
            if query and query not in contact:
                continue
            profile = f' {normalized(" ".join(row.get(key, "") for key in ("description", "notes", "notes_2")))} '
            word_matches = sum(f" {normalized(term)} " in profile for term in idea_terms)
            key = (-len(matching_sectors), -word_matches, contact, row["sheet"], row["row"])
            matched.append((key, row, matching_sectors))
    matched.sort(key=lambda item: item[0])
    start = (body.page - 1) * PAGE_SIZE
    return {"workbook": payload["workbook"], "selected_sectors": selected, "suggestions": suggestions,
            "page": body.page, "page_size": PAGE_SIZE, "total": len(matched),
            "rows": [dict(row, matching_sectors=matching_sectors)
                     for _, row, matching_sectors in matched[start:start + PAGE_SIZE]]}
