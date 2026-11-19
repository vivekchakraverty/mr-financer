const byId = id => document.getElementById(id);
const fields = {
  price: "Selling price per paid unit", volume: "Paid units sold per month", variable: "Cost per paid unit",
  fixed: "Monthly running costs excluding your pay", owner_pay: "Your monthly pay",
  startup: "One-time startup spending", capital: "Available starting cash", reserve: "Cash reserve to keep aside",
  target_profit: "Desired monthly profit after your pay", capacity: "Maximum paid units you can deliver per month",
};
let searchId, revision = 0, active, ready = false;
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function note(text) { byId("viability-status").textContent = text; }
function clearResult() {
  revision += 1; active?.abort(); active = null; ready = false;
  byId("viability-calculations").replaceChildren(); byId("viability-brief").replaceChildren();
  byId("viability-generate").hidden = true;
  byId("viability-calculate").disabled = false; byId("viability-draft").disabled = false;
}
export function setViabilitySearch(id) {
  searchId = id; clearResult(); byId("viability").hidden = false;
  // Assumptions from a previous idea must not carry over to a new search.
  for (const key of Object.keys(fields)) {
    byId("viability-" + key + "-status").value = "unknown";
    byId("viability-" + key + "-low").value = ""; byId("viability-" + key + "-high").value = "";
    syncField(key);
  }
  byId("viability-geo").textContent = "The brief uses the target market recorded for this search. Re-run search after changing the idea or location.";
  note("Research company websites and fetch financial records to add more evidence, then calculate your scenarios.");
}
export function invalidateViability(message = "New company research is available. Recalculate to include it.") {
  if (searchId) { clearResult(); note(message); }
}
function syncField(key) {
  const status = byId("viability-" + key + "-status").value;
  const low = byId("viability-" + key + "-low"), high = byId("viability-" + key + "-high");
  low.disabled = !["actual", "estimate"].includes(status); low.required = !low.disabled;
  high.disabled = status !== "estimate"; high.parentElement.hidden = status !== "estimate";
  if (low.disabled) low.value = ""; if (high.disabled) high.value = "";
}
function payload() {
  const financials = {currency: byId("viability-currency").value.trim().toUpperCase(), unit: byId("viability-unit").value.trim()};
  for (const key of Object.keys(fields)) {
    const status = byId("viability-" + key + "-status").value;
    const low = byId("viability-" + key + "-low").value, high = byId("viability-" + key + "-high").value;
    financials[key] = {status, low: low === "" ? null : low, high: high === "" ? null : high};
  }
  return {search_id: searchId, financials, mode: byId("viability-mode").value};
}
async function call(path, body, token, signal) {
  const response = await fetch(path, {method: "POST", cache: "no-store", signal,
    headers: {"Content-Type": "application/json", ...(token ? {Authorization: "Bearer " + token} : {})}, body: JSON.stringify(body)});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The brief request could not complete.");
  return data;
}
function sourceLink(id, text) {
  const a = el("a", text || "[" + id + "]"); a.href = "#viability-source-" + id;
  a.addEventListener("click", event => {
    event.preventDefault();
    const target = byId("viability-source-" + id);
    for (let node = target; node; node = node.parentElement) if (node.tagName === "DETAILS") node.open = true;
    setTimeout(() => target?.scrollIntoView({block: "center"}), 0);
  });
  return a;
}
function citedText(text) {
  const paragraph = el("p");
  for (const part of text.split(/(\[[EBCU][1-9][0-9]*\])/g)) {
    paragraph.append(/^\[[EBCU][1-9][0-9]*\]$/.test(part) ? sourceLink(part.slice(1, -1), part) : document.createTextNode(part));
  }
  return paragraph;
}
function sourceRecord(source) {
  const container = el("div");
  const format = value => {
    if (Array.isArray(value)) return value.map(format).join("; ");
    if (value && typeof value === "object") return Object.entries(value).map(([key, item]) => key.replaceAll("_", " ") + ": " + format(item)).join("; ");
    return value == null ? "Not stated" : String(value);
  };
  try {
    const record = JSON.parse(source.text);
    if (source.kind === "calculation") {
      container.append(el("p", record.value == null ? "Result: unavailable with the supplied inputs or not applicable." :
        "Result: " + record.value + " " + (record.unit === "money" ? record.currency : record.unit)));
      container.append(el("p", "Method: " + record.formula));
      const refs = el("p", "Uses your inputs: ");
      for (const id of record.source_ids) refs.append(sourceLink(id), " ");
      container.append(refs);
    } else {
      for (const [key, value] of Object.entries(record)) {
        if (["id", "field", "label"].includes(key)) continue;
        container.append(el("p", key.replaceAll("_", " ") + ": " + format(value)));
      }
    }
  } catch { container.append(el("p", source.text)); }
  return container;
}
function figures(data) {
  const out = byId("viability-calculations"); out.replaceChildren();
  const {exhibits: x, packet} = data; byId("viability-geo").textContent = packet.geography_notice;
  const signal = el("article", null, "record");
  signal.append(el("h3", x.economic_signal), el("p", "These scenarios use your inputs. They do not establish whether enough customers will buy.")); out.append(signal);
  const wrap = el("div", null, "viability-table-wrap"), table = el("table", null, "viability-table");
  table.append(el("caption", "Monthly scenarios · " + x.currency + " · paid unit: " + x.unit));
  const head = table.createTHead().insertRow();
  for (const name of ["Calculation", "Cautious", "Expected", "Optimistic"]) { const th = el("th", name); th.scope = "col"; head.append(th); }
  const body = table.createTBody();
  for (const [index, metric] of x.scenarios.base.figures.entries()) {
    const row = body.insertRow(), th = el("th", metric.label); th.scope = "row"; row.append(th);
    for (const scenario of ["cautious", "base", "optimistic"]) {
      const figure = x.scenarios[scenario].figures[index]; let value = "Unknown / not applicable";
      if (figure.value != null) value = (figure.unit === "money" ? x.currency + " " : "") + figure.value +
        (figure.unit === "percent" ? "%" : figure.unit === "units" ? " " + x.unit : figure.unit === "months" ? " months" : "");
      row.insertCell().append(sourceLink(figure.id, value));
    }
  }
  wrap.append(table); out.append(el("p", "Scroll the table sideways if needed to compare all scenarios.", "notice"), wrap);
  for (const [scenario, entry] of Object.entries(x.scenarios)) for (const flag of entry.flags) out.append(el("p", scenario + ": " + flag, "notice"));
  const limitations = el("details", null, "details"); limitations.append(el("summary", "Assumptions and missing inputs"));
  for (const text of x.assumptions) limitations.append(el("p", text));
  if (x.missing.length) limitations.append(el("p", "Missing: " + x.missing.join("; "))); out.append(limitations);
  const refs = el("details", null, "details viability-sources"); refs.append(el("summary", "Evidence, guidance and calculation sources"));
  refs.append(el("p", `Selected ${packet.retrieval.retrieved_evidence_chunks} source excerpts from ${packet.retrieval.candidate_chunks} available excerpts. Websites researched: ${packet.retrieval.websites_reviewed}. Financial companies reviewed: ${packet.retrieval.financial_companies_reviewed}.`));
  refs.append(el("p", "Citations show supporting records and methods. They do not verify every interpretation made by the model. Guidance was reviewed on " + packet.retrieval.guidance_reviewed + "."));
  for (const source of packet.citations) {
    const item = el("article", null, "record"); item.id = "viability-source-" + source.id;
    item.append(el("h4", "[" + source.id + "] " + source.title), el("p", source.kind + " · " + source.geography),
      el("p", "Retrieved / recorded: " + source.retrieved_at), sourceRecord(source));
    if (source.url) {
      try {
        const url = new URL(source.url);
        if (["http:", "https:"].includes(url.protocol)) {
          const link = el("a", "Open original source"); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; item.append(link);
        }
      } catch { /* A source remains readable as a local record. */ }
    }
    refs.append(item);
  }
  if (packet.retrieval.source_coverage.length) refs.append(el("p", "Search coverage: " + packet.retrieval.source_coverage.join("; "))); out.append(refs);
}
export function initializeViability() {
  for (const [key, name] of Object.entries(fields)) {
    const row = el("fieldset", null, "viability-input"); row.append(el("legend", name));
    const state = el("select"); state.id = "viability-" + key + "-status";
    for (const [value, text] of [["unknown", "Unknown"], ["estimate", "Estimate"], ["actual", "Actual / known"], ...(["price", "volume", "capital", "capacity"].includes(key) ? [] : [["na", "Not applicable (zero)"]])]) {
      const option = el("option", text); option.value = value; state.append(option);
    }
    const stateLabel = el("label", "Evidence status"); stateLabel.append(state); row.append(stateLabel);
    for (const [suffix, text] of [["low", "Value / low estimate"], ["high", "High estimate (optional)"]]) {
      const label = el("label", text), input = el("input"); input.id = "viability-" + key + "-" + suffix;
      input.type = "number"; input.min = "0"; input.max = "1000000000000"; input.step = ["volume", "capacity"].includes(key) ? "1" : "0.0001";
      label.append(input); row.append(label);
    }
    byId("viability-inputs").append(row); syncField(key); state.addEventListener("change", () => syncField(key));
  }
  byId("viability-form").addEventListener("input", () => { clearResult(); note("Inputs changed. Calculate again before creating a brief."); });
  document.addEventListener("viability-evidence-updated", () => invalidateViability());
  byId("viability-form").addEventListener("submit", async event => {
    event.preventDefault(); if (!searchId) return;
    clearResult(); const current = revision; active = new AbortController();
    byId("viability-calculate").disabled = true; note("Calculating your scenarios and retrieving relevant evidence…");
    try {
      const result = await call("/idea/viability/preview", payload(), null, active.signal);
      if (current !== revision) return;
      figures(result); ready = true; byId("viability-generate").hidden = false; note("Calculations and retrieved sources are ready. You can now create the brief.");
    } catch (error) { if (current === revision && error.name !== "AbortError") note(error.message); }
    finally { if (current === revision) byId("viability-calculate").disabled = false; }
  });
  byId("viability-draft").addEventListener("click", async () => {
    if (!ready) return;
    const current = revision; active?.abort(); active = new AbortController();
    const token = byId("viability-token").value.trim(); byId("viability-token").value = "";
    byId("viability-draft").disabled = true; byId("viability-brief").replaceChildren();
    note(byId("viability-mode").value === "zerogpu" ? "Creating the brief on ZeroGPU using your account's quota…" : "Creating the brief on CPU. This can take several minutes…");
    try {
      const result = await call("/idea/viability/brief", payload(), token, active.signal);
      if (current !== revision) return;
      figures(result);
      for (const section of result.sections) {
        const article = el("article", null, "record"); article.append(el("h3", section.heading));
        if (section.basis === "general guidance; market evidence missing") article.append(el("p", "General guidance · market evidence missing", "notice"));
        if (section.basis === "calculated scenario from your inputs") article.append(el("p", "Calculated from your inputs", "notice"));
        if (section.basis === "sourced business guidance") article.append(el("p", "Sourced business guidance", "notice"));
        article.append(citedText(section.text)); byId("viability-brief").append(article);
      }
      byId("viability-brief").prepend(el("p", "Draft model: " + result.model)); note("Brief ready. Review its sources, assumptions and geographic coverage before deciding.");
    } catch (error) { if (current === revision && error.name !== "AbortError") note(error.message); }
    finally { if (current === revision) byId("viability-draft").disabled = false; }
  });
}
