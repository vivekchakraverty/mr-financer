"""Command line interface.

    mrfinancer analyze "an app that tracks freelancer invoices and chases late payers"
    mrfinancer doctor
    mrfinancer sources
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import fields
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .adapters import build_discovery_adapters, build_financial_adapters
from .config import get_settings
from .foursquare_import import import_foursquare, refresh_foursquare
from .idea import normalize_idea
from .net import Fetcher, FetchError, ResponseCache
from .pipeline import analyze_idea
from .report import render_markdown
from .validation import (
    DEFAULT_PROFILE,
    PROFILE_SUMMARY,
    PROFILES,
    THRESHOLD_EFFECTS,
    Thresholds,
)

app = typer.Typer(add_completion=False, help="Mr. Financer - product idea research")
console = Console()


@app.command()
def analyze(
    idea: str = typer.Argument(..., help="The product or service idea, in plain words"),
    out: Path | None = typer.Option(None, "--out", "-o", help="Write markdown here"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of markdown"),
    limit: int | None = typer.Option(None, "--limit", min=1, max=100, help="Max competitors"),
    location: str | None = typer.Option(None, "--location", help="City or locality for a physical service"),
) -> None:
    """Find similar products and whatever financials are freely available."""
    settings = get_settings()
    if limit:
        settings = settings.model_copy(update={"max_competitors": limit})

    report = asyncio.run(analyze_idea(idea, settings, location=location))

    if as_json:
        payload = report.model_dump(mode="json")
        text = json.dumps(payload, indent=2)
    else:
        text = render_markdown(report)

    if out:
        out.write_text(text, encoding="utf-8")
        console.print(f"[green]Written to {out}[/green]")
        console.print(report.coverage)
    else:
        console.print(text, markup=False, highlight=False)


@app.command("import-places")
def import_places(
    source: Path = typer.Argument(..., exists=True, dir_okay=False),
    destination: Path = typer.Argument(..., dir_okay=False),
) -> None:
    """Build a searchable snapshot from a Foursquare Places Portal export."""
    try:
        count = import_foursquare(source, destination)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Imported {count:,} places into {destination}", markup=False)


@app.command("refresh-places")
def refresh_places(
    destination: Path = typer.Argument(..., dir_okay=False),
    country: list[str] = typer.Option(..., "--country", help="Two-letter country code. Repeat for multiple markets."),
) -> None:
    """Refresh a local snapshot from the authenticated Foursquare catalog."""
    settings = get_settings()
    uri, warehouse, table, token = (
        settings.foursquare_catalog_uri, settings.foursquare_catalog_warehouse,
        settings.foursquare_catalog_table, settings.foursquare_catalog_token,
    )
    if not (uri and warehouse and table and token):
        raise typer.BadParameter("Set the four MRF_FOURSQUARE_CATALOG_* values")
    try:
        count = refresh_foursquare(destination, uri=uri, warehouse=warehouse,
                                   table=table, token=token, countries=country)
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    except Exception as exc:
        console.print(f"Foursquare refresh failed ({type(exc).__name__}). Check the catalog settings and token.")
        raise typer.Exit(1) from None
    console.print(f"Imported {count:,} places into {destination}", markup=False)


@app.command()
def keywords(idea: str = typer.Argument(...)) -> None:
    """Show how an idea is turned into search terms. No network."""
    parsed = normalize_idea(idea)
    table = Table(title="Idea normalization")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("keywords", ", ".join(parsed.keywords))
    table.add_row("categories", ", ".join(parsed.category_terms) or "—")
    table.add_row("queries", "\n".join(parsed.search_queries))
    table.add_row("jurisdiction hint", parsed.jurisdiction_hint.value)
    console.print(table)


@app.command()
def sources() -> None:
    """List every source and whether it is usable right now. No network."""
    settings = get_settings()
    table = Table(title="Sources")
    table.add_column("id")
    table.add_column("kind")
    table.add_column("type")
    table.add_column("status")

    for adapter in build_discovery_adapters(settings):
        gap = adapter.available()
        table.add_row(
            adapter.id,
            "discovery",
            "scraper" if adapter.is_scraper else "api",
            "[green]ready[/green]" if gap is None else f"[yellow]{gap.human}[/yellow]",
        )
    for adapter in build_financial_adapters(settings):
        gap = adapter.available()
        table.add_row(
            adapter.id,
            "financial",
            "scraper" if adapter.is_scraper else "api",
            "[green]ready[/green]" if gap is None else f"[yellow]{gap.human}[/yellow]",
        )
    console.print(table)
    console.print(
        "\nScrapers are opt-in. Check each site's terms yourself before "
        "enabling one in a build you ship."
    )


@app.command()
def settings() -> None:
    """Show every validation setting, where it came from and what it changes.

    The point of this command: you should never have to read the source to find
    out why a figure was withheld, or which of a dozen numbers to change.
    """
    cfg = get_settings()
    overrides = cfg.validation_overrides()
    resolved = cfg.thresholds()
    profile_name = cfg.validation_profile.lower().strip()
    known = profile_name in PROFILES

    console.print(
        f"\n[bold]Profile in force:[/bold] {profile_name if known else profile_name + ' (unknown)'}"
    )
    if not known:
        console.print(
            f"[yellow]Not a known profile, so '{DEFAULT_PROFILE}' is being used. "
            f"Valid values: {', '.join(PROFILES)}.[/yellow]"
        )
    console.print(PROFILE_SUMMARY.get(profile_name if known else DEFAULT_PROFILE, ""))

    table = Table(title="Resolved validation settings")
    table.add_column("setting")
    table.add_column("value", justify="right")
    table.add_column("from")
    table.add_column("what it changes")
    for field in fields(Thresholds):
        value = getattr(resolved, field.name)
        shown = "∞" if value == float("inf") else str(value)
        origin = (
            "[magenta]override[/magenta]" if field.name in overrides else "profile"
        )
        table.add_row(
            field.name, shown, origin, THRESHOLD_EFFECTS.get(field.name, "")
        )
    console.print(table)

    compare = Table(title="Available profiles")
    compare.add_column("profile")
    compare.add_column("withholds warnings?")
    compare.add_column("needs currency?")
    compare.add_column("summary")
    for name, preset in PROFILES.items():
        marker = " [green]<- current[/green]" if name == (profile_name if known else DEFAULT_PROFILE) else ""
        compare.add_row(
            name + marker,
            "yes" if preset.escalate_warnings else "no",
            "yes" if preset.require_currency else "no",
            PROFILE_SUMMARY.get(name, ""),
        )
    console.print(compare)
    console.print(
        "\nChange profile with [bold]MRF_VALIDATION_PROFILE=strict[/bold], or "
        "override one value with e.g. [bold]MRF_MAX_EMPLOYEES=250000[/bold]. "
        "Run [bold]mrfinancer init[/bold] to write a .env interactively.\n"
    )


@app.command()
def init(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing .env"),
) -> None:
    """Write a .env interactively. Everything is optional; press Enter to skip."""
    target = Path(".env")
    if target.exists() and not force:
        if not typer.confirm(f"{target} exists. Overwrite it?", default=False):
            console.print("[yellow]Left alone. Nothing written.[/yellow]")
            raise typer.Exit()

    console.print(
        "\n[bold]Mr. Financer setup[/bold]\n"
        "Press Enter to skip anything. The only key really worth having is "
        "Companies House: it is free and it is the one source that returns "
        "audited numbers.\n"
    )

    lines: list[str] = ["# Written by `mrfinancer init`."]

    console.print("[bold]1. Validation profile[/bold]")
    for name, summary in PROFILE_SUMMARY.items():
        console.print(f"   {name}: {summary}")
    profile = typer.prompt("   profile", default=DEFAULT_PROFILE)
    if profile.lower().strip() not in PROFILES:
        console.print(
            f"[yellow]   '{profile}' is not a known profile; using "
            f"{DEFAULT_PROFILE}.[/yellow]"
        )
        profile = DEFAULT_PROFILE
    lines.append(f"MRF_VALIDATION_PROFILE={profile.lower().strip()}")

    console.print(
        "\n[bold]2. Companies House[/bold] (free: "
        "developer.company-information.service.gov.uk)"
    )
    ch = typer.prompt("   API key", default="", show_default=False)
    lines.append(f"MRF_COMPANIES_HOUSE_KEY={ch.strip()}")

    console.print(
        "\n[bold]3. Product Hunt[/bold] (free: create an app, use the "
        "client-only token)"
    )
    ph = typer.prompt("   token", default="", show_default=False, hide_input=True)
    lines.append(f"MRF_PRODUCTHUNT_TOKEN={ph.strip()}")
    lines.append(f"MRF_PRODUCTHUNT_ENABLED={str(bool(ph.strip())).lower()}")

    console.print("\n[bold]4. Search API[/bold] (none | brave | serper | tavily)")
    provider = typer.prompt("   provider", default="none").lower().strip()
    if provider not in {"none", "brave", "serper", "tavily"}:
        console.print(f"[yellow]   '{provider}' unknown; using none.[/yellow]")
        provider = "none"
    lines.append(f"MRF_SEARCH_PROVIDER={provider}")
    if provider != "none":
        key = typer.prompt("   API key", default="", show_default=False)
        lines.append(f"MRF_SEARCH_API_KEY={key.strip()}")
    else:
        lines.append("MRF_SEARCH_API_KEY=")

    console.print(
        "\n[bold]5. Scrapers[/bold] — off by default. Enabling one may breach a "
        "site's terms even where robots.txt allows the path; that call is yours."
    )
    alt = typer.confirm("   Enable AlternativeTo scraping?", default=False)
    mca = typer.confirm("   Enable MCA manual-lookup links?", default=False)
    lines.append(f"MRF_ENABLE_SCRAPER_ALTERNATIVETO={str(alt).lower()}")
    lines.append(f"MRF_ENABLE_SCRAPER_MCA={str(mca).lower()}")
    lines.append("MRF_RESPECT_ROBOTS=true")

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"\n[green]Written {target.resolve()}[/green]")
    console.print(
        "Next: [bold]mrfinancer settings[/bold] to see what that resolved to, "
        "then [bold]mrfinancer doctor[/bold] to test the keys.\n"
    )


@app.command()
def doctor() -> None:
    """Make one real call to every configured source.

    Run this on the machine that will host the app: the whole point is to prove
    connectivity and credentials where the code will actually run.
    """

    async def run() -> int:
        settings = get_settings()
        cache = ResponseCache(settings.cache_path, 0)  # no caching for a health check
        fetcher = Fetcher(settings, cache=cache)
        failures = 0

        table = Table(title="Live source check")
        table.add_column("source")
        table.add_column("result")

        candidates = [
            a
            for a in (*build_discovery_adapters(settings), *build_financial_adapters(settings))
            if hasattr(a, "health")
        ]
        try:
            for adapter in candidates:
                gap = adapter.available()
                if gap is not None:
                    table.add_row(adapter.id, f"[yellow]skipped — {gap.human}[/yellow]")
                    continue
                try:
                    detail = await adapter.health(fetcher)  # type: ignore[attr-defined]
                    table.add_row(adapter.id, f"[green]{detail}[/green]")
                except FetchError as exc:
                    failures += 1
                    table.add_row(
                        adapter.id, f"[red]{exc.reason.value}: {exc.detail}[/red]"
                    )
                except Exception as exc:
                    failures += 1
                    table.add_row(adapter.id, f"[red]{exc}[/red]")
        finally:
            await fetcher.aclose()

        console.print(table)
        if not candidates:
            console.print("[yellow]No source has a health check configured.[/yellow]")
        return failures

    failures = asyncio.run(run())
    if failures:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
