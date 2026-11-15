const byId = (id) => document.getElementById(id);

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function link(label, address) {
  try {
    const url = new URL(address);
    if (url.protocol !== "https:" || url.username || url.password) return element("span", label);
    const anchor = element("a", label);
    anchor.href = url.href;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    return anchor;
  } catch {
    return element("span", label);
  }
}

async function request(url, signal) {
  const response = await fetch(url, { signal });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "The source could not be loaded.");
  return result;
}

function sourceReference(source, section) {
  const line = element("p", null, "inc-reference");
  line.append(link(source.publisher + " · " + source.title, source.url));
  if (section) line.append(" · " + section);
  return line;
}

function sourceCard(source) {
  const card = element("details", null, "record inc-source");
  const summary = element("summary", source.title);
  const state = element("span", "Checking…", "inc-source-state");
  summary.append(state);
  const body = element("div");
  body.append(element("p", source.publisher + " · " + source.language + " · " + source.topics.join(", "), "inc-reference"),
    link("Open official source", source.url));
  const results = element("div");
  body.append(results);
  card.append(summary, body);
  return { card, state, results };
}

function excerptList(entries) {
  const list = element("ul", null, "inc-excerpts");
  for (const item of entries) {
    const row = element("li");
    row.append(link(item.title, item.url));
    if (item.text) row.append(element("p", item.text));
    list.append(row);
  }
  return list;
}

function renderLive(card, result) {
  const { state, results } = card;
  state.textContent = result.status === "ready" ? (result.method || "Available") :
    result.status === "no_matches" ? "No matching text" : "Unavailable";
  if (result.message) results.append(element("p", result.message));
  if (result.entries.length) {
    results.append(element("h4", result.method === "Published PDF" ? "PDF excerpts" : "Page excerpts"),
      excerptList(result.entries));
  }
  for (const search of result.searches) {
    const query = element("section", null, "inc-query");
    query.append(element("h4", search.topic), element("p", search.query, "inc-reference"));
    if (search.entries.length) {
      query.append(excerptList(search.entries));
    } else {
      query.append(element("p", search.status === "unavailable" ?
        "The publisher did not return results for this search." : "No relevant results could be extracted."));
    }
    if (search.url) query.append(link("Open search at the publisher", search.url));
    results.append(query);
  }
}

function renderGaps(gapPanel, listing, gaps) {
  listing.replaceChildren();
  gapPanel.hidden = !gaps.length;
  for (const gap of gaps) {
    const entry = element("li");
    entry.append(element("strong", gap.issue), element("p", gap.details),
      element("p", [gap.status, gap.topics].filter(Boolean).join(" · "), "inc-reference"));
    listing.append(entry);
  }
}

export function initializeIncorporation() {
  const countrySelect = byId("incorporation-country");
  const topicSelect = byId("incorporation-topic");
  const scope = byId("incorporation-scope");
  const note = byId("incorporation-status");
  const workbookPanel = byId("incorporation-workbook");
  const livePanel = byId("incorporation-live");
  const live = byId("incorporation-live-results");
  const liveStatus = byId("incorporation-live-status");
  const list = byId("incorporation-entries");
  const gaps = byId("incorporation-gaps");
  const gapList = byId("incorporation-gap-list");
  let catalog;
  let loadingCatalog;
  let country;
  let running;

  function renderEntries() {
    list.replaceChildren();
    if (!country) return;
    const sourceIndex = new Map(country.sources.map((source) => [source.id, source]));
    const topics = topicSelect.value ? [topicSelect.value] : catalog.topics;
    for (const topic of topics) {
      const matches = country.entries.filter((entry) => entry.topic === topic);
      if (!matches.length) continue;
      const group = element("section", null, "inc-topic");
      group.append(element("h3", topic));
      const cards = element("div", null, "financial-list");
      for (const entry of matches) {
        const card = element("article", null, "record inc-workbook-entry");
        card.append(element("p", entry.entity, "inc-entry-entity"),
          element("h4", entry.label), element("p", entry.summary));
        const source = sourceIndex.get(entry.source_id);
        if (source) {
          card.append(sourceReference(source, entry.section));
          if (entry.access === "Excerpt only") {
            card.append(element("p", "Workbook evidence from an excerpt · " + source.review_date, "inc-reference warning"));
          }
        }
        cards.append(card);
      }
      group.append(cards);
      list.append(group);
    }
    if (!list.childElementCount) list.append(element("p", "The workbook has no entries for this topic in the selected country.", "notice"));
  }

  async function pickCountry() {
    running?.abort();
    const code = countrySelect.value;
    workbookPanel.hidden = livePanel.hidden = true;
    scope.hidden = true;
    note.textContent = "Choose a country to load its requirements and sources.";
    country = null;
    if (!code) return;
    const controller = new AbortController();
    running = controller;
    note.textContent = "Loading country information…";
    try {
      const selected = await request("/incorporation/countries/" + code, controller.signal);
      if (controller.signal.aborted) return;
      country = selected;
      note.textContent = "";
      scope.textContent = selected.name + " · " + selected.scope;
      scope.hidden = false;
      renderEntries();
      renderGaps(gaps, gapList, selected.gaps);
      workbookPanel.hidden = livePanel.hidden = false;
      live.replaceChildren();
      let finished = 0;
      let available = 0;
      const count = selected.sources.length;
      liveStatus.textContent = "Checking " + count + " official sources…";
      await Promise.allSettled(selected.sources.map(async (source) => {
        const card = sourceCard(source);
        live.append(card.card);
        try {
          const result = await request("/incorporation/sources/" + code + "/" + encodeURIComponent(source.id), controller.signal);
          if (controller.signal.aborted) return;
          renderLive(card, result);
          if (result.status === "ready") available += 1;
        } catch (error) {
          if (controller.signal.aborted) return;
          card.state.textContent = "Unavailable";
          card.results.append(element("p", error.message));
        }
        finished += 1;
        liveStatus.textContent = finished < count ? "Checked " + finished + " of " + count + " official sources…" :
          available + " of " + count + " official sources returned readable results.";
      }));
    } catch (error) {
      if (!controller.signal.aborted) note.textContent = error.message;
    }
  }

  countrySelect.addEventListener("change", pickCountry);
  topicSelect.addEventListener("change", renderEntries);
  return async function showIncorporation() {
    if (catalog || loadingCatalog) return;
    loadingCatalog = true;
    try {
      catalog = await request("/incorporation/countries");
      for (const country of catalog.countries) {
        const option = element("option", country.name);
        option.value = country.code;
        countrySelect.append(option);
      }
      for (const topic of catalog.topics) {
        const option = element("option", topic);
        option.value = topic;
        topicSelect.append(option);
      }
      countrySelect.disabled = topicSelect.disabled = false;
      byId("incorporation-workbook-label").textContent = "Source " + catalog.workbook;
    } catch (error) {
      note.textContent = error.message;
    } finally {
      loadingCatalog = false;
    }
  };
}
