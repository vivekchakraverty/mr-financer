"""HTTP surface, so the other three modules (documentation, investor outreach,
credit suggestions) and any UI can call this one over the wire.

    uvicorn mrfinancer.api:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import __version__
from .adapters import build_discovery_adapters, build_financial_adapters
from .config import get_settings
from .idea import normalize_idea
from .models import Idea, IdeaReport
from .pipeline import analyze_idea
from .report import render_markdown
from .workspace import router as workspace_router
from .business_plan import router as business_plan_router
from .pitch_deck import router as pitch_router
from .pitch_art import router as pitch_art_router
from .pitch_export import router as pitch_export_router
from .loans import router as loan_router
from .investors import router as investor_router
from .incorporation import router as incorporation_router
from .learn import router as learn_router
from .viability import router as viability_router

app = FastAPI(
    title="Mr. Financer - Product Idea",
    version=__version__,
    description="Finds similar products and their freely available financials.",
)


app.include_router(workspace_router)
app.include_router(business_plan_router)
app.include_router(pitch_router)
app.include_router(pitch_art_router)
app.include_router(pitch_export_router)
app.include_router(loan_router)
app.include_router(investor_router)
app.include_router(incorporation_router)
app.include_router(learn_router)
app.include_router(viability_router)


class AnalyzeRequest(BaseModel):
    idea: str = Field(min_length=3, max_length=2000)
    max_competitors: int | None = Field(default=None, ge=1, le=100)
    location: str | None = Field(default=None, max_length=120)


class SourceStatus(BaseModel):
    id: str
    kind: str
    name: str
    is_scraper: bool
    ready: bool
    reason: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/sources", response_model=list[SourceStatus])
def sources() -> list[SourceStatus]:
    settings = get_settings()
    out: list[SourceStatus] = []
    for kind, adapters in (
        ("discovery", build_discovery_adapters(settings)),
        ("financial", build_financial_adapters(settings)),
    ):
        for adapter in adapters:
            gap = adapter.available()
            out.append(
                SourceStatus(
                    id=adapter.id,
                    kind=kind,
                    name=adapter.name,
                    is_scraper=adapter.is_scraper,
                    ready=gap is None,
                    reason=gap.human if gap else None,
                )
            )
    return out


@app.post("/idea/normalize", response_model=Idea)
def normalize(body: AnalyzeRequest) -> Idea:
    try:
        return normalize_idea(body.idea, body.location)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/idea/analyze", response_model=IdeaReport)
async def analyze(body: AnalyzeRequest) -> IdeaReport:
    settings = get_settings()
    if body.max_competitors:
        settings = settings.model_copy(update={"max_competitors": body.max_competitors})
    try:
        return await analyze_idea(body.idea, settings, location=body.location)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/idea/analyze.md")
async def analyze_markdown(body: AnalyzeRequest) -> dict[str, str]:
    settings = get_settings()
    if body.max_competitors:
        settings = settings.model_copy(update={"max_competitors": body.max_competitors})
    try:
        report = await analyze_idea(body.idea, settings, location=body.location)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"markdown": render_markdown(report)}
