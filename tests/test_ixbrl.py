"""Tests for the iXBRL extractor.

These are the tests that matter most in the project: a scale or sign error here
reports a competitor's revenue off by 1000x or with the wrong sign, and it would
look entirely plausible in the UI.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from mrfinancer.ixbrl import extract_facts, latest_per_metric
from mrfinancer.models import Metric

FIXTURE = Path(__file__).parent / "fixtures" / "accounts_sample.xhtml"


@pytest.fixture(scope="module")
def facts():
    return extract_facts(FIXTURE.read_text(encoding="utf-8"))


def test_finds_all_tracked_concepts_and_ignores_others(facts):
    metrics = {f.metric for f in facts}
    assert Metric.TURNOVER in metrics
    assert Metric.PROFIT_BEFORE_TAX in metrics
    assert Metric.EMPLOYEES in metrics
    assert Metric.NET_ASSETS in metrics
    # TaxExpenseIncomeTax is in the document but not something we track.
    assert len(facts) == 5


def test_scale_attribute_is_applied(facts):
    turnovers = sorted(
        (f for f in facts if f.metric is Metric.TURNOVER),
        key=lambda f: f.period_end,
    )
    # "4,812" with scale="3" means GBP 4,812,000 -- not 4812.
    assert turnovers[-1].value == pytest.approx(4_812_000)
    assert turnovers[0].value == pytest.approx(3_105_000)


def test_sign_attribute_negates(facts):
    pbt = next(f for f in facts if f.metric is Metric.PROFIT_BEFORE_TAX)
    # sign="-" on 612 with scale 3 is a loss of 612,000.
    assert pbt.value == pytest.approx(-612_000)


def test_period_comes_from_the_context(facts):
    turnover = max(
        (f for f in facts if f.metric is Metric.TURNOVER),
        key=lambda f: f.period_end,
    )
    assert turnover.period_start == date(2023, 4, 1)
    assert turnover.period_end == date(2024, 3, 31)


def test_instant_context_populates_period_end_only(facts):
    net_assets = next(f for f in facts if f.metric is Metric.NET_ASSETS)
    assert net_assets.period_start is None
    assert net_assets.period_end == date(2024, 3, 31)


def test_currency_from_unit_and_none_for_non_monetary(facts):
    turnover = next(f for f in facts if f.metric is Metric.TURNOVER)
    employees = next(f for f in facts if f.metric is Metric.EMPLOYEES)
    assert turnover.currency == "GBP"
    # xbrli:pure is not a currency, so we must not claim one.
    assert employees.currency is None
    assert employees.value == pytest.approx(27)


def test_latest_per_metric_prefers_the_current_year(facts):
    latest = latest_per_metric(facts)
    assert latest[Metric.TURNOVER].value == pytest.approx(4_812_000)
    assert latest[Metric.TURNOVER].period_end == date(2024, 3, 31)
    # One entry per metric, not one per period.
    assert len(latest) == 4


def test_garbage_input_yields_nothing_rather_than_raising():
    assert extract_facts("<html><body>not a filing</body></html>") == []
    assert extract_facts("") == []


def test_filleted_accounts_without_turnover():
    """Small companies may omit turnover. We must return the facts present and
    simply not have a turnover fact -- never a zero."""
    doc = """<?xml version="1.0"?>
    <html xmlns="http://www.w3.org/1999/xhtml"
          xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
          xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:core="http://xbrl.frc.org.uk/fr/2021-01-01/core"
          xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
      <body><div style="display:none"><ix:header><ix:resources>
        <xbrli:context id="AT">
          <xbrli:period><xbrli:instant>2025-03-31</xbrli:instant></xbrli:period>
        </xbrli:context>
        <xbrli:unit id="GBP"><xbrli:measure>iso4217:GBP</xbrli:measure></xbrli:unit>
      </ix:resources></ix:header></div>
      <ix:nonFraction name="core:NetAssetsLiabilities" contextRef="AT"
          unitRef="GBP">88250</ix:nonFraction>
      </body></html>"""
    facts = extract_facts(doc)
    metrics = {f.metric for f in facts}
    assert metrics == {Metric.NET_ASSETS}
    assert facts[0].value == pytest.approx(88_250)
