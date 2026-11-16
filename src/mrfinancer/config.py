"""Configuration.

Scrapers are off by default and each must be switched on by name. That is a
deliberate choice: the hybrid strategy means this codebase *can* scrape, but a
founder running the app should have to opt in per site, and the report tells
them which sources were scraped so they can judge the risk themselves.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from .validation import Thresholds


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MRF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- credentials -------------------------------------------------------
    investor_directory: Path | None = None
    pitch_image_space_url: str = "https://vivekchakraverty-mr-financer-pitch-images.hf.space"
    business_plan_space_url: str = "https://vivekchakraverty-mr-financer-business-plan.hf.space"
    learn_space_url: str = "https://vivekchakraverty-mr-financer-learn.hf.space"
    learn_space_token: str | None = Field(default=None, repr=False)
    learn_database: Path = Path(".mrfinancer-learn.sqlite")
    learn_yt_dlp: str = "yt-dlp"
    learn_collect_comments: bool = True
    trustpilot_api_key: str | None = Field(default=None, repr=False)
    google_places_api_key: str | None = Field(default=None, repr=False)
    g2_api_key: str | None = Field(default=None, repr=False)
    g2_usage_db: Path = Path(".mrfinancer-g2-usage.sqlite")
    g2_global_monthly_limit: int = Field(default=1000, ge=1)
    producthunt_token: str | None = Field(default=None, repr=False)
    producthunt_client_id: str | None = Field(default=None, repr=False)
    producthunt_client_secret: str | None = Field(default=None, repr=False)
    producthunt_enabled: bool = True
    foursquare_service_key: str | None = Field(default=None, repr=False)
    foursquare_snapshot: Path | None = None
    foursquare_catalog_token: str | None = Field(default=None, repr=False)
    foursquare_catalog_uri: str | None = None
    foursquare_catalog_warehouse: str | None = None
    foursquare_catalog_table: str | None = None
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    discovery_timeout_seconds: float = Field(default=18.0, gt=0)
    companies_house_key: str | None = Field(default=None, repr=False)
    opendart_key: str | None = Field(default=None, repr=False)
    sec_contact: str | None = None
    inpi_username: str | None = Field(default=None, repr=False)
    inpi_password: str | None = Field(default=None, repr=False)
    financial_timeout_seconds: float = Field(default=30.0, gt=0)
    search_provider: str = Field(
        default="none", description="none | brave | serper | tavily"
    )
    search_api_key: str | None = None

    # --- scraper opt-ins (hybrid strategy, off by default) -----------------
    enable_scraper_alternativeto: bool = False
    enable_scraper_mca: bool = False
    respect_robots: bool = Field(
        default=True,
        description="Never set False for a shipped build; it exists for testing "
        "against your own sites only.",
    )

    # --- politeness --------------------------------------------------------
    user_agent: str = (
        "MrFinancer/0.1 (+https://example.invalid/mr-financer) "
        "founder-research-tool"
    )
    requests_per_second: float = Field(default=1.0, gt=0)
    max_concurrency: int = Field(default=4, ge=1)
    timeout_seconds: float = Field(default=20.0, gt=0)
    max_retries: int = Field(default=2, ge=0)

    # Product Hunt bills complexity points, not requests: 6250 per 15 minutes.
    producthunt_complexity_budget: int = 6250
    producthunt_budget_window_seconds: int = 900

    # --- behaviour ---------------------------------------------------------
    max_competitors: int = Field(default=12, ge=1, le=100)
    min_similarity: float = Field(default=0.25, ge=0.0, le=1.0)
    # Below this, we refuse to attach a company's financials to a product.
    min_company_match_confidence: float = Field(default=0.80, ge=0.0, le=1.0)

    cache_path: Path = Path(".mrfinancer-cache.sqlite")
    cache_ttl_seconds: int = 60 * 60 * 24

    # --- figure validation -------------------------------------------------
    # One knob, not a dozen. `mrfinancer settings` prints what each profile
    # resolves to, and any single value below overrides the profile.
    validation_profile: str = Field(
        default="balanced",
        description="strict | balanced | permissive. See `mrfinancer settings`.",
    )
    max_turnover: float | None = None
    max_employees: float | None = None
    min_revenue_per_employee: float | None = None
    max_revenue_per_employee: float | None = None
    max_abs_pbt_to_turnover: float | None = None
    scale_jump_tolerance: float | None = None
    require_currency: bool | None = None
    escalate_warnings: bool | None = None

    def thresholds(self) -> "Thresholds":
        """Resolve the profile, then apply any individual overrides."""
        from .validation import thresholds_for

        return thresholds_for(
            self.validation_profile,
            max_turnover=self.max_turnover,
            max_employees=self.max_employees,
            min_revenue_per_employee=self.min_revenue_per_employee,
            max_revenue_per_employee=self.max_revenue_per_employee,
            max_abs_pbt_to_turnover=self.max_abs_pbt_to_turnover,
            scale_jump_tolerance=self.scale_jump_tolerance,
            require_currency=self.require_currency,
            escalate_warnings=self.escalate_warnings,
        )

    def validation_overrides(self) -> dict[str, float | bool]:
        """Which validation values were set explicitly, for the settings view."""
        names = (
            "max_turnover",
            "max_employees",
            "min_revenue_per_employee",
            "max_revenue_per_employee",
            "max_abs_pbt_to_turnover",
            "scale_jump_tolerance",
            "require_currency",
            "escalate_warnings",
        )
        return {n: getattr(self, n) for n in names if getattr(self, n) is not None}

    def enabled_scrapers(self) -> set[str]:
        out: set[str] = set()
        if self.enable_scraper_alternativeto:
            out.add("alternativeto")
        if self.enable_scraper_mca:
            out.add("mca_india")
        return out


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(s: Settings) -> None:
    """Test seam."""
    global _settings
    _settings = s
