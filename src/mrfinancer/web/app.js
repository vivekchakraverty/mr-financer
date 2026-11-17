import { initializePlan, updatePlanIdea, updatePlanPeers } from "./business-plan.js";
import { openCompanyResearch } from "./company-research.js";
import { initializePitchDeck } from "./pitch-deck.js";
import { initializeLoans } from "./loans.js";
import { initializeInvestors } from "./investors.js";
import { initializeNavigation } from "./navigation.js";
import { initializeIncorporation } from "./incorporation.js";
import { initializeLearn } from "./learn.js";
import { answerFinancialQuestion, balanceSheetCompositions, chartable, financialAmount, financialBasis,
  financialLabel, financialNumber, financialPercent, financialPeriod, financialQuestionExamples } from "./financial-analysis.js";

const byId = (id) => document.getElementById(id);
const state = { search: null, size: 10, selected: new Map(), searching: false, loading: false };
const reviewedForPlan = new Map();
initializePlan();
initializePitchDeck();
initializeLoans();
const countries = [["", "Automatic"], ["GB", "United Kingdom"], ["US", "United States"], ["FR", "France"],
  ["NO", "Norway"], ["SK", "Slovakia"], ["KR", "South Korea"], ["IN", "India"]];
const idLabels = { GB: "Company number", US: "SEC CIK", FR: "SIREN", NO: "Organisation number", SK: "IČO", KR: "DART corp_code" };
const reasons = {
  not_found: "No record found", ambiguous_match: "Company identity needs confirmation",
  requires_paid_document: "Requires paid access", jurisdiction_unsupported: "Registry not connected",
  credentials_missing: "API credentials needed", adapter_disabled: "Source disabled",
  rate_limited: "Source rate limit reached", failed_validation: "Figure withheld after validation",
  upstream_error: "Source could not complete the request", timeout: "Lookup timed out"
};

function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content !== undefined && content !== null) node.textContent = content;
  if (className) node.className = className;
  return node;
}

function link(label, href) {
  try {
    const url = new URL(href);
    if (!["http:", "https:"].includes(url.protocol)) return element("span", label);
    const a = element("a", label);
    a.href = url.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  } catch {
    return element("span", label);
  }
}

function note(id, message) {
  const field = byId(id);
  field.textContent = message || "";
  field.hidden = !message;
}

async function request(url, body) {
  const options = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const response = await fetch(url, options);
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(typeof result.detail === "string" ? result.detail : "The request couldn’t be completed. Try again.");
  }
  return response.json();
}

function evidenceLinks(product) {
  const refs = element("div", null, "evidence-links");
  const records = product.evidence.length ? product.evidence : [product.provenance];
  const used = new Set();
  for (const source of records) {
    if (source.url && !used.has(source.url)) {
      refs.append(link(source.source_name, source.url));
      used.add(source.url);
    }
  }
  if (product.homepage && !used.has(product.homepage)) refs.append(link("Website", product.homepage));
  return refs;
}

function selectionChanged() {
  byId("selection-count").textContent = state.selected.size ? state.selected.size + " selected" : "Choose firms to review";
  byId("finance-action").disabled = !state.selected.size || state.loading || state.searching;
  const area = byId("finance-selection");
  area.replaceChildren();
  if (!state.selected.size) return;
  for (const [index, item] of [...state.selected.entries()].sort((a, b) => a[0] - b[0])) {
    const row = element("details", null, "selection-row");
    row.append(element("summary", state.search.matches[index].name));
    const grid = element("div", null, "identity-grid");
    const name = element("input");
    name.maxLength = 200;
    name.value = item.legal_name || "";
    name.placeholder = "Optional";
    name.addEventListener("input", () => { item.legal_name = name.value.trim() || null; });
    const country = element("select");
    for (const [code, label] of countries) {
      const option = element("option", label);
      option.value = code;
      country.append(option);
    }
    country.value = item.jurisdiction || "";
    const number = element("input");
    number.maxLength = 24;
    number.value = item.registry_id || "";
    number.placeholder = idLabels[country.value] || "Choose a country";
    number.disabled = !idLabels[country.value];
    number.addEventListener("input", () => { item.registry_id = number.value.trim() || null; });
    country.addEventListener("change", () => {
      item.jurisdiction = country.value || null;
      number.disabled = !idLabels[country.value];
      number.placeholder = idLabels[country.value] || "Choose a country";
      if (!idLabels[country.value]) { number.value = ""; item.registry_id = null; }
    });
    for (const [labelText, control] of [["Registry country", country], ["Legal company name", name], ["Registry ID", number]]) {
      const label = element("label", labelText);
      label.append(control);
      grid.append(label);
    }
    row.append(grid);
    area.append(row);
  }
}

function showMatches() {
  const data = state.search;
  byId("results").hidden = false;
  const visible = data.matches.slice(0, state.size);
  const list = byId("match-list");
  list.replaceChildren();
  byId("results-title").textContent = data.direct_count ? "Direct matches found" : "Similar firms";
  const outcome = data.direct_count
    ? data.direct_count + (data.direct_count === 1 ? " offering closely matches" : " offerings closely match") + " the idea based on source evidence."
    : "No direct match surfaced in the sources searched.";
  byId("results-summary").textContent = outcome + (visible.length ? " Showing " + visible.length + " of " + data.matches.length + " firms." : " Try a more specific description or a different market.");
  byId("result-bar").hidden = !visible.length;

  for (const [index, product] of visible.entries()) {
    const row = element("article", null, "match");
    const select = element("input");
    select.type = "checkbox";
    select.checked = state.selected.has(index);
    select.setAttribute("aria-label", "Select " + product.name);
    select.addEventListener("change", () => {
      if (select.checked) state.selected.set(index, { index });
      else state.selected.delete(index);
      selectionChanged();
    });
    const body = element("div");
    body.append(element("h3", product.name));
    if (product.tagline || product.description) body.append(element("p", (product.tagline || product.description).slice(0, 330)));
    const identityHint = [product.signals.publisher, product.market].filter(Boolean).join(" Â· ");
    if (identityHint) body.append(element("p", identityHint, "reasons"));
    if (product.score_reasons.length) body.append(element("p", product.score_reasons.join(" · "), "reasons"));
    body.append(evidenceLinks(product));
    const actions = element("div", null, "match-actions");
    actions.append(element("span", product.match_type === "direct" ? "Direct match" : "Similar",
      product.match_type === "direct" ? "chip status-direct" : "chip"));
    const research = element("button", "Research company", "company-action");
    research.type = "button";
    research.setAttribute("aria-label", "Research company " + product.name);
    research.addEventListener("click", () => openCompanyResearch(data.search_id, index, product, research));
    actions.append(research);
    row.append(select, body, actions);
    list.append(row);
  }
  const runs = byId("source-runs");
  runs.replaceChildren();
  for (const source of data.sources) {
    const title = source.adapter.replaceAll("_", " ");
    runs.append(element("li", title + " — " + (source.reason || (source.results + " candidates returned"))));
  }
  const evidence = byId("evidence-list");
  evidence.replaceChildren();
  byId("other-evidence").hidden = !data.evidence.length;
  for (const product of data.evidence) {
    const row = element("li", product.name);
    row.append(evidenceLinks(product));
    evidence.append(row);
  }
  for (const button of document.querySelectorAll("[data-count]")) {
    button.setAttribute("aria-pressed", String(Number(button.dataset.count) === state.size));
  }
  selectionChanged();
}

function financialSource(container, fact) {
  container.append(link(fact.provenance.source_name, fact.provenance.url));
  if (fact.issues.length) {
    const details = element("details", null, "financial-checks");
    details.append(element("summary", "Validation notes"));
    for (const issue of fact.issues) details.append(element("div", issue.message));
    container.append(details);
  }
}

function financialPies(snapshots) {
  const section = element("section", null, "financial-composition");
  const picker = element("select");
  const info = element("div", null, "financial-chart-context financial-pie-info");
  const pies = element("div", null, "financial-pies");
  const heading = element("div", null, "financial-composition-heading");
  heading.append(element("h4", "Balance sheet composition"));
  if (snapshots.length > 1) {
    const label = element("label", "Balance sheet date");
    for (const { assets } of snapshots) {
      const context = [assets.period_end, assets.currency, assets.statement_scope || "Scope unstated", assets.provenance.source_name];
      picker.append(element("option", context.join(" · ")));
    }
    label.append(picker);
    heading.append(label);
  }
  section.append(heading, info, pies);

  function show({ assets, charts }) {
    const context = [assets.period_end, assets.statement_scope || "Scope unstated", `Total assets ${financialAmount(assets.value, assets)}`];
    info.replaceChildren(context.join(" · "), " · ", link(assets.provenance.source_name, assets.provenance.url));
    pies.replaceChildren();
    for (const { title, slices } of charts) {
      const figure = element("figure", null, "financial-pie");
      figure.append(element("figcaption", title));
      const disc = element("div", null, "financial-pie-disc");
      disc.setAttribute("aria-hidden", "true");
      disc.style.setProperty("--pie-share", `${Math.min(1, slices[0].value / assets.value) * 100}%`);
      const legend = element("ul", null, "financial-pie-legend");
      legend.setAttribute("role", "list");
      for (const slice of slices) {
        const row = element("li");
        row.append(element("span", slice.label), element("strong", financialPercent(slice.value / assets.value)),
          element("span", financialAmount(slice.value, assets, slice.calculated), "financial-pie-amount"));
        legend.append(row);
      }
      figure.append(disc, legend);
      pies.append(figure);
    }
  }

  picker.addEventListener("change", () => show(snapshots[picker.selectedIndex]));
  show(snapshots[0]);
  return section;
}

function financialQA(facts, snapshots) {
  const section = element("details", null, "details financial-qa");
  section.append(element("summary", "Ask about these figures"));
  const form = element("form");
  const label = element("label", "Question about this company");
  const question = element("input");
  question.required = true;
  question.maxLength = 180;
  question.autocomplete = "off";
  label.append(question);
  const submit = element("button", "Ask", "primary");
  submit.type = "submit";
  const controls = element("div", null, "financial-question-controls");
  controls.append(label, submit);
  const examples = element("div", null, "examples");
  for (const prompt of financialQuestionExamples(facts, snapshots)) {
    const example = element("button", prompt, "chip");
    example.type = "button";
    example.addEventListener("click", () => {
      question.value = prompt;
      form.requestSubmit();
    });
    examples.append(example);
  }
  const answer = element("div", null, "financial-answer");
  answer.hidden = true;
  answer.setAttribute("aria-live", "polite");
  form.append(controls, examples);
  section.append(form, answer);
  form.addEventListener("submit", event => {
    event.preventDefault();
    const { message, items } = answerFinancialQuestion(question.value, facts, snapshots);
    answer.hidden = false;
    answer.replaceChildren(element("h5", message));
    for (const { summary, facts: sources } of items) {
      if (summary) answer.append(element("strong", summary));
      const list = element("ul");
      for (const fact of sources) {
        const row = element("li");
        const context = [financialPeriod(fact), fact.statement_scope || "Scope unstated", financialBasis(fact)];
        row.append(element("div", `${financialLabel(fact.metric)} · ${financialAmount(fact.value, fact)}`),
          element("div", context.join(" · ")));
        const source = element("div", null, "financial-chart-source");
        financialSource(source, fact);
        row.append(source);
        list.append(row);
      }
      answer.append(list);
    }
  });
  return section;
}

function financialCharts(facts) {
  const groups = new Map();
  for (const fact of [...facts].reverse()) {
    const key = JSON.stringify([fact.metric, fact.currency, fact.unit, fact.statement_scope, financialBasis(fact)]);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(fact);
  }

  const grid = element("div", null, "financial-charts");
  const series = [...groups.values()].sort(([a], [b]) => financialLabel(a.metric).localeCompare(financialLabel(b.metric)));
  for (const group of series) {
    const first = group[0];
    const chart = element("figure", null, "financial-chart");
    const caption = element("figcaption");
    caption.append(element("h4", financialLabel(first.metric)), element("div", [
      first.currency || first.unit || "Unit unstated", first.statement_scope || "Scope unstated", financialBasis(first),
    ].join(" · "), "financial-chart-context"));
    chart.append(caption);

    const rows = element("ul", null, "financial-chart-rows");
    rows.setAttribute("role", "list");
    const values = group.map(fact => fact.value);
    const minimum = Math.min(0, ...values);
    const maximum = Math.max(0, ...values);
    const range = maximum - minimum || 1;
    const zero = -minimum / range;
    rows.style.setProperty("--chart-zero", `${zero * 100}%`);
    for (const fact of group) {
      const row = element("li", null, "financial-chart-row");
      const heading = element("div", null, "financial-chart-label");
      heading.append(element("span", financialPeriod(fact)), element("strong", financialNumber.format(fact.value)));
      const track = element("div", null, "financial-chart-track");
      track.setAttribute("aria-hidden", "true");
      const mark = element("div", null, "financial-chart-mark");
      mark.classList.toggle("is-negative", fact.value < 0);
      mark.classList.toggle("is-zero", fact.value === 0);
      mark.style.setProperty("--bar-start", `${(Math.min(fact.value, 0) - minimum) / range * 100}%`);
      mark.style.setProperty("--bar-width", `${Math.abs(fact.value) / range * 100}%`);
      track.append(mark);
      const source = element("div", null, "financial-chart-source");
      financialSource(source, fact);
      row.append(heading, track, source);
      rows.append(row);
    }
    chart.append(rows);
    grid.append(chart);
  }
  return grid;
}

function financialCard(review) {
  const { profile } = review;
  const facts = [...profile.financials].sort((a, b) => (b.period_end || "").localeCompare(a.period_end || "") ||
    (b.period_start || "").localeCompare(a.period_start || ""));
  const figures = facts.filter(chartable);
  const card = element("article", null, "record");
  const top = element("div", null, "record-top");
  const heading = element("div");
  heading.append(element("h3", profile.product.name));
  if (profile.company) {
    const company = profile.company;
    const line = element("p", null, "identity");
    line.append(link(company.name, company.provenance?.url), " · " + company.jurisdiction,
      company.registry_id ? " · " + company.registry_id : "");
    heading.append(line);
    if (review.identity_inputs !== "offering") heading.append(element("p", "Resolved using the company details you supplied.", "identity"));
  } else {
    heading.append(element("p", "Company identity could not be verified. Add its registry country and legal name or ID, then retry.", "identity"));
  }
  let outcome = "No records returned";
  if (figures.length) outcome = "Figures found";
  else if (profile.documents.length) outcome = "Filings found";
  else if (facts.length) outcome = "Figures unavailable";
  top.append(heading, element("span", outcome, "chip"));
  card.append(top);

  if (facts.length) {
    if (figures.length) {
      const snapshots = balanceSheetCompositions(figures);
      card.append(financialQA(figures, snapshots));
      if (snapshots.length) card.append(financialPies(snapshots));
      card.append(element("p", "Each chart uses its own scale.", "financial-scale-note"), financialCharts(figures));
    }
    const records = element("details", null, "details");
    records.open = !figures.length;
    records.append(element("summary", "Financial figures and sources"));
    const wrap = element("div", null, "table-scroll");
    const table = element("table");
    table.setAttribute("aria-label", "Financial figures for " + profile.product.name);
    const headers = element("tr");
    for (const name of ["Metric", "Reported amount", "Period", "Statement", "Source"]) {
      const cell = element("th", name); cell.scope = "col"; headers.append(cell);
    }
    const head = element("thead"); head.append(headers); table.append(head);
    const body = element("tbody");
    for (const fact of facts) {
      const row = element("tr");
      const amount = chartable(fact)
        ? [fact.currency || fact.unit, financialNumber.format(fact.value)].filter(Boolean).join(" ")
        : fact.issues.some(issue => issue.severity === "reject") ? "Withheld" : "Unavailable";
      const source = element("td");
      financialSource(source, fact);
      row.append(element("td", financialLabel(fact.metric)), element("td", amount), element("td", financialPeriod(fact)),
        element("td", [fact.statement_scope, financialBasis(fact)].filter(Boolean).join(" · ")), source);
      body.append(row);
    }
    table.append(body);
    wrap.append(table);
    records.append(wrap);
    card.append(records);
  }

  if (profile.documents.length) {
    const wrapper = element("details", null, "details");
    wrapper.open = !figures.length;
    wrapper.append(element("summary", "Source filings (" + profile.documents.length + ")"));
    const list = element("ul");
    for (const doc of profile.documents) {
      const row = element("li");
      row.append(link(doc.title, doc.provenance.url));
      if (doc.period_end) row.append(" · " + doc.period_end);
      if (doc.provenance.note) row.append(" · " + doc.provenance.note);
      list.append(row);
    }
    wrapper.append(list);
    card.append(wrapper);
  }

  if (profile.gaps.length) {
    const wrapper = element("details", null, "details");
    wrapper.open = !figures.length && !profile.documents.length;
    wrapper.append(element("summary", "Lookup notes"));
    const list = element("ul");
    for (const gap of profile.gaps) {
      const title = reasons[gap.reason] || gap.reason.replaceAll("_", " ");
      list.append(element("li", [title, gap.source?.replaceAll("_", " "), gap.detail].filter(Boolean).join(" · ")));
    }
    wrapper.append(list);
    card.append(wrapper);
  }
  return card;
}

for (const example of document.querySelectorAll("[data-idea]")) {
  example.addEventListener("click", () => {
    byId("idea").value = example.dataset.idea;
    byId("market").value = example.dataset.market || "";
    byId("idea").focus();
  });
}

for (const button of document.querySelectorAll("[data-count]")) {
  button.addEventListener("click", () => {
    if (!state.search) return;
    state.size = Number(button.dataset.count);
    showMatches();
  });
}

byId("select-visible").addEventListener("click", () => {
  for (let index = 0; index < Math.min(state.size, state.search.matches.length); index += 1) {
    if (!state.selected.has(index)) state.selected.set(index, { index });
  }
  showMatches();
});

byId("idea-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.searching || state.loading) return;
  state.searching = true;
  byId("search-action").disabled = true;
  byId("finance-action").disabled = true;
  note("search-note", "Searching available product and service sources…");
  try {
    state.search = await request("/idea/discover", { idea: byId("idea").value.trim(), location: byId("market").value.trim() || null });
    state.selected.clear();
    reviewedForPlan.clear();
    updatePlanPeers([]);
    updatePlanIdea(byId("idea").value.trim(), byId("market").value.trim());
    state.size = 10;
    byId("finance-results").replaceChildren();
    note("finance-note", "");
    showMatches();
    note("search-note", "");
    byId("results").scrollIntoView({ block: "start" });
  } catch (error) {
    note("search-note", error.message);
  } finally {
    state.searching = false;
    byId("search-action").disabled = false;
    byId("finance-action").disabled = !state.selected.size;
  }
});

byId("finance-action").addEventListener("click", async () => {
  if (!state.selected.size || state.loading || state.searching) return;
  const chosen = [...state.selected.values()].map((item) => ({ ...item }));
  const invalid = chosen.find((item) => item.legal_name && !item.jurisdiction);
  if (invalid) {
    note("finance-note", "Select a registry country for " + state.search.matches[invalid.index].name + ".");
    return;
  }
  state.loading = true;
  byId("search-action").disabled = true;
  byId("finance-action").disabled = true;
  const results = byId("finance-results");
  results.replaceChildren();
  let finished = 0;
  let failed = 0;
  note("finance-note", "Checking registries for " + chosen.length + " selected firms…");
  try {
    for (let offset = 0; offset < chosen.length; offset += 4) {
      const firms = chosen.slice(offset, offset + 4);
      try {
        const reviews = await request("/financials/review", { search_id: state.search.search_id, firms });
        for (const review of reviews) {
          results.append(financialCard(review));
          reviewedForPlan.set(review.index, review);
        }
        updatePlanPeers([...reviewedForPlan.values()]);
      } catch (error) {
        failed += firms.length;
        const group = element("article", null, "record");
        group.append(element("h3", firms.map((item) => state.search.matches[item.index].name).join(", ")),
          element("p", error.message));
        results.append(group);
      }
      finished += firms.length;
      note("finance-note", "Checked " + finished + " of " + chosen.length + " firms" + (failed ? " · " + failed + " lookups could not run." : "."));
    }
  } finally {
    state.loading = false;
    byId("search-action").disabled = false;
    byId("finance-action").disabled = !state.selected.size;
  }
});

let sourceLoading = false;
let sourcesLoaded = false;
async function showSources() {
  if (sourceLoading || sourcesLoaded) return;
  sourceLoading = true;
  const panel = byId("source-content");
  panel.textContent = "Loading source availability.";
  try {
    const [live, catalog] = await Promise.all([request("/sources"), request("/assets/sources.json")]);
    panel.replaceChildren();
    for (const [kind, heading] of [["discovery", "Idea search"], ["financial", "Financial records"]]) {
      panel.append(element("h3", heading));
      const list = element("ul");
      for (const item of live.filter((source) => source.kind === kind)) {
        const line = element("li");
        line.append(element("strong", item.name), " · " + (item.ready ? "Configured" : (item.reason || "Setup required")));
        list.append(line);
      }
      panel.append(list);
      const details = element("details", null, "details");
      details.append(element("summary", "Other sources in the research catalog"));
      const refs = element("ul");
      for (const source of catalog[kind]) {
        const row = element("li");
        row.append(link(source.name, source.url), " · No direct connector");
        refs.append(row);
      }
      details.append(refs);
      panel.append(details);
    }
    sourcesLoaded = true;
  } catch (error) {
    panel.textContent = error.message;
    const retry = element("button", "Try again", "chip");
    retry.type = "button";
    retry.addEventListener("click", showSources);
    panel.append(" ", retry);
  } finally {
    sourceLoading = false;
  }
}

initializeNavigation({
  showSources, showInvestors: initializeInvestors(), showIncorporation: initializeIncorporation(),
  showLearn: initializeLearn(),
});
