const byId = (id) => document.getElementById(id);

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function externalLink(text, href) {
  if (!href) return null;
  try {
    const url = new URL(href);
    if (!["https:", "http:"].includes(url.protocol)) return null;
    const anchor = node("a", text);
    anchor.href = url.href;
    anchor.rel = "noopener noreferrer";
    anchor.target = "_blank";
    return anchor;
  } catch {
    return null;
  }
}

function status(element, text) {
  element.textContent = text || "";
  element.hidden = !text;
}

async function request(url, signal) {
  const response = await fetch(url, { signal });
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "The lookup could not be completed.");
  return payload;
}

function renderRows(parent, rows) {
  const step = 15;
  const list = node("ul", undefined, "loan-providers");
  let rendered = 0;
  const more = node("button", undefined, "chip");
  more.type = "button";
  function appendRows() {
    const end = Math.min(rendered + step, rows.length);
    for (const row of rows.slice(rendered, end)) {
      const item = node("li");
      const name = externalLink(row.name, row.url) || node("span", row.name);
      item.append(node("strong"));
      item.firstChild.append(name);
      if (row.source_url) {
        const sourceLink = externalLink("View source", row.source_url);
        if (sourceLink) item.append(" · ", sourceLink);
      }
      const details = Object.entries(row.fields || {}).filter(([, value]) => value);
      if (details.length) {
        const meta = node("dl", undefined, "loan-provider-details");
        for (const [label, value] of details) {
          const entry = node("div");
          entry.append(node("dt", label), node("dd", value));
          meta.append(entry);
        }
        item.append(meta);
      }
      list.append(item);
    }
    rendered = end;
    more.textContent = "Show " + Math.min(rows.length - rendered, step) + " more";
    more.hidden = rendered === rows.length;
  }
  more.addEventListener("click", appendRows);
  parent.append(list, more);
  appendRows();
}

function sourceCard(source) {
  const element = node("article", undefined, "record loan-source-card");
  const top = node("div", undefined, "loan-source-title");
  const heading = node("h3", source.name);
  const links = node("div", undefined, "loan-source-links");
  const anchor = externalLink("Open original source", source.url);
  if (anchor) links.append(anchor);
  top.append(heading, links);
  const caption = node("p", source.coverage + " · " + source.type, "loan-caption");
  const note = node("p", source.note, "loan-source-note");
  const body = node("div");
  status(body, "Loading directory…");
  body.setAttribute("role", "status");
  element.append(top, caption, note, body);
  return { element, body, links };
}

export function initializeLoans() {
  const countries = new Map();
  const moreCountry = byId("loans-more-country");
  const fintableCountry = byId("loans-fintable-country");
  const loanType = byId("loans-kind");
  const moreOutput = byId("loans-more-results");
  const moreNote = byId("loans-more-status");
  const fintableNote = byId("loans-fintable-status");
  const fintableOutput = byId("loans-fintable-results");
  const fintableForm = byId("loans-fintable-form");
  const next = byId("loans-fintable-next");
  const tabs = [...document.querySelectorAll("#loans [role=tab]")];
  let moreRequest, fintableRequest;
  let fintablePage = 1;
  let lastFintableQuery = "";

  function selectTab(tab) {
    for (const button of tabs) {
      const selected = button === tab;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      byId(button.getAttribute("aria-controls")).hidden = !selected;
    }
  }

  for (const tab of tabs) {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const target = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs.at(-1) : tabs.find((item) => item !== tab);
      selectTab(target);
      target.focus();
    });
  }

  async function loadMoreSources() {
    moreRequest?.abort();
    moreOutput.replaceChildren();
    const country = countries.get(moreCountry.value);
    if (!country) {
      status(moreNote, "Choose a country to load its directories.");
      return;
    }
    const controller = new AbortController();
    moreRequest = controller;
    const kind = loanType.value;
    status(moreNote, "Checking " + country.sources.length + (country.sources.length === 1 ? " directory in " : " directories in ") + country.name + "…");
    let loaded = 0;
    await Promise.all(country.sources.map(async (source) => {
      const card = sourceCard(source);
      moreOutput.append(card.element);
      try {
        const url = "/loans/sources/" + country.code + "/" + source.id + "?kind=" + kind;
        const results = await request(url, controller.signal);
        if (controller.signal.aborted) return;
        card.body.replaceChildren();
        if (results.rows.length) {
          loaded += 1;
          const data = results.data_url !== source.url && externalLink("View result source", results.data_url);
          if (data) card.links.append(data);
          card.body.removeAttribute("role");
          card.body.append(node("p", results.rows.length + (results.rows.length === 1 ? " record" : " records") +
            " shown · " + results.method, "loan-caption"));
          renderRows(card.body, results.rows);
        } else {
          status(card.body, results.message || "No provider records were returned.");
        }
      } catch (error) {
        if (!controller.signal.aborted) status(card.body, error.message);
      }
    }));
    if (!controller.signal.aborted) {
      status(moreNote, loaded ? null : "No provider records loaded for this selection. The source links below can be opened directly.");
    }
  }

  async function loadFintable(page, query) {
    fintableRequest?.abort();
    const country = countries.get(fintableCountry.value);
    next.hidden = true;
    if (!country) {
      fintableOutput.replaceChildren();
      status(fintableNote, "Choose a country to look up institutions.");
      return;
    }
    const controller = new AbortController();
    fintableRequest = controller;
    if (page === 1) fintableOutput.replaceChildren();
    status(fintableNote, "Searching Fintable…");
    try {
      const params = new URLSearchParams({ q: query, page });
      const output = await request("/loans/fintable/" + country.code + "?" + params, controller.signal);
      if (controller.signal.aborted) return;
      if (output.rows.length) {
        renderRows(fintableOutput, output.rows);
        status(fintableNote, null);
      } else {
        status(fintableNote, page === 1 ? "No matching institutions for this country on this page." : "This page has no additional matching institutions.");
      }
      fintablePage = page;
      next.hidden = !output.has_more || page >= 25;
    } catch (error) {
      if (!controller.signal.aborted) status(fintableNote, error.message);
    }
  }

  moreCountry.addEventListener("change", loadMoreSources);
  loanType.addEventListener("change", loadMoreSources);
  fintableForm.addEventListener("submit", (event) => {
    event.preventDefault();
    lastFintableQuery = byId("loans-fintable-query").value.trim();
    loadFintable(1, lastFintableQuery);
  });
  fintableCountry.addEventListener("change", () => fintableForm.requestSubmit());
  next.addEventListener("click", () => loadFintable(fintablePage + 1, lastFintableQuery));

  request("/loans/countries").then((data) => {
    for (const country of data) {
      countries.set(country.code, country);
      for (const select of [moreCountry, fintableCountry]) {
        const option = node("option", country.name);
        option.value = country.code;
        select.append(option);
      }
    }
    moreCountry.disabled = fintableCountry.disabled = false;
    byId("loans-fintable-submit").disabled = false;
  }).catch((error) => {
    status(moreNote, error.message);
    status(fintableNote, error.message);
  });
}
