"""Small, decimal-based viability calculators. Unknown inputs stay unknown."""
from decimal import Decimal as D, ROUND_CEILING, ROUND_HALF_UP, localcontext

from pydantic import Field, model_validator

from .plan_financials import Amount, Input


LABELS = {
    "price": "Selling price per paid unit", "volume": "Paid units per month",
    "variable": "Cost per paid unit", "fixed": "Monthly running costs excluding owner's pay",
    "owner_pay": "Monthly owner's pay", "startup": "One-time startup spending",
    "capital": "Available starting cash", "reserve": "Cash reserve to keep aside",
    "target_profit": "Desired monthly profit after owner's pay", "capacity": "Maximum paid units per month",
}


class ViabilityInputs(Input):
    currency: str = Field("INR", pattern=r"^[A-Z]{3}$")
    unit: str = Field("orders", min_length=1, max_length=60)
    price: Amount = Field(default_factory=Amount)
    volume: Amount = Field(default_factory=Amount)
    variable: Amount = Field(default_factory=Amount)
    fixed: Amount = Field(default_factory=Amount)
    owner_pay: Amount = Field(default_factory=Amount)
    startup: Amount = Field(default_factory=Amount)
    capital: Amount = Field(default_factory=Amount)
    reserve: Amount = Field(default_factory=Amount)
    target_profit: Amount = Field(default_factory=Amount)
    capacity: Amount = Field(default_factory=Amount)

    @model_validator(mode="after")
    def whole_units(self):
        for name in ("volume", "capacity"):
            if any(x is not None and x % 1 for x in (getattr(self, name).low, getattr(self, name).high)):
                raise ValueError("Sales volume and capacity must be whole numbers.")
        # N/A has a defined zero meaning for costs, but cannot establish sales or available cash.
        for name in ("price", "volume", "capital", "capacity"):
            if getattr(self, name).status == "na":
                raise ValueError(f"Use Unknown or an explicit value for {LABELS[name]}.")
        return self


def money(value):
    return None if value is None else str(value.quantize(D("0.01"), rounding=ROUND_HALF_UP))


def calculate(data: ViabilityInputs):
    # Large but valid inputs and very small margins must not exhaust Decimal's
    # default precision when ratios or currency amounts are rounded.
    with localcontext() as context:
        context.prec = 80
        return _calculate(data)


def _calculate(data: ViabilityInputs):
    """Single-offer steady-state scenarios, not a time-based cash forecast."""
    inputs = [{"id": f"U{i}", "field": key, "label": label,
               **getattr(data, key).model_dump(mode="json")} for i, (key, label) in enumerate(LABELS.items(), 1)]
    ids = {row["field"]: row["id"] for row in inputs}
    figures, scenarios = [], {}
    for scenario in ("cautious", "base", "optimistic"):
        values = {key: getattr(data, key).pick(scenario, expense=key in
                  {"variable", "fixed", "owner_pay", "startup", "reserve", "target_profit"}) for key in LABELS}
        # Integer volume at the midpoint must also represent whole paid units.
        for key in ("volume", "capacity"):
            if values[key] is not None:
                values[key] = values[key].to_integral_value(rounding="ROUND_FLOOR")
        def all_known(*keys):
            return all(values[k] is not None for k in keys)
        price, variable, volume = (values[k] for k in ("price", "variable", "volume"))
        actual_volume = min(volume, values["capacity"]) if all_known("volume", "capacity") else volume
        contribution = price - variable if all_known("price", "variable") else None
        running = values["fixed"] + values["owner_pay"] if all_known("fixed", "owner_pay") else None
        revenue = price * actual_volume if all_known("price", "volume") else None
        profit = contribution * actual_volume - running if contribution is not None and actual_volume is not None and running is not None else None
        break_even = ((running / contribution).to_integral_value(rounding=ROUND_CEILING)
                      if running is not None and contribution is not None and contribution > 0 else None)
        target = (((running + values["target_profit"]) / contribution).to_integral_value(rounding=ROUND_CEILING)
                  if running is not None and values["target_profit"] is not None and contribution is not None and contribution > 0 else None)
        available = max(D(0), values["capital"] - values["startup"] - values["reserve"]) if all_known("capital", "startup", "reserve") else None
        gap = max(D(0), values["startup"] + values["reserve"] - values["capital"]) if all_known("capital", "startup", "reserve") else None
        metrics = [
            ("revenue", "Monthly sales income", revenue, "money", ["price", "volume", "capacity"], "selling price × monthly paid units, capped at capacity when supplied"),
            ("contribution", "Amount left per sale before monthly costs", contribution, "money", ["price", "variable"], "selling price − cost per paid unit"),
            ("contribution_margin", "Share of each sale left before monthly costs", contribution / price * 100 if contribution is not None and price > 0 else None, "percent", ["price", "variable"], "amount left per sale ÷ selling price × 100"),
            ("profit", "Monthly operating profit after owner's pay", profit, "money", ["price", "variable", "volume", "capacity", "fixed", "owner_pay"], "amount left per sale × paid units − running costs − owner's pay"),
            ("profit_margin", "Operating profit as a share of sales", profit / revenue * 100 if profit is not None and revenue and revenue > 0 else None, "percent", ["price", "variable", "volume", "capacity", "fixed", "owner_pay"], "monthly operating profit ÷ sales income × 100"),
            ("break_even", "Monthly sales needed to cover costs and owner's pay", break_even, "units", ["price", "variable", "fixed", "owner_pay"], "round up (running costs + owner's pay) ÷ amount left per sale; requires positive contribution"),
            ("target_sales", "Monthly sales needed to reach your profit target", target, "units", ["price", "variable", "fixed", "owner_pay", "target_profit"], "round up (running costs + owner's pay + profit target) ÷ amount left per sale"),
            ("funding_gap", "Extra cash needed for startup and stated reserve", gap, "money", ["startup", "reserve", "capital"], "maximum of zero and (startup spending + reserve − starting cash)"),
            ("runway", "Months the remaining cash could cover a steady monthly loss", available / -profit if available is not None and profit is not None and profit < 0 and gap == 0 else None, "months", ["capital", "startup", "reserve", "price", "variable", "volume", "capacity", "fixed", "owner_pay"], "cash left after startup and reserve ÷ monthly loss; assumes immediate payment"),
            ("payback", "Months of steady operating profit to recover startup spending", values["startup"] / profit if values["startup"] is not None and profit is not None and profit > 0 else None, "months", ["startup", "price", "variable", "volume", "capacity", "fixed", "owner_pay"], "startup spending ÷ positive monthly operating profit; a simple indicator, not a cash payback forecast"),
        ]
        rows = []
        for key, label, value, unit, fields, formula in metrics:
            row = {"id": f"C{len(figures) + 1}", "scenario": scenario, "metric": key, "label": label,
                   "value": int(value) if value is not None and unit == "units" else money(value), "unit": unit,
                   "source_ids": [ids[k] for k in fields], "formula": formula,
                   "status": "unknown" if value is None else "calculated from user inputs"}
            figures.append(row)
            rows.append(row)
        flags = []
        if contribution is not None and contribution <= 0:
            flags.append("Each sale leaves no money to cover monthly running costs at these inputs.")
        if values["capacity"] is not None and break_even is not None and break_even > values["capacity"]:
            flags.append("Sales needed to cover costs exceed your stated capacity.")
        if volume is not None and values["capacity"] is not None and volume > values["capacity"]:
            flags.append("The scenario's sales were capped at your stated capacity.")
        scenarios[scenario] = {"figures": rows, "flags": flags}
    missing = [row["label"] for row in inputs if row["status"] == "unknown"]
    base_profit = next(row["value"] for row in scenarios["base"]["figures"] if row["metric"] == "profit")
    verdict = ("Insufficient cost or sales inputs" if base_profit is None else
               "The expected scenario loses money" if D(base_profit) < 0 else
               "The expected scenario covers costs exactly" if D(base_profit) == 0 else
               "The expected scenario leaves an operating profit; demand still needs testing")
    return {"currency": data.currency, "unit": data.unit, "inputs": inputs, "figures": figures,
            "scenarios": scenarios, "missing": missing, "economic_signal": verdict,
            "assumptions": [
                "These are single-offer monthly scenarios based on your inputs, not predictions or probabilities. Base uses range midpoints; cautious uses lower prices and sales and higher costs; optimistic reverses them.",
                "Running costs exclude owner's pay, which is subtracted separately. Per-sale costs must include delivery, fees, returns and other costs that rise with each sale.",
                "Operating profit is before interest and tax. Cash indicators assume immediate receipts and payments, with no ramp-up, seasonality, inventory timing or debt repayments. Use the Business Plan for a monthly cash forecast.",
                "Unknown inputs remain unknown. Enter zero or Not applicable only when a cost really does not apply. Capacity is applied only when provided.",
            ]}
