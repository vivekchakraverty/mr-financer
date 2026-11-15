"""Tests for the figure sanity layer.

The scenario each of these encodes: the parser did its job correctly on the
attributes it was given, and the *filing* or the taxonomy was wrong. Unit tests
on the parser cannot catch that. These can.
"""

from __future__ import annotations

from datetime import date

import pytest

from mrfinancer.models import FinancialFact, Metric, Severity
from mrfinancer.provenance import AccessMethod, Provenance, ReasonCode, Trust
from mrfinancer.validation import (
    DEFAULT_PROFILE,
    PROFILES,
    Thresholds,
    thresholds_for,
    validate_facts,
)


def prov() -> Provenance:
    return Provenance(
        source="companies_house",
        source_name="UK Companies House",
        method=AccessMethod.OFFICIAL_API,
        trust=Trust.STATUTORY,
        url="https://example.invalid/filing",
    )


def fact(
    metric: Metric,
    value: float,
    *,
    period_end: date | None = date(2024, 3, 31),
    currency: str | None = "GBP",
    scale: int | None = None,
    decimals: int | None = None,
    text: str = "",
) -> FinancialFact:
    return FinancialFact(
        metric=metric,
        value=value,
        currency=currency,
        period_end=period_end,
        as_filed=True,
        source_scale=scale,
        source_decimals=decimals,
        source_text=text,
        provenance=prov(),
    )


def rules(f: FinancialFact) -> set[str]:
    return {i.rule for i in f.issues}


class TestProfiles:
    def test_default_profile_is_balanced(self):
        assert thresholds_for() == PROFILES[DEFAULT_PROFILE]

    def test_unknown_profile_falls_back_rather_than_raising(self):
        """A typo in configuration must not stop a founder getting a report."""
        assert thresholds_for("STRICTT") == PROFILES[DEFAULT_PROFILE]

    def test_profile_name_is_case_and_space_insensitive(self):
        assert thresholds_for("  Strict ") == PROFILES["strict"]

    def test_single_value_override_applies_on_top_of_a_profile(self):
        t = thresholds_for("strict", max_employees=42)
        assert t.max_employees == 42
        # Everything else still comes from the profile.
        assert t.escalate_warnings is PROFILES["strict"].escalate_warnings

    def test_none_overrides_are_ignored(self):
        assert thresholds_for("balanced", max_employees=None) == PROFILES["balanced"]


class TestSignErrors:
    def test_negative_turnover_is_withheld(self):
        f = fact(Metric.TURNOVER, -4_812_000)
        _, gaps = validate_facts([f])
        assert "negative_impossible" in rules(f)
        assert f.rejected
        assert gaps and gaps[0].reason is ReasonCode.FAILED_VALIDATION

    def test_negative_loss_is_perfectly_fine(self):
        """A sign check that rejected losses would be worse than no check."""
        f = fact(Metric.PROFIT_BEFORE_TAX, -612_000)
        validate_facts([f])
        assert f.issues == []

    def test_negative_net_assets_is_fine(self):
        f = fact(Metric.NET_ASSETS, -250_000)
        validate_facts([f])
        assert f.issues == []


class TestMagnitude:
    def test_absurd_headcount_is_withheld(self):
        f = fact(Metric.EMPLOYEES, 4_812_000, currency=None)
        validate_facts([f])
        assert "employees_impossible" in rules(f)
        assert f.rejected

    def test_money_beyond_the_ceiling_is_withheld(self):
        f = fact(Metric.TURNOVER, 4.8e13)
        validate_facts([f])
        assert "money_impossible" in rules(f)
        assert f.rejected

    def test_ordinary_figures_pass_clean(self):
        facts = [
            fact(Metric.TURNOVER, 4_812_000),
            fact(Metric.PROFIT_BEFORE_TAX, -612_000),
            fact(Metric.EMPLOYEES, 27, currency=None),
            fact(Metric.NET_ASSETS, 1_904_000),
        ]
        _, gaps = validate_facts(facts)
        assert gaps == []
        assert all(f.issues == [] for f in facts)


class TestCurrency:
    def test_missing_currency_warns_by_default(self):
        f = fact(Metric.TURNOVER, 4_812_000, currency=None)
        validate_facts([f])
        assert "missing_currency" in rules(f)
        assert f.suspect and not f.rejected

    def test_strict_profile_withholds_it(self):
        f = fact(Metric.TURNOVER, 4_812_000, currency=None)
        validate_facts([f], thresholds_for("strict"))
        assert f.rejected


class TestScaleDecimalsCoherence:
    def test_thousands_presentation_claiming_unit_accuracy_is_flagged(self):
        """scale=3 on an integer steps in thousands, so decimals=0 is a lie.
        That contradiction is exactly what a wrong scale looks like."""
        f = fact(Metric.TURNOVER, 4_812_000, scale=3, decimals=0, text="4,812")
        validate_facts([f])
        assert "scale_decimals_incoherent" in rules(f)

    def test_coherent_filing_passes(self):
        f = fact(Metric.TURNOVER, 4_812_000, scale=3, decimals=-3, text="4,812")
        validate_facts([f])
        assert "scale_decimals_incoherent" not in rules(f)

    def test_decimal_digits_buy_back_precision(self):
        # "4,812.5" at scale 3 steps in hundreds, so decimals=-2 is coherent.
        f = fact(Metric.TURNOVER, 4_812_500, scale=3, decimals=-2, text="4,812.5")
        validate_facts([f])
        assert "scale_decimals_incoherent" not in rules(f)

    def test_coarser_accuracy_claim_is_not_flagged(self):
        f = fact(Metric.TURNOVER, 4_812_000, scale=3, decimals=-6, text="4,812")
        validate_facts([f])
        assert "scale_decimals_incoherent" not in rules(f)

    def test_unscaled_facts_are_not_checked(self):
        f = fact(Metric.NET_ASSETS, 88_250, scale=0, decimals=0, text="88250")
        validate_facts([f])
        assert "scale_decimals_incoherent" not in rules(f)


class TestScaleJumpAcrossPeriods:
    def test_thousandfold_step_with_differing_scales_is_withheld(self):
        """The near-certain case: the same metric restated for the prior year,
        1000x apart, with different scale attributes. One of them was not
        multiplied. That is a filing error, not growth."""
        current = fact(
            Metric.TURNOVER,
            4_812_000,
            period_end=date(2024, 3, 31),
            scale=3,
            text="4,812",
        )
        prior = fact(
            Metric.TURNOVER,
            4_812,
            period_end=date(2023, 3, 31),
            scale=0,
            text="4812",
        )
        _, gaps = validate_facts([current, prior])
        assert prior.rejected
        assert "scale_jump" in rules(prior)
        assert gaps and "different scale attributes" in gaps[0].detail

    def test_thousandfold_step_with_equal_scales_only_warns(self):
        """Could be real hypergrowth, so flag it and let a human look."""
        current = fact(
            Metric.TURNOVER, 4_812_000, period_end=date(2024, 3, 31), scale=3
        )
        prior = fact(Metric.TURNOVER, 4_812, period_end=date(2023, 3, 31), scale=3)
        validate_facts([current, prior])
        assert current.suspect and not current.rejected
        assert prior.suspect and not prior.rejected

    def test_normal_growth_is_untouched(self):
        current = fact(
            Metric.TURNOVER, 4_812_000, period_end=date(2024, 3, 31), scale=3
        )
        prior = fact(
            Metric.TURNOVER, 3_105_000, period_end=date(2023, 3, 31), scale=3
        )
        _, gaps = validate_facts([current, prior])
        assert gaps == []
        assert current.issues == [] and prior.issues == []

    def test_a_tenfold_jump_is_not_a_scale_error(self):
        current = fact(Metric.TURNOVER, 48_120_000, period_end=date(2024, 3, 31))
        prior = fact(Metric.TURNOVER, 4_812_000, period_end=date(2023, 3, 31))
        validate_facts([current, prior])
        assert "scale_jump" not in rules(current)

    def test_a_millionfold_jump_is_caught(self):
        current = fact(
            Metric.TURNOVER, 4_812_000_000, period_end=date(2024, 3, 31), scale=6
        )
        prior = fact(
            Metric.TURNOVER, 4_812, period_end=date(2023, 3, 31), scale=0
        )
        validate_facts([current, prior])
        assert "scale_jump" in rules(prior)

    def test_headcount_is_exempt(self):
        """Employee counts are small integers; a 1000x ratio there is a real
        change in a tiny number, not a presentation error."""
        current = fact(
            Metric.EMPLOYEES, 1000, period_end=date(2024, 3, 31), currency=None
        )
        prior = fact(Metric.EMPLOYEES, 1, period_end=date(2023, 3, 31), currency=None)
        validate_facts([current, prior])
        assert "scale_jump" not in rules(current)
        assert "scale_jump" not in rules(prior)


class TestRevenuePerEmployee:
    def test_implausibly_high_revenue_per_head_is_flagged(self):
        """The signature of a 1000x scale error with no absolute threshold
        needed: 27 people cannot produce GBP 4.8bn."""
        turnover = fact(Metric.TURNOVER, 4_812_000_000)
        headcount = fact(Metric.EMPLOYEES, 27, currency=None)
        validate_facts([turnover, headcount])
        assert "revenue_per_employee" in rules(turnover)
        assert "per head" in next(
            i.message for i in turnover.issues if i.rule == "revenue_per_employee"
        )

    def test_realistic_revenue_per_head_passes(self):
        turnover = fact(Metric.TURNOVER, 4_812_000)
        headcount = fact(Metric.EMPLOYEES, 27, currency=None)
        _, gaps = validate_facts([turnover, headcount])
        assert gaps == []
        assert turnover.issues == []

    def test_permissive_profile_skips_the_check(self):
        turnover = fact(Metric.TURNOVER, 4_812_000_000)
        headcount = fact(Metric.EMPLOYEES, 27, currency=None)
        validate_facts([turnover, headcount], thresholds_for("permissive"))
        assert "revenue_per_employee" not in rules(turnover)

    def test_no_headcount_means_no_check(self):
        turnover = fact(Metric.TURNOVER, 4_812_000_000)
        validate_facts([turnover])
        assert "revenue_per_employee" not in rules(turnover)


class TestProfitAgainstTurnover:
    def test_loss_far_exceeding_turnover_is_flagged(self):
        turnover = fact(Metric.TURNOVER, 100_000)
        pbt = fact(Metric.PROFIT_BEFORE_TAX, -9_000_000)
        validate_facts([turnover, pbt])
        assert "pbt_vs_turnover" in rules(pbt)

    def test_an_ordinary_loss_is_not_flagged(self):
        turnover = fact(Metric.TURNOVER, 4_812_000)
        pbt = fact(Metric.PROFIT_BEFORE_TAX, -612_000)
        validate_facts([turnover, pbt])
        assert pbt.issues == []


class TestEscalation:
    def test_strict_turns_every_warning_into_a_withholding(self):
        turnover = fact(Metric.TURNOVER, 4_812_000_000)
        headcount = fact(Metric.EMPLOYEES, 27, currency=None)
        _, gaps = validate_facts([turnover, headcount], thresholds_for("strict"))
        assert turnover.rejected
        assert all(i.severity is Severity.REJECT for i in turnover.issues)
        assert gaps

    def test_balanced_keeps_them_visible_but_marked(self):
        turnover = fact(Metric.TURNOVER, 4_812_000_000)
        headcount = fact(Metric.EMPLOYEES, 27, currency=None)
        validate_facts([turnover, headcount], thresholds_for("balanced"))
        assert turnover.suspect and not turnover.rejected


class TestRejectedFactsAreInvisibleToTheUI:
    def test_latest_and_series_skip_rejected_facts(self):
        from mrfinancer.models import CompetitorProfile, ProductMatch

        good = fact(Metric.TURNOVER, 4_812_000, period_end=date(2023, 3, 31))
        bad = fact(Metric.TURNOVER, -1_000, period_end=date(2024, 3, 31))
        validate_facts([good, bad])

        profile = CompetitorProfile(
            product=ProductMatch(name="X", provenance=prov()),
            financials=[good, bad],
        )
        # The newest fact is the rejected one; it must not be what the UI sees.
        assert profile.latest(Metric.TURNOVER) is good
        assert profile.series(Metric.TURNOVER) == [good]
        assert profile.has_statutory_financials is True

    def test_profile_with_only_rejected_facts_reports_none(self):
        from mrfinancer.models import CompetitorProfile, ProductMatch

        bad = fact(Metric.TURNOVER, -1_000)
        validate_facts([bad])
        profile = CompetitorProfile(
            product=ProductMatch(name="X", provenance=prov()),
            financials=[bad],
        )
        assert profile.latest(Metric.TURNOVER) is None
        assert profile.has_statutory_financials is False


class TestGapReporting:
    def test_one_gap_per_metric_and_rule(self):
        a = fact(Metric.TURNOVER, -1, period_end=date(2024, 3, 31))
        b = fact(Metric.TURNOVER, -2, period_end=date(2023, 3, 31))
        _, gaps = validate_facts([a, b])
        assert len([g for g in gaps if g.field == "turnover"]) == 1

    def test_gap_detail_explains_the_likely_cause(self):
        f = fact(Metric.TURNOVER, -4_812_000)
        _, gaps = validate_facts([f])
        assert "sign attribute" in gaps[0].detail
