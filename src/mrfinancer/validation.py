"""Sanity checks on extracted financial figures.

The problem this solves: a wrong `scale` or `sign` in an iXBRL filing produces a
number that is off by 1000x or inverted and *looks completely plausible*. Tests
prove the parser handles the attributes it is given; they cannot catch a filing
whose attributes are themselves wrong, or a future taxonomy change. A plausible
wrong number is worse than no number, so every figure is checked against things
that cannot be true, and anything that fails is withheld with its reason.

The checks fall into three kinds, in increasing order of usefulness:

* **Per-fact** -- turnover cannot be negative; a headcount cannot be 40 million.
  Cheap, and catches sign errors outright.
* **Scale/decimals coherence** -- iXBRL's `decimals` states the accuracy of a
  figure and `scale` states its presentation multiplier, so the two constrain
  each other exactly. A figure presented in thousands cannot also claim
  unit-level accuracy. This catches malformed filings the parser would
  otherwise trust.
* **Cross-fact** -- the strongest signals, because they need no absolute
  thresholds. Accounts always restate the prior year, so two periods of the
  same metric differing by almost exactly 1000x is the signature of a scale
  error, and it is near-certain when the two facts carry different `scale`
  attributes. Revenue per employee is the other: a 1000x error throws it far
  outside anything a real company produces.

Thresholds come from one named profile rather than a dozen separate settings.
See `PROFILES`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from .models import FactIssue, FinancialFact, Metric, Severity
from .provenance import Gap, ReasonCode

# Metrics that are money, and so need a currency to mean anything.
MONEY_METRICS = {
    Metric.TURNOVER,
    Metric.PROFIT_BEFORE_TAX,
    Metric.OPERATING_PROFIT,
    Metric.NET_ASSETS,
    Metric.CASH,
    Metric.AUTHORISED_CAPITAL,
    Metric.PAID_UP_CAPITAL,
    Metric.TOTAL_FUNDING,
    Metric.LAST_ROUND_AMOUNT,
    Metric.VALUATION,
}

# Metrics that cannot legitimately be negative. Net assets and the profit
# measures are deliberately absent: both are routinely negative.
NON_NEGATIVE_METRICS = {
    Metric.TURNOVER,
    Metric.CASH,
    Metric.EMPLOYEES,
    Metric.AUTHORISED_CAPITAL,
    Metric.PAID_UP_CAPITAL,
    Metric.TOTAL_FUNDING,
    Metric.LAST_ROUND_AMOUNT,
    Metric.VALUATION,
}


@dataclass(frozen=True)
class Thresholds:
    """Every tunable number in one place. Pick a profile instead of these."""

    # Absolute ceilings. Generous on purpose: these catch impossibilities, not
    # unusual companies.
    max_money: float = 1e13
    max_turnover: float = 1e12
    max_employees: float = 3e6

    # Revenue per employee, in the figure's own currency. A 1000x scale error
    # throws this far outside any real business.
    min_revenue_per_employee: float = 1e3
    max_revenue_per_employee: float = 5e7

    # A loss can exceed turnover, but not by an order of magnitude.
    max_abs_pbt_to_turnover: float = 5.0

    # How close a period-on-period ratio must be to a power of 1000 before we
    # call it a scale error. 0.15 means within 15%.
    scale_jump_tolerance: float = 0.15

    # Strict mode: treat every warning as grounds for withholding the figure.
    escalate_warnings: bool = False
    # Withhold money figures that arrive with no currency.
    require_currency: bool = False


PROFILES: dict[str, Thresholds] = {
    # Withhold anything questionable. For a build where a wrong number costs
    # more than a missing one -- which is the usual case for investor material.
    "strict": Thresholds(
        max_turnover=1e11,
        max_employees=1e6,
        min_revenue_per_employee=5e3,
        max_revenue_per_employee=1e7,
        max_abs_pbt_to_turnover=3.0,
        scale_jump_tolerance=0.25,
        escalate_warnings=True,
        require_currency=True,
    ),
    # Report questionable figures, flagged, and withhold only impossibilities.
    "balanced": Thresholds(),
    # Show almost everything. For exploring data, not for producing documents.
    "permissive": Thresholds(
        max_money=1e15,
        max_turnover=1e14,
        max_employees=1e7,
        min_revenue_per_employee=0.0,
        max_revenue_per_employee=float("inf"),
        max_abs_pbt_to_turnover=100.0,
        scale_jump_tolerance=0.02,
        escalate_warnings=False,
        require_currency=False,
    ),
}

DEFAULT_PROFILE = "balanced"

PROFILE_SUMMARY = {
    "strict": "Withhold anything questionable. Best for investor-facing output.",
    "balanced": "Flag questionable figures, withhold impossible ones. Default.",
    "permissive": "Show almost everything. For exploration, not documents.",
}

# What each threshold actually changes, for `mrfinancer settings`. Kept next to
# the thresholds so the two cannot drift apart.
THRESHOLD_EFFECTS: dict[str, str] = {
    "max_money": "Any money figure above this is withheld as impossible.",
    "max_turnover": "Turnover above this is flagged as implausible.",
    "max_employees": "Headcount above this is withheld (usually a money figure read as a count).",
    "min_revenue_per_employee": "Revenue per head below this is flagged.",
    "max_revenue_per_employee": "Revenue per head above this is flagged. Catches 1000x scale errors.",
    "max_abs_pbt_to_turnover": "Flags profit or loss more than this multiple of turnover.",
    "scale_jump_tolerance": "How near a period-on-period ratio must be to 1000x to count as a scale error.",
    "escalate_warnings": "Treat every warning as grounds for withholding the figure.",
    "require_currency": "Withhold money figures that arrive with no currency.",
}


def thresholds_for(
    profile: str = DEFAULT_PROFILE, **overrides: float | bool | None
) -> Thresholds:
    """Resolve a profile name, then apply any individual overrides.

    Unknown profile names fall back to the default rather than raising: a typo
    in configuration should not stop a founder getting a report, and the
    `settings` command shows which profile is actually in force.
    """
    base = PROFILES.get(profile.lower().strip(), PROFILES[DEFAULT_PROFILE])
    applied = {k: v for k, v in overrides.items() if v is not None}
    return replace(base, **applied) if applied else base


# ---------------------------------------------------------------------------
# per-fact rules
# ---------------------------------------------------------------------------


def _check_sign(fact: FinancialFact) -> list[FactIssue]:
    if fact.value is None or fact.value >= 0:
        return []
    if fact.metric not in NON_NEGATIVE_METRICS:
        return []
    return [
        FactIssue(
            rule="negative_impossible",
            severity=Severity.REJECT,
            message=(
                f"{fact.metric.value} came through as {fact.value:,.0f}, which "
                "cannot be negative. Most likely an iXBRL sign attribute applied "
                "where it should not have been."
            ),
        )
    ]


def _check_magnitude(fact: FinancialFact, t: Thresholds) -> list[FactIssue]:
    if fact.value is None:
        return []
    size = abs(fact.value)
    issues: list[FactIssue] = []

    if fact.metric is Metric.EMPLOYEES:
        if size > t.max_employees:
            issues.append(
                FactIssue(
                    rule="employees_impossible",
                    severity=Severity.REJECT,
                    message=(
                        f"Headcount of {size:,.0f} exceeds the ceiling of "
                        f"{t.max_employees:,.0f}. Probably a scaled money figure "
                        "read as a count."
                    ),
                )
            )
        return issues

    if fact.metric in MONEY_METRICS and size > t.max_money:
        issues.append(
            FactIssue(
                rule="money_impossible",
                severity=Severity.REJECT,
                message=(
                    f"{fact.metric.value} of {size:,.0f} exceeds the ceiling of "
                    f"{t.max_money:,.0f}. A scale attribute has almost certainly "
                    "been applied twice."
                ),
            )
        )
    elif fact.metric is Metric.TURNOVER and size > t.max_turnover:
        issues.append(
            FactIssue(
                rule="turnover_implausible",
                severity=Severity.WARN,
                message=(
                    f"Turnover of {size:,.0f} is above the plausible ceiling of "
                    f"{t.max_turnover:,.0f} for this kind of comparison."
                ),
            )
        )
    return issues


def _check_currency(fact: FinancialFact, t: Thresholds) -> list[FactIssue]:
    if fact.metric not in MONEY_METRICS or fact.currency:
        return []
    return [
        FactIssue(
            rule="missing_currency",
            severity=Severity.REJECT if t.require_currency else Severity.WARN,
            message=(
                "No currency stated, so this figure cannot be compared with "
                "others or converted."
            ),
        )
    ]


def _decimal_digits(text: str) -> int:
    """Digits after the decimal point in the figure as it was printed."""
    cleaned = text.replace(",", "").replace(" ", "").strip()
    if "." not in cleaned:
        return 0
    return len(cleaned.rsplit(".", 1)[-1])


def _check_scale_decimals(fact: FinancialFact) -> list[FactIssue]:
    """iXBRL `scale` and `decimals` constrain each other exactly.

    `scale` is the presentation multiplier; `decimals` is the accuracy. A figure
    printed as an integer with scale=3 steps in thousands, so it cannot be
    accurate to the unit: the finest accuracy it can claim is -3. One decimal
    digit buys one back, so expected accuracy is (decimal digits - scale).

    A filing claiming *finer* accuracy than its own presentation allows is
    malformed, and a malformed scale is precisely the 1000x bug. Claiming
    coarser accuracy is normal rounding and passes silently.
    """
    if fact.source_scale is None or fact.source_decimals is None:
        return []
    if fact.source_scale <= 0:
        return []

    expected = _decimal_digits(fact.source_text or "") - fact.source_scale
    if fact.source_decimals <= expected:
        return []
    return [
        FactIssue(
            rule="scale_decimals_incoherent",
            severity=Severity.WARN,
            message=(
                f'Printed as "{fact.source_text}" with scale={fact.source_scale} '
                f"and decimals={fact.source_decimals}, but that presentation can "
                f"only support decimals={expected} or coarser. The scale "
                "attribute may be wrong, which would make this figure off by a "
                "factor of a thousand."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# cross-fact rules
# ---------------------------------------------------------------------------


def _is_power_of_thousand(ratio: float, tolerance: float) -> int | None:
    """Return the exponent if ratio is ~1000**n for n in 1..3, else None."""
    for exponent in (1, 2, 3):
        target = 1000.0**exponent
        if abs(ratio - target) <= target * tolerance:
            return exponent
    return None


def _check_scale_jumps(
    facts: list[FinancialFact], t: Thresholds
) -> list[tuple[FinancialFact, FactIssue]]:
    """Compare periods of the same metric for a thousand-fold step.

    This is the best detector available, because accounts restate the prior
    year right beside the current one. Two periods differing by almost exactly
    1000x is the signature of a scale error. When the two facts also carry
    *different* scale attributes it is near-certain, and the odd one out is
    withheld rather than flagged.
    """
    found: list[tuple[FinancialFact, FactIssue]] = []
    by_metric: dict[Metric, list[FinancialFact]] = {}
    for fact in facts:
        if fact.value is None or fact.value == 0 or fact.metric is Metric.EMPLOYEES:
            continue
        by_metric.setdefault(fact.metric, []).append(fact)

    for metric, group in by_metric.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda f: (f.period_end or date.min))
        for older, newer in zip(ordered, ordered[1:]):
            a, b = abs(older.value or 0), abs(newer.value or 0)
            if a == 0 or b == 0:
                continue
            ratio = max(a, b) / min(a, b)
            exponent = _is_power_of_thousand(ratio, t.scale_jump_tolerance)
            if exponent is None:
                continue

            scales_differ = (
                older.source_scale is not None
                and newer.source_scale is not None
                and older.source_scale != newer.source_scale
            )
            factor = 1000**exponent
            if scales_differ:
                # Blame the period whose scale attribute is smaller: that is
                # the figure presented without the multiplier its sibling got.
                suspect = (
                    newer
                    if (newer.source_scale or 0) < (older.source_scale or 0)
                    else older
                )
                found.append(
                    (
                        suspect,
                        FactIssue(
                            rule="scale_jump",
                            severity=Severity.REJECT,
                            message=(
                                f"{metric.value} differs from the adjacent period "
                                f"by ~{factor:,}x, and the two periods carry "
                                f"different scale attributes "
                                f"({older.source_scale} vs {newer.source_scale}). "
                                "This is a presentation error in the filing, not "
                                "growth, so the figure is withheld."
                            ),
                        ),
                    )
                )
            else:
                for fact in (older, newer):
                    found.append(
                        (
                            fact,
                            FactIssue(
                                rule="scale_jump",
                                severity=Severity.WARN,
                                message=(
                                    f"{metric.value} changes by ~{factor:,}x "
                                    f"between periods ending "
                                    f"{older.period_end} and {newer.period_end}. "
                                    "That can be real, but it is also what a "
                                    "scale error looks like. Check the filing."
                                ),
                            ),
                        )
                    )
    return found


def _check_revenue_per_employee(
    facts: list[FinancialFact], t: Thresholds
) -> list[tuple[FinancialFact, FactIssue]]:
    """Revenue per head, which needs no absolute threshold to be useful."""
    if t.max_revenue_per_employee == float("inf") and t.min_revenue_per_employee <= 0:
        return []

    turnovers = [
        f for f in facts if f.metric is Metric.TURNOVER and f.value not in (None, 0)
    ]
    employees = [
        f for f in facts if f.metric is Metric.EMPLOYEES and f.value not in (None, 0)
    ]
    if not turnovers or not employees:
        return []

    def newest(group: list[FinancialFact]) -> FinancialFact:
        return max(group, key=lambda f: (f.period_end or date.min))

    turnover = newest(turnovers)
    headcount = newest(employees)
    # Prefer a matching period when one exists; comparing years would itself
    # produce false positives.
    same_period = [
        f for f in employees if f.period_end and f.period_end == turnover.period_end
    ]
    if same_period:
        headcount = same_period[0]

    assert turnover.value is not None and headcount.value is not None
    rpe = abs(turnover.value) / abs(headcount.value)
    if t.min_revenue_per_employee <= rpe <= t.max_revenue_per_employee:
        return []

    direction = "high" if rpe > t.max_revenue_per_employee else "low"
    return [
        (
            turnover,
            FactIssue(
                rule="revenue_per_employee",
                severity=Severity.WARN,
                message=(
                    f"Turnover of {turnover.value:,.0f} over "
                    f"{headcount.value:,.0f} employees is "
                    f"{rpe:,.0f} per head, implausibly {direction} "
                    f"(expected {t.min_revenue_per_employee:,.0f} to "
                    f"{t.max_revenue_per_employee:,.0f}). A thousand-fold scale "
                    "error on either figure would look exactly like this."
                ),
            ),
        )
    ]


def _check_pbt_against_turnover(
    facts: list[FinancialFact], t: Thresholds
) -> list[tuple[FinancialFact, FactIssue]]:
    turnover = next(
        (f for f in facts if f.metric is Metric.TURNOVER and f.value not in (None, 0)),
        None,
    )
    pbt = next(
        (
            f
            for f in facts
            if f.metric is Metric.PROFIT_BEFORE_TAX and f.value not in (None, 0)
        ),
        None,
    )
    if turnover is None or pbt is None:
        return []
    assert turnover.value is not None and pbt.value is not None

    ratio = abs(pbt.value) / abs(turnover.value)
    if ratio <= t.max_abs_pbt_to_turnover:
        return []
    return [
        (
            pbt,
            FactIssue(
                rule="pbt_vs_turnover",
                severity=Severity.WARN,
                message=(
                    f"Profit before tax of {pbt.value:,.0f} is {ratio:,.1f}x "
                    f"turnover of {turnover.value:,.0f}. Losses can exceed "
                    "turnover, but not usually by this much; one of the two may "
                    "be mis-scaled."
                ),
            ),
        )
    ]


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def validate_facts(
    facts: list[FinancialFact], thresholds: Thresholds | None = None
) -> tuple[list[FinancialFact], list[Gap]]:
    """Attach issues to each fact and return gaps for anything withheld.

    Facts are mutated in place and also returned, so a caller can ignore the
    return value and still see the issues. Rejected facts stay in the list --
    `CompetitorProfile.latest()` and `series()` filter them out, so the evidence
    survives for debugging while never reaching the UI as a number.
    """
    t = thresholds or PROFILES[DEFAULT_PROFILE]

    for fact in facts:
        issues = (
            _check_sign(fact)
            + _check_magnitude(fact, t)
            + _check_currency(fact, t)
            + _check_scale_decimals(fact)
        )
        fact.issues.extend(issues)

    for fact, issue in (
        _check_scale_jumps(facts, t)
        + _check_revenue_per_employee(facts, t)
        + _check_pbt_against_turnover(facts, t)
    ):
        fact.issues.append(issue)

    if t.escalate_warnings:
        for fact in facts:
            fact.issues = [
                FactIssue(
                    rule=i.rule,
                    severity=Severity.REJECT if i.severity is Severity.WARN else i.severity,
                    message=i.message,
                )
                for i in fact.issues
            ]

    gaps: list[Gap] = []
    seen: set[tuple[str, str]] = set()
    for fact in facts:
        for issue in fact.issues:
            if issue.severity is not Severity.REJECT:
                continue
            key = (fact.metric.value, issue.rule)
            if key in seen:
                continue
            seen.add(key)
            gaps.append(
                Gap(
                    field=fact.metric.value,
                    reason=ReasonCode.FAILED_VALIDATION,
                    source=fact.provenance.source,
                    detail=issue.message,
                )
            )
    return facts, gaps
