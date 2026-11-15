const byId = (id) => document.getElementById(id);

function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content !== undefined && content !== null) node.textContent = content;
  if (className) node.className = className;
  return node;
}

function website(value) {
  if (!value || /\s/.test(value)) return null;
  try {
    const url = new URL(/^[a-z][a-z\d+.-]*:/i.test(value) ? value : "https://" + value);
    if (!["https:", "http:"].includes(url.protocol) || !url.hostname.includes(".") || url.username || url.password) return null;
    const link = element("a", "Website", "link");
    link.href = url.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  } catch {
    return null;
  }
}

function contactLink(value, scheme) {
  const clean = scheme === "tel" ? value.replace(/[().\s-]/g, "") : value;
  const valid = scheme === "tel" ? /^\+?\d{5,20}$/.test(clean) : /^[^@\s?#&]+@[^@\s?#&]+\.[^@\s?#&]+$/.test(clean);
  if (!valid) return element("span", value);
  const link = element("a", value);
  link.href = scheme === "mailto" ? "mailto:" + encodeURIComponent(clean).replace("%40", "@") : "tel:" + clean;
  return link;
}

function addField(list, label, value) {
  if (value === undefined || value === null || value === "") return;
  const field = element("div");
  const content = element("dd");
  if (value instanceof Node) content.append(value);
  else content.textContent = String(value);
  field.append(element("dt", label), content);
  list.append(field);
}

function investorCard(row, workbook) {
  const card = element("article", null, "record investor-card");
  const head = element("div", null, "record-top");
  const main = element("div");
  main.append(element("h3", row.contact || row.firm || "Angel investor"));
  const firm = row.contact && row.firm !== row.contact ? row.firm : null;
  main.append(element("p", [row.title, firm].filter(Boolean).join(" · "), "identity"));
  head.append(main);
  if (row.website) head.append(website(row.website) || element("span", row.website, "identity"));
  card.append(head);
  const sectorList = element("ul", null, "investor-match-tags");
  sectorList.setAttribute("aria-label", "Matched sectors");
  for (const label of row.matching_sectors) sectorList.append(element("li", label));
  card.append(sectorList);
  if (row.description) card.append(element("p", row.description, "investor-description"));

  const details = element("details", null, "details investor-details");
  details.append(element("summary", "Contact and workbook details"));
  const fields = element("dl", null, "investor-fields");
  addField(fields, "Email", row.email && contactLink(row.email, "mailto"));
  addField(fields, "Phone", row.phone && contactLink(String(row.phone), "tel"));
  addField(fields, "Email format status", row.email_format);
  addField(fields, "DNS MX status from workbook", row.email_mx);
  addField(fields, "Email verification notes", row.email_notes);
  addField(fields, "Total investments", row.total_investments);
  addField(fields, "Active investments", row.active_investments);
  addField(fields, "Dry powder ($M)", row.dry_powder_millions);
  addField(fields, "All listed sectors", row.sectors.join(", "));
  addField(fields, "Notes", row.notes);
  addField(fields, "Additional notes", row.notes_2);
  addField(fields, "Original source file", row.source_file);
  addField(fields, "Workbook reference", workbook + " · " + row.sheet + " · row " + row.row);
  details.append(fields);
  card.append(details);
  return card;
}

async function request(path, options) {
  const response = await fetch(path, { ...options, cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The investor directory could not be loaded.");
  return data;
}

export function initializeInvestors() {
  const form = byId("investor-form");
  const idea = byId("investor-idea");
  const ideationIdea = byId("idea");
  const copy = byId("investor-copy-idea");
  const submit = byId("investor-submit");
  const sectorList = byId("investor-sectors");
  const query = byId("investor-query");
  const filter = byId("investor-filter");
  const status = byId("investor-status");
  const sectorInfo = byId("investor-sector-info");
  const results = byId("investor-results");
  const pagination = byId("investor-pagination");
  const previous = byId("investor-prev");
  const next = byId("investor-next");
  let directory;
  let opening;
  let activeRequest;
  let searched;
  let data;
  let edited = false;
  let manualSectors = false;

  function showStatus(message) {
    status.textContent = message || "";
    status.hidden = !message;
  }

  function applyIdea() {
    const value = ideationIdea.value.trim();
    if (!value) return;
    idea.value = value;
    edited = false;
    manualSectors = false;
    if (directory) find(1);
  }

  function render(data, moveFocus) {
    results.replaceChildren();
    pagination.hidden = !data.total;
    filter.hidden = data.selected_sectors.length === 0;
    const matchedList = data.selected_sectors.join(", ");
    const suggestions = data.suggestions.map(({ sector, terms }) => sector + " (" + terms.join(", ") + ")");
    sectorInfo.textContent = matchedList ? "Showing matches in " + matchedList + ". " : "";
    if (suggestions.length) sectorInfo.append("Idea signals include " + suggestions.join("; ") + ".");
    else if (matchedList) sectorInfo.append("Sectors selected manually.");
    sectorInfo.hidden = !sectorInfo.textContent;
    for (const item of sectorList.querySelectorAll("input")) item.checked = data.selected_sectors.includes(item.value);
    if (!data.total) {
      if (!matchedList) byId("investor-sector-panel").open = true;
      showStatus(matchedList ? "No angel investors match these sectors and filters." : "Enter a more specific idea or choose a sector from the workbook.");
      return;
    }
    const start = (data.page - 1) * data.page_size + 1;
    const end = start + data.rows.length - 1;
    showStatus("Showing " + start + "–" + end + " of " + data.total.toLocaleString() + " angel investor records.");
    for (const row of data.rows) results.append(investorCard(row, data.workbook));
    previous.disabled = data.page <= 1;
    next.disabled = end >= data.total;
    if (moveFocus) {
      status.focus({ preventScroll: true });
      status.scrollIntoView({ block: "start" });
    }
  }

  async function find(page, useLastSearch = false) {
    activeRequest?.abort();
    const controller = new AbortController();
    activeRequest = controller;
    const values = useLastSearch && searched ? searched : {
      idea: idea.value.trim(),
      sectors: manualSectors ? [...sectorList.querySelectorAll("input:checked")].map((item) => item.value) : null,
      query: query.value.trim(),
    };
    results.setAttribute("aria-busy", "true");
    previous.disabled = next.disabled = true;
    if (!useLastSearch) {
      results.replaceChildren();
      pagination.hidden = true;
      sectorInfo.hidden = true;
    }
    showStatus("Searching the investor workbook…");
    try {
      const output = await request("/investors/search", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
        body: JSON.stringify({ ...values, page }),
      });
      if (controller.signal.aborted) return;
      searched = values;
      data = output;
      render(output, useLastSearch);
    } catch (error) {
      if (!controller.signal.aborted) {
        showStatus(error.message);
        if (useLastSearch && data) {
          previous.disabled = data.page <= 1;
          next.disabled = data.page * data.page_size >= data.total;
        }
      }
    } finally {
      if (!controller.signal.aborted) results.setAttribute("aria-busy", "false");
    }
  }

  ideationIdea.addEventListener("input", () => { copy.hidden = !ideationIdea.value.trim(); });
  idea.addEventListener("input", () => { edited = true; manualSectors = false; });
  sectorList.addEventListener("change", () => { manualSectors = true; edited = true; });
  copy.addEventListener("click", applyIdea);
  form.addEventListener("submit", (event) => { event.preventDefault(); find(1); });
  previous.addEventListener("click", () => find(data.page - 1, true));
  next.addEventListener("click", () => find(data.page + 1, true));

  return async function openInvestors() {
    copy.hidden = !ideationIdea.value.trim();
    if (!directory) {
      opening ||= request("/investors/sectors");
      try {
        const catalog = await opening;
        if (!directory) {
          directory = catalog;
          for (const sector of directory.sectors) {
            const label = element("label");
            const input = element("input");
            input.type = "checkbox";
            input.value = sector.name;
            label.append(input, element("span", sector.name), element("small", sector.count.toLocaleString()));
            sectorList.append(label);
          }
          submit.disabled = false;
          byId("investor-source-label").textContent = "Source " + directory.workbook +
            " · " + directory.total_records.toLocaleString() + " records with Angel Investor titles.";
        }
      } catch (error) {
        showStatus(error.message);
        return;
      } finally {
        opening = null;
      }
    }
    if (!edited && ideationIdea.value.trim() && idea.value !== ideationIdea.value.trim()) applyIdea();
  };
}
