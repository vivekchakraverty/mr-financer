const views = {
  ideation: { page: "ideation" },
  lookup: { page: "ideation", scroll: true },
  results: { page: "ideation", scroll: true },
  finance: { page: "ideation", scroll: true },
  documents: { page: "documents", feature: "business-plan" },
  "business-plan": { page: "documents", feature: "business-plan" },
  "pitch-deck": { page: "documents", feature: "pitch-deck" },
  funding: { page: "funding", feature: "loans" },
  loans: { page: "funding", feature: "loans" },
  "angel-investors": { page: "funding", feature: "angel-investors" },
  incorporation: { page: "incorporation" },
  "incorporation-live": { page: "incorporation", scroll: true },
  sources: { page: "sources" },
};

export function initializeNavigation({ showSources, showInvestors, showIncorporation }) {
  const pages = [...document.querySelectorAll("[data-workspace-page]")];
  const features = [...document.querySelectorAll("[data-workspace-feature]")];
  function display() {
    const fragment = window.location.hash.slice(1);
    const view = Object.hasOwn(views, fragment) ? views[fragment] : views.ideation;
    for (const page of pages) page.hidden = page.id !== view.page;
    for (const feature of features) feature.hidden = feature.id !== view.feature;
    for (const link of document.querySelectorAll("[data-page-link], [data-feature-link]")) {
      const selected = link.hasAttribute("data-page-link")
        ? link.dataset.pageLink === view.page
        : link.dataset.featureLink === view.feature;
      if (selected) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    if (view.page === "sources") showSources();
    if (view.page === "incorporation") showIncorporation();
    if (view.feature === "angel-investors") showInvestors();

    requestAnimationFrame(() => {
      if (view.scroll) document.getElementById(fragment).scrollIntoView({ block: "start" });
      else window.scrollTo({ top: 0, behavior: "instant" });
    });
  }
  window.addEventListener("hashchange", display);
  display();
}
