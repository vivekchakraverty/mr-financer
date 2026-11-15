"""Render an IdeaReport as markdown.

Report rules, inherited from the funding research: every figure carries its
period and its source, scraped rows are labelled, and an absent number is
replaced by the reason it is absent rather than a blank.
"""

from __future__ import annotations

from datetime import date

from .models import CompetitorProfile, IdeaReport, Metric
from .provenance import Trust
from .validation import MONEY_METRICS

TRUST_BADGE = {
    Trust.STATUTORY: "filed",
    Trust.OFFICIAL: "official",
    Trust.THIRD_PARTY: "third-party",
    Trust.ESTIMATE: "estimate",
    Trust.UNVERIFIED: "scraped",
}


def _money(value: float, currency: str | None) -> str:
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000_000:
        text = f"{v / 1_000_000_000:.2f}bn"
    elif v >= 1_000_000:
        text = f"{v / 1_000_000:.2f}m"
    elif v >= 1_000:
        text = f"{v / 1_000:.1f}k"
    else:
        text = f"{v:,.0f}"
    return f"{sign}{currency + ' ' if currency else ''}{text}"


def _fact_cell(profile: CompetitorProfile, metric: Metric) -> str:
    fact = profile.latest(metric)
    if fact is None or fact.value is None:
        gap = next(
            (g for g in profile.gaps if g.field == metric.value),
            None,
        )
        return f"_{gap.human}_" if gap else "—"
    if metric in MONEY_METRICS:
        shown = _money(fact.value, fact.currency)
    else:
        shown = f"{fact.value:,.0f}"
    badge = TRUST_BADGE.get(fact.provenance.trust, "")
    period = fact.period_end.isoformat() if fact.period_end else "period n/s"
    # A figure that failed a sanity check is shown with a mark, never bare.
    flag = " ⚠sanity" if fact.suspect else ""
    return f"{shown} ({period}, {badge}){flag}"


def render_markdown(report: IdeaReport) -> str:
    idea = report.idea
    lines: list[str] = []

    lines.append(f"# Similar products and services")
    lines.append("")
    lines.append(f"**Idea:** {idea.raw}")
    lines.append("")
    lines.append(f"- Run on {report.generated_at.isoformat()}")
    lines.append(f"- {report.coverage}")
    if idea.keywords:
        lines.append(f"- Keywords used: {', '.join(idea.keywords)}")
    if idea.search_queries:
        lines.append(f"- Search phrases: {'; '.join(idea.search_queries)}")
    if idea.location:
        lines.append(f"- Target market: {idea.location}")
    if idea.jurisdiction_hint.value != "UNKNOWN":
        lines.append(f"- Jurisdiction hint: {idea.jurisdiction_hint.value}")
    lines.append("")

    if not report.competitors:
        lines.append("No sufficiently similar listings were returned. Review the discovery coverage below.")
        lines.append("")
    else:
        lines.append("## Competitor set")
        lines.append("")
        lines.append(
            "| Product or evidence | Company | Turnover | Profit before tax | Employees | Match |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for profile in report.competitors:
            p = profile.product
            name = f"[{p.name}]({p.url})" if p.url else p.name
            if p.provenance.trust is Trust.UNVERIFIED:
                name += " ⚠"
            if profile.company:
                c = profile.company
                company = c.name
                if c.registry_id:
                    company += f" ({c.registry_id})"
                company += f" · {c.match_confidence:.2f}"
            else:
                company = "_not resolved_"
            lines.append(
                "| "
                + " | ".join(
                    [
                        name,
                        company,
                        _fact_cell(profile, Metric.TURNOVER),
                        _fact_cell(profile, Metric.PROFIT_BEFORE_TAX),
                        _fact_cell(profile, Metric.EMPLOYEES),
                        f"{p.match_type} ({p.similarity:.2f})",
                    ]
                )
                + " |"
            )
        lines.append("")
        lines.append("⚠ marks a row discovered by scraping rather than an API.")
        lines.append("")

        lines.append("## Detail")
        lines.append("")
        for profile in report.competitors:
            p = profile.product
            lines.append(f"### {p.name}")
            lines.append("")
            if p.tagline:
                lines.append(f"{p.tagline}")
                lines.append("")
            meta: list[str] = []
            meta.append(f"Evidence type: {p.kind.replace('_', ' ')}")
            meta.extend(p.score_reasons)
            if p.market:
                meta.append(f"Market: {p.market}")
            meta.extend(f"Evidence: {item.citation()}" for item in (p.evidence or [p.provenance]))
            if p.homepage:
                meta.append(f"site: {p.homepage}")
            if p.topics:
                meta.append(f"topics: {', '.join(p.topics[:5])}")
            for key, value in p.signals.items():
                meta.append(f"{key}: {value}")
            if meta:
                lines.append("- " + "\n- ".join(meta))
                lines.append("")

            if profile.company and profile.company.match_basis:
                c = profile.company
                lines.append(
                    f"Matched to **{c.name}** on {c.match_basis}, "
                    f"from {c.candidates_considered} candidate(s)."
                )
                lines.append("")

            reportable = [
                f for f in profile.financials if f.value is not None and not f.rejected
            ]
            if reportable:
                lines.append("| Metric | Value | Period | Checks | Source |")
                lines.append("| --- | --- | --- | --- | --- |")
                for fact in sorted(
                    reportable,
                    key=lambda f: (f.metric.value, f.period_end or date.min),
                ):
                    shown = (
                        _money(fact.value, fact.currency)
                        if fact.metric in MONEY_METRICS
                        else f"{fact.value:,.0f}"
                    )
                    checks = "ok" if not fact.issues else "⚠ see below"
                    lines.append(
                        f"| {fact.metric.value} | {shown} | {fact.period_label} "
                        f"| {checks} | {fact.provenance.citation()} |"
                    )
                lines.append("")

            suspects = profile.suspect_facts
            if suspects:
                lines.append("Figures that passed but look questionable:")
                lines.append("")
                for fact in suspects:
                    for issue in fact.issues:
                        lines.append(f"- **{fact.metric.value}** ({issue.rule}) — {issue.message}")
                lines.append("")

            if profile.funding:
                for event in profile.funding:
                    amount = (
                        _money(event.amount, event.currency)
                        if event.amount is not None
                        else "undisclosed"
                    )
                    when = event.announced_on.isoformat() if event.announced_on else "date n/s"
                    lines.append(
                        f"- {event.round_label or 'Round'}: {amount}, {when} "
                        f"({event.provenance.source_name})"
                    )
                lines.append("")

            if profile.gaps:
                lines.append("What is missing and why:")
                lines.append("")
                for gap in profile.gaps:
                    lines.append(f"- **{gap.field}** — {gap.human}")
                lines.append("")

    lines.append("## Sources used in this run")
    lines.append("")
    lines.append("| Source | Enabled | Result | Note |")
    lines.append("| --- | --- | --- | --- |")
    for run in report.runs:
        lines.append(
            f"| {run.adapter} | {'yes' if run.enabled else 'no'} "
            f"| {run.results if run.ok else 'failed'} | {run.reason or ''} |"
        )
    lines.append("")
    lines.append(
        "Figures marked *filed* come from statutory accounts. Anything else is "
        "reported or scraped and should be verified before you rely on it. "
        "A ⚠sanity mark means the figure passed but failed a plausibility "
        "check; figures that failed outright are not shown as numbers at all, "
        "only as the reason they were withheld."
    )
    return "\n".join(lines)
