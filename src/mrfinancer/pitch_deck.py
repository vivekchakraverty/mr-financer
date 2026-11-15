"""Convert reviewed founder answers into an exact-length pitch outline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from .plan_financials import PlanRequest, build_exhibits

router = APIRouter(prefix="/business-plan/pitch")
GROUPS = json.loads((Path(__file__).parent / "web" / "pitch-questions.json").read_text(encoding="utf-8"))
TOPICS = {topic["id"]: topic for group in GROUPS for topic in group["topics"]}
QUESTIONS = {q["id"]: q for topic in TOPICS.values() for q in topic["questions"]}
BASE = [
    ("cover",), ("problem", "solution"), ("market", "competition"),
    ("traction", "model"), ("team", "finance"), ("ask",),
]
ORDER = (
    "cover", "problem", "why_now", "solution", "demo", "evidence", "market", "competition",
    "advantage", "traction", "model", "economics", "channels", "partnerships", "operations",
    "team", "roadmap", "vision", "finance", "sensitivity", "risks", "ask", "funding_use", "close", "sources",
)
SCENES = {
    "cover": "a compelling wide editorial scene that immediately communicates the product category",
    "problem": "a realistic customer situation that conveys friction before the solution exists",
    "solution": "a conceptual illustration of how the service helps the customer complete their task",
    "why_now": "an editorial photograph suggesting a shift in the market or in customer behavior",
    "channels": "a conceptual scene of a buyer encountering a product in a relevant sales channel",
    "operations": "an editorial scene showing the operational setting and resources involved",
    "advantage": "a restrained metaphor for a distinctive capability",
    "risks": "a subdued conceptual scene about operational resilience",
    "vision": "an optimistic but credible future customer environment",
    "ask": "an understated closing image with negative space for the request",
    "close": "an understated closing image that reinforces the business idea",
}
ASSET_GUIDANCE = {
    "data": "Use an editable chart or table built from sourced numbers. Keep actuals, forecasts and estimates distinct.",
    "real": "Use authentic screenshots, demo footage, approved logos, team photos or source documents, as relevant.",
}
Audience = Literal["investor", "lender", "grant", "customer or partner", "internal review"]
EvidenceType = Literal["founder-reported", "documented", "estimate", "target", "unknown"]
MAX_ANSWER = 1000


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Answer(Input):
    text: str = Field(min_length=1, max_length=MAX_ANSWER)
    status: EvidenceType = "founder-reported"
    source: HttpUrl | None = None

    @model_validator(mode="after")
    def validate_source(self):
        if self.status == "documented" and self.source is None:
            raise ValueError("Documented answers need a source link.")
        if self.source and (self.source.username is not None or self.source.password is not None):
            raise ValueError("Use a shareable source without credentials.")
        return self


class PitchRequest(Input):
    slide_count: int = Field(ge=6, le=len(TOPICS))
    audience: Audience = "investor"
    format: Literal["live pitch", "leave-behind", "both"] = "both"
    minutes: int | None = Field(default=None, ge=1, le=90)
    visual_style: Literal["editorial photo", "clean illustration", "studio product concept"] = "editorial photo"
    visual_subject: str = Field("", max_length=200)
    answers: dict[str, Answer] = Field(default_factory=dict, max_length=len(QUESTIONS))
    plan: PlanRequest | None = None

    @model_validator(mode="after")
    def check_answers(self):
        for key, answer in self.answers.items():
            question = QUESTIONS.get(key)
            if question is None:
                raise ValueError("The deck contains an unrecognized question.")
            if question["type"] == "url" and answer.status != "unknown":
                url = HttpUrl(answer.text)
                if url.username is not None or url.password is not None:
                    raise ValueError("Use a shareable link without credentials.")
        return self


def lineup(count: int, answered=(), audience: Audience = "investor"):
    """Allocate extra slides to split dense topics, then add evidence modules."""
    slots = list(BASE)
    order = list(ORDER)
    if audience in ("lender", "grant"):
        order.remove("finance")
        order.insert(order.index("traction") + 1, "finance")
    for index in range(len(slots) - 1, -1, -1):
        if len(slots) >= count:
            break
        group = slots[index]
        if len(group) > 1:
            slots[index:index + 1] = [(name,) for name in group]
    covered = {topic for group in slots for topic in group}
    answered = set(answered)
    extra = sorted((topic for topic in TOPICS.values() if topic["id"] not in covered),
                   key=lambda topic: (topic["id"] not in answered, topic["priority"]))
    slots.extend((topic["id"],) for topic in extra[:count - len(slots)])
    return sorted(slots, key=lambda group: min(order.index(topic) for topic in group))


def plan_content(plan):
    if plan is None:
        return {}, []
    exhibits = build_exhibits(plan)
    base = exhibits["scenarios"]["base"]
    c = exhibits["currency"]
    entries = []
    for label, value in (("Year-one revenue", base["annual_revenue"]), ("Lowest cash balance", base["lowest_cash"]),
                         ("Funding gap", base["funding_gap"])):
        if value is not None:
            entries.append(evidence(label, f"{c} {value}", "Base forecast"))
    if base["annual_revenue"] is not None:
        for label, scenario in exhibits["scenarios"].items():
            if label != "base" and scenario["annual_revenue"] is not None:
                entries.append(evidence(f"{label.replace('_', ' ').capitalize()} revenue",
                                        f"{c} {scenario['annual_revenue']}", "Scenario forecast"))
    unit = []
    prices = []
    for row in base["unit_economics"]:
        if row["price"] is not None:
            prices.append(evidence(row["offer"], f"{c} {row['price']} per selling unit", "Plan input or estimate"))
        if row["break_even_units"] is not None:
            unit.append(evidence(f"{row['offer']} break-even", f"{row['break_even_units']} {row['unit']}",
                                 "Base forecast" + (" · above capacity" if row["break_even_exceeds_capacity"] else "")))
        if row["contribution_margin_percent"] is not None:
            unit.append(evidence(f"{row['offer']} contribution margin", f"{row['contribution_margin_percent']}%",
                                 "Base forecast"))
    filings = []
    for fact in exhibits["competitor_figures"]:
        value = " ".join(str(v) for v in (fact["currency"], fact["amount"], fact["unit"]) if v)
        period = " to ".join(v for v in (fact["period_start"], fact["period_end"]) if v)
        filings.append(evidence(f'{fact["id"]} · {fact["company"]} · {fact["metric"].replace("_", " ")}',
                                value, f'{fact["status"]} · {fact["scope"]} · {period}', fact["url"],
                                " · ".join(v for v in (fact["source"], fact["locator"]) if v)))
    for margin in exhibits["competitor_margins"]:
        filings.append(evidence(f'{margin["label"]} margin · {", ".join(margin["source_ids"])}',
                                f'{margin["percent"]}%', "Calculated from the filing figures above"))
    excerpts = {"finance": entries, "economics": unit, "competition": filings, "model": prices}
    if plan.recent:
        excerpts["traction"] = [evidence("Recent results", plan.recent, "Founder-reported")]
    if plan.channels:
        excerpts["channels"] = [evidence("Initial channels", plan.channels, "Founder-reported")]
    signals = [f"Business plan input unavailable: {item}" for item in exhibits["missing"]]
    return excerpts, signals


def evidence(label, text, status, source=None, reference=None):
    return dict(label=label, text=text, status=status, source=source, reference=reference)


def prompt(request, group):
    if any(TOPICS[topic]["visual"] == "real" for topic in group):
        return None, ASSET_GUIDANCE["real"]
    if any(TOPICS[topic]["visual"] == "data" for topic in group):
        return None, ASSET_GUIDANCE["data"]
    topic = group[0]
    scene = SCENES.get(topic, "a restrained editorial scene appropriate to this company")
    subject = request.visual_subject or "the customer setting for the business"
    return (
        f"{request.visual_style.capitalize()}, 16:9 wide composition, {scene}. "
        f"Public visual brief: {subject}. Keep a consistent restrained palette and a clean side for slide text. "
        "Exclude words, numbers, graphs, logos, watermarks, recognizable real people and simulated product screenshots.",
        "Use as illustrative artwork and label it where a viewer could mistake it for evidence.",
    )


@router.get("/questions")
def questions():
    return {"groups": GROUPS, "min_slides": 6, "max_slides": len(TOPICS)}


@router.get("/questionnaire")
def downloadable_questionnaire():
    path = Path(__file__).parent / "web" / "Mr_Financer_Pitch_Deck_Questionnaire.md"
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=path.name)


@router.post("/outline")
def outline(request: PitchRequest):
    excerpts, missing = plan_content(request.plan)
    answered = {topic for topic in excerpts if excerpts[topic]}
    answered.update(key.partition(".")[0] for key, value in request.answers.items() if value.status != "unknown")
    selected = lineup(request.slide_count, answered, request.audience)
    slides = []
    for number, group in enumerate(selected, 1):
        rows, gaps = [], []
        for topic_id in group:
            topic = TOPICS[topic_id]
            for question in topic["questions"]:
                answer = request.answers.get(question["id"])
                if answer and answer.status != "unknown":
                    rows.append(evidence(question["label"], answer.text, answer.status,
                                         str(answer.source) if answer.source else None))
                elif question["core"] or answer is not None:
                    gaps.append(question["label"])
            rows.extend(excerpts.get(topic_id, []))
        if not rows and not gaps:
            gaps.append("Add at least one answer or a source for this slide.")
        artwork, guidance = prompt(request, group)
        slides.append(dict(number=number, title=" and ".join(TOPICS[t]["title"] for t in group),
                           topics=group, items=rows, missing=gaps, image_prompt=artwork, visual_guidance=guidance))
    return {"slide_count": len(slides), "audience": request.audience, "format": request.format,
            "minutes": request.minutes, "slides": slides, "financial_gaps": missing}

