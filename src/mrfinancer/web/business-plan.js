const byId = id => document.getElementById(id);
const metrics = {turnover: "Revenue", cost_of_sales: "Cost of sales", gross_profit: "Gross profit",
  operating_profit: "Operating profit", net_profit: "Net profit", profit_before_tax: "Profit before tax",
  cash: "Cash", debt: "Debt", net_assets: "Equity", total_assets: "Assets",
  total_liabilities: "Liabilities", employees: "Employees", funding: "Funding"};
const reviewed = new Map();
let lastIdea = "", lastMarket = "";

function el(tag, text, className) {
  const item = document.createElement(tag);
  if (text != null) item.textContent = text;
  if (className) item.className = className;
  return item;
}

function select(options) {
  const field = el("select");
  for (const value of options) {
    const [id, label] = Array.isArray(value) ? value : [value, value];
    const option = el("option", label);
    option.value = id;
    field.append(option);
  }
  return field;
}

function field(parent, name, caption, config = {}) {
  let input = config.options ? select(config.options) : el(config.long ? "textarea" : "input");
  if (input.tagName === "INPUT") input.type = config.type || "text";
  if (config.long) input.rows = 2;
  if (config.max) input.maxLength = config.max;
  if (config.value) input.value = config.value;
  if (config.placeholder) input.placeholder = config.placeholder;
  if (config.required) input.required = true;
  if (config.pattern) input.pattern = config.pattern;
  if (config.type === "number") {
    input.min = config.min ?? 0;
    input.max = config.limit ?? 1000000000000;
    input.step = config.step || "1";
  }
  input.dataset.field = name;
  const label = el("label", caption);
  label.append(input);
  parent.append(label);
  return input;
}

function value(parent, name) {
  return parent.querySelector('[data-field="' + name + '"]').value.trim();
}

function amount(parent, name, label, integer = false) {
  const group = el("div", null, "plan-amount");
  group.dataset.amount = name;
  group.append(el("strong", label));
  const status = field(group, "status", "Value status", {
    options: [["unknown", "I don’t know"], ["actual", "Actual"], ["estimate", "Estimate"], ["na", "Doesn’t apply"]],
  });
  const bounds = el("div", null, "plan-range");
  const low = field(bounds, "low", "Amount or low end", {type: "number", step: integer ? "1" : "0.0001"});
  const high = field(bounds, "high", "High end (optional)", {type: "number", step: integer ? "1" : "0.0001"});
  const update = () => {
    bounds.hidden = !["actual", "estimate"].includes(status.value);
    low.disabled = bounds.hidden;
    low.required = !bounds.hidden;
    high.parentElement.hidden = status.value !== "estimate";
    high.disabled = high.parentElement.hidden;
  };
  status.addEventListener("change", update);
  update();
  group.append(bounds);
  parent.append(group);
}

function amounts(parent) {
  return Object.fromEntries([...parent.querySelectorAll("[data-amount]")].map(group => {
    const status = value(group, "status");
    return [group.dataset.amount, {status,
      low: ["actual", "estimate"].includes(status) ? value(group, "low") || null : null,
      high: status === "estimate" ? value(group, "high") || null : null}];
  }));
}

function removable(parent, heading, removable = true) {
  const box = el("div", null, "plan-entry");
  const top = el("div", null, "plan-entry-head");
  top.append(el("strong", heading));
  if (removable) {
    const button = el("button", "Remove", "link");
    button.type = "button";
    button.addEventListener("click", () => box.remove());
    top.append(button);
  }
  box.append(top);
  const grid = el("div", null, "plan-grid");
  box.append(grid);
  parent.append(box);
  return grid;
}

function addOffer(first = false) {
  if (byId("plan-offers").children.length >= 4) return note("Use up to four offers.");
  const row = removable(byId("plan-offers"), first ? "Main offer" : "Additional offer", !first);
  field(row, "label", "What do you sell?", {value: first ? "Main offer" : "", max: 100, required: true});
  const unit = field(row, "unit", "How will you get paid?", {
    options: [["orders", "Per order"], ["jobs", "Per job"], ["active subscriptions", "Monthly subscription"],
      ["transactions", "Commission"], ["other paid units", "Another paid unit"]],
  });
  amount(row, "price", "9. Price or typical transaction value");
  amount(row, "commission_percent", "Commission percentage");
  amount(row, "first", "10. Paid units in an early month", true);
  amount(row, "last", "Paid units by month twelve", true);
  amount(row, "variable", "11. Cost to deliver one paid unit");
  amount(row, "capacity", "16. Maximum paid units you can deliver each month", true);
  const commission = row.querySelector('[data-amount="commission_percent"]');
  const update = () => {
    commission.hidden = unit.value !== "transactions";
    for (const item of commission.querySelectorAll("input, select")) {
      const status = value(commission, "status");
      item.disabled = commission.hidden || item.dataset.field === "low" && !["actual", "estimate"].includes(status)
        || item.dataset.field === "high" && status !== "estimate";
    }
  };
  unit.addEventListener("change", update);
  update();
}

function addEvent() {
  if (byId("plan-events").children.length >= 20) return note("Use up to twenty cash items.");
  const row = removable(byId("plan-events"), "Cash item");
  field(row, "label", "What is it?", {max: 100, required: true});
  field(row, "kind", "Type", {options: ["startup", "confirmed funding", "unconfirmed funding", "other inflow", "other outflow"]});
  field(row, "month", "Month paid or received", {type: "number", min: 1, limit: 12, value: "1", required: true});
  amount(row, "amount", "Amount");
}

function addManual() {
  if (byId("plan-manual").children.length >= 24) return note("Use up to twenty-four figures.");
  const row = removable(byId("plan-manual"), "Competitor figure");
  field(row, "company", "Legal company name", {required: true, max: 200});
  field(row, "entity_id", "Registry ID including country", {max: 100});
  field(row, "fit", "Why is it comparable?", {max: 280});
  field(row, "metric", "Metric", {options: Object.entries(metrics)});
  field(row, "amount", "Reported amount in full units", {type: "number", min: -10000000000000000, limit: 10000000000000000, step: "0.0001", required: true});
  field(row, "currency", "Currency code", {max: 3, pattern: "[A-Za-z]{3}"});
  field(row, "period_start", "Reporting period start", {type: "date"});
  field(row, "period_end", "Reporting period end", {type: "date", required: true});
  field(row, "scope", "Reporting scope", {options: ["unknown", "standalone", "consolidated", "segment"]});
  field(row, "status", "Evidence status", {options: ["unverified", "reported", "estimated", "derived"]});
  field(row, "source", "Filing or source name", {max: 180});
  field(row, "url", "Filing or report link", {type: "url", max: 2083});
  field(row, "locator", "Page, table or cell", {max: 250});
}

function link(label, href) {
  try {
    const url = new URL(href);
    if (!["https:", "http:"].includes(url.protocol)) return el("span", label);
    const item = el("a", label);
    item.href = url.href; item.target = "_blank"; item.rel = "noopener noreferrer";
    return item;
  } catch {
    return el("span", label);
  }
}

export function updatePlanIdea(idea, market) {
  const area = byId("plan-questions");
  for (const [key, previous, incoming] of [["idea", lastIdea, idea], ["market", lastMarket, market]]) {
    const input = area.querySelector('[data-field="' + key + '"]');
    if (!input.value || input.value === previous) input.value = incoming;
  }
  lastIdea = idea; lastMarket = market;
}

export function updatePlanPeers(reviews) {
  const previous = new Set([...reviewed].filter(([, row]) => row.input.checked).map(([key]) => key));
  reviewed.clear();
  const list = byId("plan-peers");
  list.replaceChildren();
  for (const {profile} of reviews) {
    if (!profile.company) continue;
    const company = profile.company.name;
    const entity_id = [profile.company.jurisdiction, profile.company.registry_id || company].join(":");
    const details = el("details", null, "details");
    details.append(el("summary", company));
    for (const fact of profile.financials) {
      if (!metrics[fact.metric] || fact.value == null || !fact.period_end || !fact.currency && fact.metric !== "employees") continue;
      const input = el("input");
      input.type = "checkbox";
      const key = JSON.stringify([entity_id, fact.metric, fact.value, fact.period_start, fact.period_end,
        fact.statement_scope, fact.currency, fact.provenance.url, fact.provenance.note]);
      if (reviewed.has(key)) continue;
      input.checked = previous.has(key);
      const status = fact.as_filed && fact.provenance.url ? "reported" : fact.provenance.trust === "estimate" ? "estimated" : "unverified";
      const row = el("div", null, "plan-peer");
      const label = el("label");
      label.append(input, " " + metrics[fact.metric] + " · " + [fact.currency, fact.value].filter(v => v != null).join(" ") +
        " · " + [fact.period_start, fact.period_end].filter(Boolean).join(" to ") + " · " +
        (fact.statement_scope || "scope unavailable") + " · " + status);
      row.append(label, link(fact.provenance.source_name, fact.provenance.url));
      if (fact.issues.length) row.append(el("small", fact.issues.map(issue => issue.message).join(" ")));
      details.append(row);
      reviewed.set(key, {input, fact: {company, entity_id, metric: fact.metric, amount: String(fact.value),
        currency: fact.currency || null, unit: fact.unit || "", period_start: fact.period_start,
        period_end: fact.period_end, scope: fact.statement_scope || "unknown", url: fact.provenance.url || null,
        source: fact.provenance.source_name, locator: (fact.provenance.note || "").slice(0, 250), status}});
    }
    if (details.children.length > 1) list.append(details);
  }
  byId("plan-no-peers").hidden = reviewed.size > 0;
}

export function businessPlanAnswers() {
  const questions = byId("plan-questions");
  const cash = byId("plan-cash");
  const answers = {};
  for (const key of ["idea", "customer", "market", "currency", "stage", "launch", "alternative", "channels", "team_goal", "audience", "notes", "recent"]) {
    answers[key] = value(questions, key);
  }
  answers.currency = answers.currency.toUpperCase();
  if (answers.stage !== "already selling") answers.recent = "";
  const offers = [...byId("plan-offers").querySelectorAll(".plan-grid")].map(row => {
    const unit = value(row, "unit");
    const fields = amounts(row);
    if (unit !== "transactions") delete fields.commission_percent;
    return {label: value(row, "label"), unit, ...fields};
  });
  const events = [...byId("plan-events").querySelectorAll(".plan-grid")].map(row => ({
    label: value(row, "label"), kind: value(row, "kind"), month: Number(value(row, "month")), amount: amounts(row).amount,
  }));
  const peers = [...reviewed.values()].filter(row => row.input.checked).map(row => row.fact);
  for (const row of byId("plan-manual").querySelectorAll(".plan-grid")) {
    const fact = {};
    for (const key of ["company", "entity_id", "fit", "metric", "amount", "currency", "period_start", "period_end", "scope", "status", "source", "url", "locator"]) fact[key] = value(row, key);
    fact.currency = fact.currency.toUpperCase() || null;
    fact.period_start ||= null;
    fact.url ||= null;
    fact.unit = fact.metric === "employees" ? "count" : "";
    peers.push(fact);
  }
  const timing = name => value(cash, name) === "" ? null : Number(value(cash, name));
  return {...answers, offers, events, peers, ...amounts(cash),
    receipts_days: timing("receipts_days"), supplier_days: timing("supplier_days")};
}

function note(message) {
  const box = byId("plan-note");
  box.textContent = message;
  box.hidden = !message;
}

function table(parent, headers, rows) {
  const wrap = el("div", null, "table-scroll");
  const node = el("table");
  const head = el("thead"), top = el("tr"), body = el("tbody");
  for (const title of headers) {
    const th = el("th", title); th.scope = "col"; top.append(th);
  }
  head.append(top);
  for (const values of rows) {
    const row = el("tr");
    for (const value of values) {
      const cell = el("td");
      if (value instanceof Node) cell.append(value);
      else cell.textContent = value ?? "Unavailable";
      row.append(cell);
    }
    body.append(row);
  }
  node.append(head, body); wrap.append(node); parent.append(wrap);
}

function render(result) {
  const output = byId("plan-output");
  output.replaceChildren();
  for (const section of result.sections || []) {
    const card = el("article", null, "record");
    card.append(el("h3", section.heading), el("p", section.text));
    output.append(card);
  }
  const x = result.exhibits;
  for (const [name, scenario] of Object.entries(x.scenarios)) {
    const details = el("details", null, "record");
    details.open = name === "base";
    details.append(el("summary", name.replaceAll("_", " ") + " forecast · " + x.currency));
    for (const [caption, key] of [["Annual revenue", "annual_revenue"], ["Lowest cash balance", "lowest_cash"], ["Funding gap", "funding_gap"]]) {
      details.append(el("p", caption + " · " + (scenario[key] ?? "Unavailable")));
    }
    if (scenario.capacity_limits.length) details.append(el("p", "Forecast capped at stated capacity for " + scenario.capacity_limits.join(", ") + "."));
    table(details, ["Offer", "Price", "Variable cost", "Contribution", "Contribution margin %", "Break-even units"],
      scenario.unit_economics.map(r => [r.offer, r.price, r.variable_cost, r.contribution, r.contribution_margin_percent,
        r.break_even_units == null ? null : r.break_even_units + (r.break_even_exceeds_capacity ? " (above capacity)" : "")]));
    const cols = {month: "Month", opening_cash: "Opening cash", revenue: "Sales", variable_cost: "Variable costs",
      fixed_cost: "Fixed costs", operating_result: "Operating result", customer_receipts: "Cash receipts",
      variable_payments: "Supplier payments", funding_and_other_inflows: "Other cash in",
      setup_and_other_payments: "Other cash out", cash_change: "Cash change", closing_cash: "Closing cash"};
    table(details, Object.values(cols), scenario.rows.map(row => Object.keys(cols).map(key => row[key])));
    output.append(details);
  }
  if (x.input_statuses?.length) {
    const details = el("details", null, "record");
    details.append(el("summary", "Forecast inputs and confidence"));
    const status = {actual: "Actual", estimate: "Estimate", unknown: "Unknown", na: "Doesn’t apply"};
    table(details, ["Input", "Status", "Entered value"],
      x.input_statuses.map(row => [row.label, status[row.status], [row.low, row.high].filter(v => v !== null).join(" to ") || "—"]));
    output.append(details);
  }
  const peers = el("article", null, "record");
  peers.append(el("h3", "Reviewed competitor financials and filings"));
  if (x.competitor_figures.length) {
    table(peers, ["ID", "Company", "Metric", "Figure", "Period", "Scope", "Evidence", "Reference"],
      x.competitor_figures.map(f => [f.id, f.company, metrics[f.metric],
        [f.currency, f.amount, f.unit].filter(Boolean).join(" "), [f.period_start, f.period_end].filter(Boolean).join(" to "),
        f.scope, f.status, link([f.source || "Filing", f.locator].filter(Boolean).join(" · "), f.url)]));
  } else peers.append(el("p", "No reviewed competitor figures were included."));
  if (x.competitor_margins.length) {
    table(peers, ["Comparable margin", "Percent", "Filing figures"],
      x.competitor_margins.map(row => [row.label, row.percent, row.source_ids.join(", ")]));
  }
  for (const [title, values] of [["Missing information", x.missing], ["Forecast assumptions", x.assumptions]]) {
    if (!values.length) continue;
    const card = el("article", null, "record");
    const list = el("ul");
    for (const line of values) list.append(el("li", line));
    card.append(el("h3", title), list);
    output.append(card);
  }
  output.append(peers);
}

export function initializePlan() {
  const questions = el("fieldset");
  const grid = el("div", null, "plan-grid");
  questions.append(el("legend", "Your business idea"), grid);
  byId("plan-questions").append(questions);
  field(grid, "idea", "1. What are you selling, and what problem does it solve?", {long: true, max: 2000, required: true});
  field(grid, "customer", "2. Who uses it and who pays?", {long: true, max: 800});
  field(grid, "market", "3. Where will you start selling?", {max: 300});
  field(grid, "currency", "Which currency should the plan use?", {max: 3, required: true, pattern: "[A-Za-z]{3}", placeholder: "INR, USD, GBP"});
  const stage = field(grid, "stage", "4. How far along are you?", {options: ["idea", "testing", "about to launch", "already selling"]});
  field(grid, "launch", "Expected launch or expansion month", {max: 100});
  field(grid, "alternative", "5. What do customers use now, and why would they choose you?", {long: true, max: 800});
  field(grid, "channels", "6. How will you reach your first customers?", {long: true, max: 800});
  field(grid, "team_goal", "7. Who will run it, and what do you want to achieve in a year?", {long: true, max: 1200});
  field(grid, "audience", "8. Who is this plan for?", {options: ["founder or team", "lender", "investor", "grant application", "other"]});
  field(grid, "notes", "Seasonality, subscription cancellations or other assumptions", {long: true, max: 1200});
  const recent = field(grid, "recent", "Recent revenue, volume and spending, with the period covered", {long: true, max: 1200});
  recent.parentElement.hidden = true;
  stage.addEventListener("change", () => { recent.parentElement.hidden = stage.value !== "already selling"; });
  addOffer(true);
  const cash = byId("plan-cash");
  amount(cash, "fixed", "12. Regular monthly bills");
  amount(cash, "opening", "14. Available cash at the start");
  field(cash, "receipts_days", "15. Days until customers pay", {type: "number", limit: 365, placeholder: "0 for immediate payment"});
  field(cash, "supplier_days", "Days until you pay suppliers", {type: "number", limit: 365, placeholder: "0 for immediate payment"});
  byId("plan-add-offer").addEventListener("click", () => addOffer());
  byId("plan-add-event").addEventListener("click", addEvent);
  byId("plan-add-peer").addEventListener("click", addManual);
  byId("plan-form").addEventListener("submit", async event => {
    event.preventDefault();
    const preview = event.submitter?.value === "preview";
    const tokenField = byId("plan-token");
    const token = tokenField.value.trim();
    if (!preview && !token) return note("Enter your Hugging Face access token to generate the plan.");
    const buttons = [...event.currentTarget.querySelectorAll('button[type="submit"]')];
    buttons.forEach(button => { button.disabled = true; });
    note(preview ? "Calculating your forecast." : "Checking your Hugging Face account and drafting the plan. This may take a few minutes.");
    try {
      const response = await fetch("/business-plan/" + (preview ? "preview" : "draft"), {
        method: "POST", headers: {"Content-Type": "application/json", ...(!preview && {Authorization: "Bearer " + token})},
        body: JSON.stringify(businessPlanAnswers()),
      });
      const result = await response.json();
      if (!preview && response.ok && result.ok) tokenField.value = "";
      if (!response.ok || !result.ok) throw new Error(result.error?.message || result.detail || "The plan request could not be completed.");
      render(result);
      note(result.warning || (preview ? "Financial preview ready." : "Business plan ready."));
    } catch (error) {
      note(error.message || "The plan request could not be completed.");
    } finally {
      buttons.forEach(button => { button.disabled = false; });
    }
  });
}

