import { businessPlanAnswers } from "./business-plan.js";
import { downloadFile, requestDownload } from "./downloads.js";

const byId = id => document.getElementById(id);
const modes = [["investor", "Investor"], ["lender", "Lender"], ["grant", "Grant committee"],
  ["customer or partner", "Customer or partner"], ["internal review", "Internal review"]];
const evidence = [["founder-reported", "Founder-reported"], ["documented", "Documented"],
  ["estimate", "Estimate"], ["target", "Target"], ["unknown", "I don’t know"]];
const fields = new Map();
const headings = new Map();
let currentOutline = null;
const imageUrls = new Set();

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function control(container, label, tag, attrs = {}) {
  const wrapper = el("label", label);
  const input = el(tag);
  for (const [name, value] of Object.entries(attrs)) input[name] = value;
  wrapper.append(input);
  container.append(wrapper);
  return input;
}

function choices(container, label, options) {
  const input = control(container, label, "select");
  for (const value of options) {
    const [id, text] = Array.isArray(value) ? value : [value, value];
    const row = el("option", text);
    row.value = id;
    input.append(row);
  }
  return input;
}

function note(message) {
  const box = byId("pitch-status");
  box.textContent = message;
  box.hidden = !message;
}

async function api(path, options) {
  const response = await fetch("/business-plan/pitch/" + path, options);
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map(row => row.loc.slice(1).join(" → ") + " · " + row.msg).join(" ")
      : data.detail;
    throw new Error(detail || "The pitch deck request could not be completed.");
  }
  return data;
}

function questionGroup(group) {
  const details = el("details", null, "pitch-question-group");
  details.open = group.topics.some(topic => topic.questions.some(question => question.core));
  details.append(el("summary", group.title));
  for (const topic of group.topics) {
    headings.set(topic.id, topic.title);
    const box = el("fieldset", null, "pitch-topic");
    box.append(el("legend", topic.title));
    for (const question of topic.questions) {
      const row = el("div", null, "pitch-answer");
      const type = question.type;
      const text = control(row, question.label, type === "url" ? "input" : "textarea",
        type === "url" ? {type: "url", maxLength: 1000, placeholder: "https://"} : {rows: 2, maxLength: 1000});
      const evidenceRow = el("div", null, "plan-grid");
      const status = choices(evidenceRow, "Evidence status", evidence);
      const source = control(evidenceRow, "Supporting link", "input", {type: "url", maxLength: 1000, placeholder: "https://"});
      status.addEventListener("change", () => { text.disabled = status.value === "unknown"; });
      row.append(evidenceRow);
      box.append(row);
      fields.set(question.id, {text, status, source});
    }
    details.append(box);
  }
  return details;
}

function answers() {
  const result = {};
  for (const [id, field] of fields) {
    const status = field.status.value;
    const text = status === "unknown" ? "Unknown" : field.text.value.trim();
    const source = field.source.value.trim();
    if (status === "documented" && (!text || !source)) {
      throw new Error("Add an answer and supporting link for the documented response in " + headings.get(id.split(".")[0]) + ".");
    }
    if (text) result[id] = {text, status, source: status === "unknown" ? null : source || null};
  }
  return result;
}

function importPlan() {
  const data = businessPlanAnswers();
  const map = {
    "cover.promise": data.idea, "problem.buyer": data.customer, "problem.workaround": data.alternative,
    "market.segment": data.market, "channels.route": data.channels, "team.roles": data.team_goal,
    "traction.stage": data.stage === "idea" ? "" : data.stage, "traction.actuals": data.recent,
  };
  let count = 0;
  for (const [id, text] of Object.entries(map)) {
    const field = fields.get(id);
    if (text && field && !field.text.value.trim() && !field.text.disabled) {
      field.text.value = text.slice(0, field.text.maxLength);
      count++;
    }
  }
  note(count ? "Filled " + count + " blank answers from the current business plan." : "No additional business plan answers to copy.");
}

function link(label, value) {
  if (!value) return el("span", label);
  const result = el("a", label);
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol)) return el("span", label);
  result.href = url.href;
  result.target = "_blank";
  result.rel = "noopener noreferrer";
  return result;
}

function artworkControls(slide) {
  const region = el("div", null, "pitch-artwork");
  const input = control(region, "Image prompt", "textarea", {rows: 3, maxLength: 1200, value: slide.image_prompt});
  const button = el("button", "Generate illustration", "chip");
  button.type = "button";
  const status = el("p");
  status.setAttribute("role", "status");
  const preview = el("div");
  let currentUrl;
  region.append(button, status, preview);
  button.addEventListener("click", async () => {
    const tokenField = byId("pitch-hf-token");
    const token = tokenField.value.trim();
    if (!token) {
      status.textContent = "Enter your Hugging Face access token for images.";
      tokenField.focus();
      return;
    }
    button.disabled = true;
    status.textContent = "Generating on Hugging Face ZeroGPU…";
    try {
      const response = await fetch("/business-plan/pitch/image", {
        method: "POST",
        headers: {"Content-Type": "application/json", Authorization: "Bearer " + token},
        body: JSON.stringify({prompt: input.value.trim()}),
      });
      if (!response.ok || !(response.headers.get("Content-Type") || "").startsWith("image/jpeg")) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : "Check the image prompt and try again.");
      }
      const blob = await response.blob();
      if (!region.isConnected) return;
      if (currentUrl) {
        URL.revokeObjectURL(currentUrl);
        imageUrls.delete(currentUrl);
      }
      currentUrl = URL.createObjectURL(blob);
      imageUrls.add(currentUrl);
      const image = el("img");
      image.src = currentUrl;
      image.alt = "Generated illustration for slide " + slide.number;
      const download = el("a", "Download illustration");
      download.href = currentUrl;
      download.download = "Mr-Financer-Slide-" + slide.number + ".jpg";
      preview.replaceChildren(image, download);
      status.textContent = "Illustration ready.";
    } catch (error) {
      status.textContent = error.message || "Could not generate the illustration.";
    } finally {
      button.disabled = false;
    }
  });
  return region;
}

function render(deck) {
  for (const url of imageUrls) URL.revokeObjectURL(url);
  imageUrls.clear();
  const root = byId("pitch-slides");
  root.replaceChildren(el("p", deck.slide_count + " slides · " + deck.audience + " · " + deck.format +
    (deck.minutes ? " · " + deck.minutes + " minutes" : "")));
  for (const slide of deck.slides) {
    const card = el("article", null, "record pitch-slide");
    card.append(el("h3", slide.number + ". " + slide.title));
    const list = el("ul");
    for (const item of slide.items) {
      const entry = el("li");
      entry.append(el("strong", item.label), el("p", item.text, "pitch-answer-text"),
        el("small", [item.status, item.reference].filter(Boolean).join(" · ")));
      if (item.source) entry.append(" ", link("Source", item.source));
      list.append(entry);
    }
    if (slide.items.length) card.append(list);
    if (slide.missing.length) {
      const missing = el("details", null, "details");
      missing.append(el("summary", "Answers still needed"));
      const parts = el("ul");
      for (const question of slide.missing) parts.append(el("li", question));
      missing.append(parts);
      card.append(missing);
    }
    card.append(el("p", slide.visual_guidance));
    if (slide.image_prompt) {
      const prompt = el("details", null, "details");
      prompt.append(el("summary", "Slide illustration"), artworkControls(slide));
      card.append(prompt);
    }
    root.append(card);
  }
  if (deck.financial_gaps.length) {
    const card = el("details", null, "record");
    card.append(el("summary", "Missing business plan inputs"));
    const list = el("ul");
    for (const item of deck.financial_gaps) list.append(el("li", item));
    card.append(list);
    root.append(card);
  }
  currentOutline = deck;
  byId("pitch-result").hidden = false;
  byId("pitch-prompts").disabled = !deck.slides.some(slide => slide.image_prompt);
}

function download(name, text) {
  downloadFile(new Blob([text], {type: "text/markdown;charset=utf-8"}), name);
}

function outlineText(deck, onlyPrompts = false) {
  const escape = value => String(value ?? "").replace(/[\\`*_\[\]<>#]/g, "\\$&");
  const lines = onlyPrompts ? ["# Pitch deck image prompts"] :
    ["# Pitch deck outline", "", deck.slide_count + " slides · " + deck.audience + " · " + deck.format +
      (deck.minutes ? " · " + deck.minutes + " minutes" : "")];
  for (const slide of deck.slides) {
    if (onlyPrompts && !slide.image_prompt) continue;
    lines.push("", "## " + slide.number + ". " + escape(slide.title), "");
    if (!onlyPrompts) {
      for (const item of slide.items) {
        lines.push("- **" + escape(item.label) + "** · " + escape(item.text).replaceAll("\n", "\n  ") +
          " (" + escape([item.status, item.reference].filter(Boolean).join(" · ")) + ")" +
          (item.source ? " [Source](<" + item.source + ">)" : ""));
      }
      for (const gap of slide.missing) lines.push("- Missing · " + escape(gap));
      lines.push("", escape(slide.visual_guidance));
    }
    if (slide.image_prompt) lines.push("", escape(slide.image_prompt));
  }
  if (!onlyPrompts && deck.financial_gaps.length) {
    lines.push("", "## Missing business plan inputs", "", ...deck.financial_gaps.map(item => "- " + escape(item)));
  }
  return lines.join("\n") + "\n";
}

function initializeImageDeck() {
  const form = byId("pitch-image-deck");
  const input = byId("pitch-upload");
  const list = byId("pitch-image-list");
  const button = byId("pitch-download-pptx");
  const status = byId("pitch-upload-status");
  const images = [];
  const mb = 1024 * 1024;
  let exporting = false;

  function announce(message) {
    status.textContent = message;
    status.hidden = !message;
  }

  function renderImages() {
    list.replaceChildren(...images.map(({file, url}, index) => {
      const row = el("li");
      const thumbnail = el("img");
      thumbnail.src = url;
      thumbnail.alt = "";
      thumbnail.loading = "lazy";
      const name = el("span", file.name, "pitch-image-name");
      const actions = el("div", null, "pitch-image-actions");
      for (const [action, label, unavailable] of [
        ["up", "Move up", index === 0],
        ["down", "Move down", index === images.length - 1],
        ["remove", "Remove", false],
      ]) {
        const control = el("button", label, "chip");
        control.type = "button";
        control.dataset.imageAction = action;
        control.dataset.imageIndex = index;
        control.setAttribute("aria-label", label + " " + file.name + " (slide " + (index + 1) + ")");
        control.disabled = unavailable;
        actions.append(control);
      }
      row.append(thumbnail, name, actions);
      return row;
    }));
    list.hidden = images.length === 0;
    button.disabled = exporting || images.length === 0;
  }

  input.addEventListener("change", () => {
    const files = [...input.files];
    input.value = "";
    if (!files.length) return;
    if (images.length + files.length > 40) return announce("Select up to 40 images in one deck.");
    for (const file of files) {
      if (!file.size || file.size > 10 * mb) return announce(file.name + " must be between 1 byte and 10 MB.");
      if (!/^image\/(png|jpeg|webp)$/.test(file.type) && !/\.(png|jpe?g|webp)$/i.test(file.name)) {
        return announce(file.name + " must be a PNG, JPEG, or WebP image.");
      }
    }
    const bytes = files.reduce((total, file) => total + file.size, 0) +
      images.reduce((total, image) => total + image.file.size, 0);
    if (bytes > 50 * mb) return announce("Select images totaling 50 MB or less.");
    for (const file of files) images.push({file, url: URL.createObjectURL(file)});
    renderImages();
    announce(images.length + (images.length === 1 ? " image ready." : " images ready."));
  });

  list.addEventListener("click", event => {
    const control = event.target.closest("[data-image-action]");
    if (!control) return;
    const index = Number(control.dataset.imageIndex);
    const action = control.dataset.imageAction;
    let nextIndex = index;
    if (action === "remove") {
      const [removed] = images.splice(index, 1);
      URL.revokeObjectURL(removed.url);
      nextIndex = Math.min(index, images.length - 1);
      announce("Removed " + removed.file.name + ".");
    } else {
      nextIndex = index + (action === "up" ? -1 : 1);
      [images[index], images[nextIndex]] = [images[nextIndex], images[index]];
      announce(images[nextIndex].file.name + " moved to slide " + (nextIndex + 1) + ".");
    }
    renderImages();
    const row = list.children[nextIndex];
    (row?.querySelector('[data-image-action="' + action + '"]:enabled') ||
      row?.querySelector("button:enabled") || input).focus();
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (exporting || !images.length) return;
    const data = new FormData();
    for (const {file} of images) data.append("images", file);
    exporting = true;
    button.disabled = true;
    announce("Building your PowerPoint…");
    try {
      await requestDownload("/business-plan/pitch/export/pptx", {body: data}, "Mr-Financer-Slide-Deck.pptx");
      announce("Download started.");
    } catch (error) {
      announce(error.message);
    } finally {
      exporting = false;
      button.disabled = images.length === 0;
    }
  });
}

export async function initializePitchDeck() {
  initializeImageDeck();
  const container = byId("pitch-settings");
  const submit = byId("pitch-submit");
  try {
    const schema = await api("questions");
    const slideCount = control(container, "Slide count", "input", {type: "number", min: schema.min_slides,
      max: schema.max_slides, value: 12, required: true});
    const audience = choices(container, "Audience", modes);
    const format = choices(container, "Deck format", ["both", "live pitch", "leave-behind"]);
    const minutes = control(container, "Speaking time in minutes (optional)", "input", {type: "number", min: 1, max: 90});
    const style = choices(container, "Image style", ["editorial photo", "clean illustration", "studio product concept"]);
    const subject = control(container, "Visual brief using public details only", "input",
      {maxLength: 200, placeholder: "e.g. a small bakery packing subscription boxes"});
    const copyVisualBrief = el("button", "Use the company one-liner as the visual brief", "chip");
    copyVisualBrief.type = "button";
    copyVisualBrief.addEventListener("click", () => {
      const publicDescription = fields.get("cover.promise").text.value.trim();
      if (!publicDescription) return note("Add a company one-liner first.");
      subject.value = publicDescription.slice(0, subject.maxLength);
      subject.focus();
      note("Check that the visual brief contains only public details.");
    });
    container.append(copyVisualBrief);
    byId("pitch-questions").replaceChildren(...schema.groups.map(questionGroup));
    byId("pitch-import").addEventListener("click", importPlan);
    byId("pitch-clear-token").addEventListener("click", () => {
      byId("pitch-hf-token").value = "";
      byId("pitch-hf-token").focus();
    });
    byId("pitch-export").addEventListener("click", () =>
      download("Mr-Financer-Pitch-Outline.md", outlineText(currentOutline)));
    byId("pitch-prompts").addEventListener("click", () =>
      download("Mr-Financer-Pitch-Image-Prompts.md", outlineText(currentOutline, true)));
    byId("pitch-form").addEventListener("submit", async event => {
      event.preventDefault();
      submit.disabled = true;
      byId("pitch-result").hidden = true;
      note("Building the outline…");
      try {
        const plan = byId("pitch-include-plan").checked ? businessPlanAnswers() : null;
        if (plan && (plan.idea.length < 3 || !/^[A-Z]{3}$/.test(plan.currency))) {
          throw new Error("Enter an idea and a three-letter currency in the business plan, or clear Include current business plan figures.");
        }
        const result = await api("outline", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({slide_count: Number(slideCount.value), audience: audience.value, format: format.value,
            minutes: minutes.value ? Number(minutes.value) : null, visual_style: style.value,
            visual_subject: subject.value.trim(), answers: answers(), plan}),
        });
        render(result);
        note(result.slide_count + " slides ready.");
      } catch (error) {
        note(error.message);
      } finally {
        submit.disabled = false;
      }
    });
    submit.disabled = false;
  } catch (error) {
    note(error.message);
  }
}

