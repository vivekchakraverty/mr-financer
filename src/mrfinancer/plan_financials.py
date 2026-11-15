"""Validate plan inputs and build the authoritative financial exhibits."""
from decimal import Decimal as D, ROUND_CEILING, ROUND_HALF_UP
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

ZERO = D(0)
HUNDRED = D(100)
Status = Literal["actual", "estimate", "unknown", "na"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Amount(Input):
    status: Status = "unknown"
    low: D | None = Field(None, ge=0, le=10**12, max_digits=17, decimal_places=4)
    high: D | None = Field(None, ge=0, le=10**12, max_digits=17, decimal_places=4)

    @model_validator(mode="after")
    def check_value(self):
        if self.status in ("actual", "estimate"):
            if self.low is None or (self.high is not None and self.high < self.low):
                raise ValueError("Enter a number or an ordered range.")
            if self.status == "actual" and self.high is not None:
                raise ValueError("Use Estimate for ranges.")
        elif self.low is not None or self.high is not None:
            raise ValueError("Select Actual or Estimate to enter a value.")
        return self

    def pick(self, scenario, expense=False):
        if self.status == "na":
            return ZERO
        if self.low is None or self.high is None:
            return self.low
        if scenario == "base":
            return (self.low + self.high) / 2
        optimistic = scenario == "optimistic"
        return self.high if optimistic != expense else self.low


class Offer(Input):
    label: str = Field(min_length=1, max_length=100)
    unit: Literal["orders", "jobs", "active subscriptions", "transactions", "other paid units"]
    price: Amount = Field(default_factory=Amount)
    commission_percent: Amount | None = None
    first: Amount = Field(default_factory=Amount)
    last: Amount = Field(default_factory=Amount)
    variable: Amount = Field(default_factory=Amount)
    capacity: Amount = Field(default_factory=Amount)

    @model_validator(mode="after")
    def check_offer(self):
        for value in (self.first, self.last, self.capacity):
            if any(x is not None and x % 1 for x in (value.low, value.high)):
                raise ValueError("Enter whole numbers for sales volume and capacity.")
        if self.commission_percent and any(
            x is not None and x > 100 for x in (self.commission_percent.low, self.commission_percent.high)
        ):
            raise ValueError("Commission must be between 0 and 100 percent.")
        return self


class CashItem(Input):
    label: str = Field(min_length=1, max_length=100)
    amount: Amount
    month: int = Field(ge=1, le=12)
    kind: Literal["startup", "confirmed funding", "unconfirmed funding", "other inflow", "other outflow"]


class PeerFact(Input):
    company: str = Field(min_length=1, max_length=200)
    entity_id: str = Field("", max_length=100)
    fit: str = Field("", max_length=280)
    metric: Literal["turnover", "cost_of_sales", "gross_profit", "operating_profit",
                    "net_profit", "profit_before_tax", "cash", "debt", "net_assets",
                    "total_assets", "total_liabilities", "employees", "funding"]
    amount: D = Field(ge=-(10**16), le=10**16, max_digits=21, decimal_places=4)
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    unit: str = Field("", max_length=30)
    period_start: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    scope: Literal["consolidated", "standalone", "segment", "unknown"] = "unknown"
    source: str = Field("", max_length=180)
    url: HttpUrl | None = None
    locator: str = Field("", max_length=250)
    status: Literal["reported", "derived", "estimated", "unverified"] = "unverified"

    @model_validator(mode="after")
    def check_source(self):
        from datetime import date

        end = date.fromisoformat(self.period_end)
        if self.period_start and date.fromisoformat(self.period_start) > end:
            raise ValueError("The reporting start date must precede the end date.")
        if self.metric != "employees" and not self.currency:
            raise ValueError("Enter the original currency for monetary competitor figures.")
        if self.status in ("reported", "derived") and not self.url and not (self.source and self.locator):
            raise ValueError("Add a filing link or a source name and locator for reported figures.")
        if self.url and (self.url.username is not None or self.url.password is not None):
            raise ValueError("Remove credentials from filing links.")
        return self


class PlanRequest(Input):
    idea: str = Field(min_length=3, max_length=2000)
    customer: str = Field("", max_length=800)
    market: str = Field("", max_length=300)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    stage: Literal["idea", "testing", "about to launch", "already selling"]
    launch: str = Field("", max_length=100)
    alternative: str = Field("", max_length=800)
    channels: str = Field("", max_length=800)
    team_goal: str = Field("", max_length=1200)
    audience: Literal["founder or team", "lender", "investor", "grant application", "other"]
    notes: str = Field("", max_length=1200)
    recent: str = Field("", max_length=1200)
    offers: list[Offer] = Field(min_length=1, max_length=4)
    fixed: Amount = Field(default_factory=Amount)
    opening: Amount = Field(default_factory=Amount)
    receipts_days: int | None = Field(None, ge=0, le=365)
    supplier_days: int | None = Field(None, ge=0, le=365)
    events: list[CashItem] = Field(default_factory=list, max_length=20)
    peers: list[PeerFact] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def check_peer_count(self):
        if len({(p.entity_id or p.company).casefold() for p in self.peers}) > 5:
            raise ValueError("Choose up to five comparable companies.")
        return self


def total(values):
    values = list(values)
    return None if any(v is None for v in values) else sum(values, ZERO)


def multiply(a, b):
    if a == ZERO or b == ZERO:
        return ZERO
    return None if a is None or b is None else a * b


def difference(a, b):
    return None if a is None or b is None else a - b


def share(numerator, denominator):
    return None if numerator is None or denominator is None or denominator <= 0 else numerator * 100 / denominator


def presented(x):
    if isinstance(x, D):
        return str(x.quantize(D(".01"), rounding=ROUND_HALF_UP))
    return x


def collect_cash(accruals, days, index):
    # Assume transactions are spread uniformly through a 30-day planning month.
    if days is None:
        return ZERO if all(v == ZERO for v in accruals[:index + 1]) else None
    months, remaining = divmod(days, 30)
    recent = index - months
    older = recent - 1
    main = accruals[recent] if recent >= 0 else ZERO
    next_share = accruals[older] if older >= 0 else ZERO
    return total((multiply(main, D(30 - remaining) / 30), multiply(next_share, D(remaining) / 30)))


def forecast(data, scenario, include_unconfirmed=False):
    fixed = data.fixed.pick(scenario, expense=True)
    opening = data.opening.pick(scenario)
    unit_economics, sales, expenses, capped = [], [], [], set()
    for offer in data.offers:
        price = offer.price.pick(scenario)
        if offer.commission_percent is not None:
            price = multiply(price, offer.commission_percent.pick(scenario))
            price = None if price is None else price / 100
        variable = offer.variable.pick(scenario, expense=True)
        first, last, capacity = (x.pick(scenario) for x in (offer.first, offer.last, offer.capacity))
        contribution = difference(price, variable)
        break_even = None
        if len(data.offers) == 1 and contribution is not None and contribution > 0 and fixed is not None:
            break_even = int((fixed / contribution).to_integral_value(rounding=ROUND_CEILING))
        unit_economics.append(dict(offer=offer.label, unit=offer.unit, price=presented(price),
            variable_cost=presented(variable), contribution=presented(contribution),
            contribution_margin_percent=presented(share(contribution, price)), break_even_units=break_even,
            break_even_exceeds_capacity=break_even is not None and capacity is not None and break_even > capacity))
        units = []
        for index in range(12):
            amount = first if index == 0 else last if index == 11 else (
                None if first is None or last is None else (first + (last - first) * D(index) / 11)
            )
            if amount is not None and capacity is not None and amount > capacity:
                capped.add(offer.label)
                amount = capacity
            units.append(None if amount is None else amount.to_integral_value(rounding=ROUND_HALF_UP))
        sales.append([multiply(price, v) for v in units])
        expenses.append([multiply(variable, v) for v in units])
    revenue = [total(values) for values in zip(*sales)]
    variable_costs = [total(values) for values in zip(*expenses)]
    rows, balances = [], []
    for index in range(12):
        events = [item for item in data.events if item.month == index + 1]
        receipts = collect_cash(revenue, data.receipts_days, index)
        payments = collect_cash(variable_costs, data.supplier_days, index)
        funding = total(item.amount.pick(scenario) for item in events
                        if item.kind in ("confirmed funding", "other inflow")
                        or (include_unconfirmed and item.kind == "unconfirmed funding"))
        other_spend = total(item.amount.pick(scenario, expense=True) for item in events
                            if item.kind in ("startup", "other outflow"))
        net_cash = difference(total((receipts, funding)), total((payments, fixed, other_spend)))
        closing = total((opening, net_cash))
        balances.append(closing)
        rows.append({key: presented(value) for key, value in dict(
            month=index + 1, opening_cash=opening, revenue=revenue[index], variable_cost=variable_costs[index],
            fixed_cost=fixed, operating_result=difference(revenue[index], total((variable_costs[index], fixed))),
            customer_receipts=receipts, variable_payments=payments, funding_and_other_inflows=funding,
            setup_and_other_payments=other_spend, cash_change=net_cash, closing_cash=closing).items()})
        opening = closing
    complete = all(value is not None for value in balances)
    minimum = min([data.opening.pick(scenario), *balances]) if complete else None
    return dict(rows=rows, unit_economics=unit_economics, capacity_limits=sorted(capped),
                lowest_cash=presented(minimum), funding_gap=presented(None if minimum is None else max(ZERO, -minimum)),
                annual_revenue=presented(total(revenue)))


def reviewed_competitors(peers):
    from collections import defaultdict

    facts, margins = [], []
    by_period = defaultdict(dict)
    for i, fact in enumerate(peers, 1):
        row = fact.model_dump(mode="json")
        row["id"] = f"F{i}"
        facts.append(row)
        if (fact.scope in ("standalone", "consolidated") and fact.period_start and fact.currency
                and fact.status in ("reported", "derived")):
            key = (fact.entity_id or fact.company, fact.period_start, fact.period_end, fact.scope,
                   fact.currency, fact.unit)
            by_period[key].setdefault(fact.metric, []).append((row["id"], fact.amount))
    for group in by_period.values():
        revenue = group.get("turnover", [])
        if len(revenue) != 1 or revenue[0][1] <= 0:
            continue
        for metric, label in (("gross_profit", "Gross"), ("operating_profit", "Operating"), ("net_profit", "Net")):
            values = group.get(metric, [])
            if len(values) == 1:
                ids, profit = [revenue[0][0], values[0][0]], values[0][1]
            elif metric == "gross_profit" and not values and len(group.get("cost_of_sales", [])) == 1:
                cost = group["cost_of_sales"][0]
                if cost[1] < 0:
                    continue
                ids, profit = [revenue[0][0], cost[0]], revenue[0][1] - cost[1]
            else:
                continue
            margins.append(dict(label=label, percent=presented(share(profit, revenue[0][1])), source_ids=ids))
    return facts, margins


def build_exhibits(data):
    scenarios = {"base": forecast(data, "base")}
    numbers = [data.fixed, data.opening, *(event.amount for event in data.events)]
    missing = []
    for offer in data.offers:
        fields = {"selling price": offer.price, "early sales": offer.first, "month 12 sales": offer.last,
                  "variable cost": offer.variable, "capacity": offer.capacity}
        if offer.commission_percent is not None:
            fields["commission percentage"] = offer.commission_percent
        numbers.extend(fields.values())
        missing.extend(f"{offer.label}: {label}" for label, amount in fields.items() if amount.status == "unknown")
    if any(x.high is not None for x in numbers):
        for name in ("cautious", "optimistic"):
            scenarios[name] = forecast(data, name)
    if any(event.kind == "unconfirmed funding" for event in data.events):
        scenarios["base_with_unconfirmed_funding"] = forecast(data, "base", True)
    for label, absent in (
        ("Monthly fixed costs", data.fixed.status == "unknown"),
        ("Starting cash", data.opening.status == "unknown"),
        ("Customer payment delay", data.receipts_days is None),
        ("Supplier payment delay", data.supplier_days is None),
        ("Reviewed competitor financials", not data.peers),
    ):
        if absent:
            missing.append(label)
    missing.extend(f"Cash amount for {item.label}" for item in data.events if item.amount.status == "unknown")
    facts, margins = reviewed_competitors(data.peers)
    input_statuses = [
        {"label": "Monthly fixed costs", **data.fixed.model_dump(mode="json")},
        {"label": "Starting cash", **data.opening.model_dump(mode="json")},
    ]
    for offer in data.offers:
        fields = offer.model_dump(mode="json", exclude={"label", "unit"}, exclude_none=True)
        input_statuses.extend({"label": f"{offer.label}: {key.replace('_', ' ')}", **value}
                              for key, value in fields.items())
    input_statuses.extend({"label": event.label, **event.amount.model_dump(mode="json")}
                          for event in data.events)
    assumptions = [
        "Known early and month-twelve sales volumes are interpolated linearly and capped at stated capacity. Seasonality and churn notes are qualitative.",
        "Cash timing uses thirty-day months with sales and variable costs spread evenly during each month. Fixed costs are paid as incurred.",
        "Month one begins at the launch or planning date. Opening receivables, existing payables, tax, debt service, and inventory timing only enter the cash forecast through explicitly scheduled cash items.",
        "Variable cash payments follow the variable-cost input and supplier delay. Schedule only inventory adjustments beyond those payments to avoid counting a purchase twice.",
        "Operating result is before interest, tax, and one-time cash items. Contribution margin uses the provided per-sale variable cost. Single-offer break-even uses positive contribution and monthly fixed costs.",
        "Range midpoints drive the base case; favorable and unfavorable bounds drive the alternatives. Unknowns remain blank.",
    ]
    return dict(currency=data.currency, scenarios=scenarios, competitor_figures=facts, competitor_margins=margins,
                missing=missing, assumptions=assumptions, input_statuses=input_statuses)

