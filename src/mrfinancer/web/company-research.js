const dialog = document.querySelector("#company-research");
const title = document.querySelector("#company-research-title");
const site = document.querySelector("#company-research-site");
const body = document.querySelector("#company-research-content");
const status = document.querySelector("#company-research-status");
let active, opener, currentSearchId, currentIndex;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function safeUrl(value) {
  try {
    if (!value) return null;
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

function external(text, href) {
  const url = safeUrl(href);
  if (!url) return element("span", text);
  const a = element("a", text);
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

function message(text = "", error = false) {
  status.textContent = text;
  status.hidden = !text;
  status.classList.toggle("company-error", error);
}

function group(name, items, empty) {
  const section = element("section", null, "company-section");
  section.append(element("h3", name));
  if (!items.length) {
    section.append(element("p", empty));
    return section;
  }
  const list = element("ul", null, "company-list");
  for (const item of items) {
    const entry = element("li");
    const row = element("div", null, "company-item-heading");
    row.append(external(item.name, item.url));
    if (item.kind) row.append(element("span", item.kind, "company-item-kind"));
    entry.append(row);
    if (item.detail) entry.append(element("p", item.detail));
    if (name === "Products, services and pricing") {
      const details = element("div", null, "company-prices");
      for (const price of item.prices) details.append(external(price.label, price.source));
      if (!item.prices.length) details.append(element("span", "Price unavailable"));
      entry.append(details);
    }
    if (item.sources.length) {
      const refs = element("details", null, "company-sources");
      refs.append(element("summary", "Sources"));
      const links = element("ul");
      for (const url of item.sources) {
        const label = new URL(url).pathname;
        const li = element("li");
        li.append(external(label === "/" ? "Company homepage" : label, url));
        links.append(li);
      }
      refs.append(links);
      entry.append(refs);
    }
    list.append(entry);
  }
  section.append(list);
  return section;
}

function reviewCard(feed, review) {
  const li = element("li", null, "company-review");
  const meta = element("div", null, "company-review-meta");
  if (feed.scale === 5) meta.append(element("strong", review.rating + " / 5"));
  else meta.append(element("strong", review.rating === 0 ? "Not recommended" : "Recommended"));
  if (review.date) meta.append(element("span", review.date));
  li.append(meta);
  if (review.title && feed.scale !== 1) li.append(element("h5", review.title));
  if (review.text) li.append(element("p", review.text, "company-review-text"));
  const attribution = element("div", null, "company-review-meta");
  if (safeUrl(review.avatar)) {
    const avatar = element("img");
    avatar.src = safeUrl(review.avatar);
    avatar.alt = review.author ? review.author + " avatar" : "Reviewer avatar";
    avatar.loading = "lazy";
    avatar.referrerPolicy = "no-referrer";
    attribution.append(avatar);
  }
  if (review.author) attribution.append(external(review.author, review.author_url));
  if (review.url) attribution.append(external("View original review", review.url));
  if (review.report_link) attribution.append(external("Report review", review.report_link));
  if (review.visit_date) attribution.append(element("span", "Visited " + review.visit_date));
  if (review.translated) attribution.append(element("span", "Translated by Google"));
  li.append(attribution);
  return li;
}

function reviews(feeds) {
  const section = element("section", null, "company-section");
  section.append(element("h3", "Reviews"));
  const starReviews = [];
  const votes = [];
  for (const feed of feeds) {
    const provider = element("div", null, "company-provider");
    const name = element("h4");
    name.append(external(feed.name, feed.url));
    if (feed.name === "Google Maps") name.translate = false;
    provider.append(name, element("p", [feed.scope, feed.address].filter(Boolean).join(" · ")));
    if (feed.average != null) {
      provider.append(element("p", feed.average + " / 5 overall" +
        (feed.count != null ? " · " + feed.count.toLocaleString() + " ratings" : "")));
    }
    provider.append(element("p", feed.note));
    if (feed.status === "ok" && feed.reviews.length === 0) provider.append(element("p", "No reviews returned."));
    section.append(provider);
    for (const review of feed.reviews) {
      (feed.scale === 5 ? starReviews : votes).push({feed, review});
    }
  }
  for (const [label, entries] of [["Star ratings · lowest first", starReviews], ["Steam recommendations · negative first", votes]]) {
    if (!entries.length) continue;
    section.append(element("h4", label));
    const list = element("ul", null, "company-list");
    entries.sort((a, b) => a.review.rating - b.review.rating);
    for (const {feed, review} of entries) {
      const card = reviewCard(feed, review);
      const attribution = external(feed.name, feed.url);
      attribution.className = "company-review-source";
      if (feed.name === "Google Maps") attribution.translate = false;
      card.prepend(attribution);
      list.append(card);
    }
    section.append(list);
  }
  return section;
}

function g2Controls(searchId, index) {
  const section = element("section", null, "company-section company-g2");
  section.append(element("h3", "G2 product reviews"));
  section.append(element("p", "On-demand G2 lookup · maximum 3 actual G2 API calls per account per UTC calendar month. A matching product and its reviews normally require 2 calls. Opening this sidebar does not spend G2 calls."));
  const label = element("label", "Hugging Face personal token (account identification only)");
  label.setAttribute("for", "g2-hf-token");
  const tokenInput = element("input");
  tokenInput.type = "password";
  tokenInput.id = "g2-hf-token";
  tokenInput.autocomplete = "off";
  tokenInput.spellcheck = false;
  tokenInput.placeholder = "hf_…";
  tokenInput.maxLength = 2048;
  label.append(tokenInput);
  section.append(label);
  section.append(element("p", "Your Hugging Face token is checked only to identify your G2 monthly allowance. The shared G2 token stays on the server. No G2 calls occur before you press the button."));
  const button = element("button", "Fetch G2 reviews (up to 2 calls)", "chip");
  button.type = "button";
  const feedback = element("p", "", "company-g2-feedback");
  feedback.setAttribute("role", "status");
  feedback.hidden = true;
  const out = element("div", null, "company-g2-result");
  section.append(button, feedback, out);
  button.addEventListener("click", async () => {
    const personalToken = tokenInput.value.trim();
    if (!personalToken) {
      feedback.textContent = "Enter your own Hugging Face token to identify your account before using the shared G2 allowance.";
      feedback.hidden = false;
      return;
    }
    button.disabled = true;
    feedback.textContent = "Contacting G2 through the server…";
    feedback.hidden = false;
    try {
      const response = await fetch("/idea/company-research/g2", {
        method: "POST",
        headers: {"Content-Type": "application/json", "Authorization": "Bearer " + personalToken},
        body: JSON.stringify({search_id: searchId, index}),
        signal: active?.signal,
        cache: "no-store",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = data.detail;
        const messageText = typeof detail === "object" && detail?.message ? detail.message : (typeof detail === "string" ? detail : "G2 lookup failed.");
        throw new Error(messageText);
      }
      if (!section.isConnected) return;
      out.replaceChildren(reviews([data.feed]));
      const used = data.quota?.used;
      feedback.textContent = used == null ? "G2 request finished." : `G2 calls this month: ${used} of 3 used · ${data.quota.remaining} personal calls remaining · resets ${data.quota.reset_date} UTC.`;
    } catch (error) {
      if (error.name !== "AbortError" && section.isConnected) feedback.textContent = error.message;
    } finally {
      button.disabled = false;
      tokenInput.value = "";
    }
  });
  return section;
}

function render(result) {
  if (result.website) {
    site.replaceChildren(external(new URL(result.website).hostname, result.website));
  }
  body.replaceChildren(
    group("Founders and team", result.people, "No names found on the pages checked."),
    group("Clients", result.clients, "No client names found on the pages checked."),
    group("Products, services and pricing", result.offerings, "No products or services found on the pages checked."),
    group("Samples and demos", result.samples, "No public samples or demos found."),
    reviews(result.reviews),
    g2Controls(currentSearchId, currentIndex),
  );
  const coverage = element("details", null, "company-section company-coverage");
  coverage.append(element("summary", result.pages.length ? "Website coverage · " + result.pages.length + " pages" : "Website coverage"));
  for (const text of result.notes) coverage.append(element("p", text));
  if (result.pages.length) {
    const list = element("ul");
    for (const page of result.pages) {
      const li = element("li");
      li.append(external(new URL(page).pathname, page));
      list.append(li);
    }
    coverage.append(list);
  }
  body.prepend(coverage);
}

export async function openCompanyResearch(searchId, index, product, button) {
  active?.abort();
  const request = new AbortController();
  active = request;
  opener = button;
  currentSearchId = searchId;
  currentIndex = index;
  title.textContent = product.name;
  body.replaceChildren();
  site.replaceChildren();
  message("Checking the company website and review sources…");
  body.setAttribute("aria-busy", "true");
  if (!dialog.open) dialog.showModal();
  try {
    const response = await fetch("/idea/company-research", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({search_id: searchId, index}), signal: request.signal,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Company research could not complete.");
    if (active !== request) return;
    render(result);
    message();
  } catch (error) {
    if (active === request && error.name !== "AbortError") message(error.message, true);
  } finally {
    if (active === request) body.removeAttribute("aria-busy");
  }
}

dialog.addEventListener("close", () => {
  active?.abort();
  active = null;
  body.replaceChildren();
  if (opener?.isConnected) opener.focus();
});

