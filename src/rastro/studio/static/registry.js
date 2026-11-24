// the Hub view. Explore by site, publish a proposal; folder tools sit under Advanced.
// (Registry (URL). Search and install from a local registry folder or an HTTPS URL;
// publish writes into a local registry folder only (no account, no push, no Git).
// Every source-derived value stays plain text.
import { $, api, el, requireFile, table } from "./dom.js";
import { onLangChange, t } from "./i18n.js";
import { exploreSection } from "./hub_explore.js";
import { publishSection } from "./hub_publish.js";

function input(row, id, label, placeholder) {
  const caption = el("label", label);
  caption.htmlFor = id;
  const field = el("input");
  Object.assign(field, { id, type: "text", maxLength: 1024, placeholder, spellcheck: false });
  row.append(caption, field);
  return field;
}

function status(id) {
  const node = el("p");
  node.id = id;
  node.setAttribute("role", "status");
  node.setAttribute("aria-live", "polite");
  return node;
}

async function action(button, output, message, run) {
  button.disabled = true;
  output.textContent = message;
  output.className = "";
  try {
    await run();
    output.className = "good";
  } catch (error) {
    output.textContent = t("msg.error", { why: error.message });
    output.className = "bad";
  } finally {
    button.disabled = false;
  }
}

function trustText(trust) {
  return t("registry.trust", {
    replay: t(trust.replay_verified ? "registry.verified" : "registry.notVerified"),
    sample: t(trust.has_sample ? "registry.included" : "registry.missing"),
    days: trust.days_since_verified, format: trust.format,
  });
}

function renderEntries(entries, registry, results, output) {
  if (!entries.length) {
    results.replaceChildren(el("p", t("registry.noMatch")));
    return;
  }
  const rows = entries.map((entry) => {
    const install = el("button", t("registry.install"), "small");
    install.type = "button";
    install.addEventListener("click", () => action(install, output, t("registry.installing"), async () => {
      const file = requireFile();
      const result = await api("/api/registry/install", { registry, spec: `${entry.id}@${entry.version}`, file });
      output.textContent = t(result.reused ? "registry.reused" : "registry.installed", {
        spec: `${result.id}@${result.version}`, state: result.state, file, trust: trustText(result.trust),
      });
    }));
    return [entry.id, entry.version, entry.source_patterns.join(", "), entry.author.name,
      entry.license, trustText(entry.trust), install];
  });
  const heads = ["recipe", "version", "sources", "author", "license", "trust", "action"].map((k) => t(`registry.col.${k}`));
  results.replaceChildren(table(heads, rows, t("registry.caption")));
}

function searchForm(section) {
  const form = el("form");
  const row = el("div", undefined, "row");
  const registry = input(row, "registry-location", t("registry.location"), "examples/registry");
  registry.required = true;
  const query = input(row, "registry-query", t("registry.query"), "listing");
  const search = el("button", t("registry.search"));
  search.type = "submit";
  row.append(search);
  const output = status("registry-status");
  const installStatus = status("registry-install-status");
  const results = el("div");
  results.id = "registry-results";
  form.append(row);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    action(search, output, t("registry.searching"), async () => {
      results.replaceChildren();
      installStatus.textContent = "";
      const location = registry.value.trim();
      const data = await api("/api/registry/search", { registry: location, query: query.value });
      renderEntries(data.entries, location, results, installStatus);
      output.textContent = t("registry.found", { n: data.entries.length });
    });
  });
  section.append(form, output, results, installStatus);
}

function publishForm(section) {
  section.append(el("h3", t("registry.publishTitle")), el("p", t("registry.publishNote"), "warn"));
  const form = el("form");
  const row = el("div", undefined, "row");
  const bundle = input(row, "registry-bundle", t("registry.bundle"), "exports/my-recipe");
  bundle.required = true;
  const destination = input(row, "registry-destination", t("registry.destination"), "examples/registry");
  destination.required = true;
  const publish = el("button", t("registry.publish"));
  publish.type = "submit";
  row.append(publish);
  const output = status("registry-publish-status");
  form.append(row);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    action(publish, output, t("registry.publishing"), async () => {
      const data = await api("/api/registry/publish", { bundle: bundle.value.trim(), registry: destination.value.trim() });
      output.textContent = t("registry.published", { spec: `${data.entry.id}@${data.entry.version}` });
    });
  });
  section.append(form, output);
}

// The folder search and bundle publish stay for people who run their own hub (Advanced).
function folderTools() {
  const box = el("details", undefined, "card fold");
  box.dataset.adv = "";
  box.append(el("summary", t("registry.publishTitle")), el("p", t("registry.intro"), "muted"),
    el("p", t("registry.claim"), "warn"));
  searchForm(box);
  publishForm(box);
  return box;
}

function build() {
  const section = el("div");
  section.id = "registry-section";
  const tabs = el("div", undefined, "row");
  tabs.setAttribute("role", "group");
  tabs.setAttribute("aria-label", t("hub.tabs.label"));
  const panels = { explore: exploreSection(), publish: publishSection() };
  const buttons = {};
  const pick = (name) => {
    for (const key of Object.keys(panels)) {
      panels[key].hidden = key !== name;
      buttons[key].setAttribute("aria-pressed", String(key === name));
    }
  };
  for (const key of Object.keys(panels)) {
    buttons[key] = el("button", t(`hub.tab.${key}`));
    buttons[key].type = "button";
    buttons[key].addEventListener("click", () => pick(key));
    tabs.append(buttons[key]);
  }
  section.append(tabs, panels.explore, panels.publish, folderTools());
  pick("explore");
  return section;
}

const FIELDS = ["registry-location", "registry-query", "registry-bundle", "registry-destination",
  "hub-site", "hub-address", "hub-source", "hub-sample", "hub-destination", "hub-id",
  "hub-version", "hub-author", "hub-license"];

// The guided create found a hub offer for its URL and the person chose "Try": search it here.
export function tryFromWizard(event) {
  const url = event.detail && event.detail.url;
  const site = document.getElementById("hub-site");
  if (!url || !site) return;
  site.value = url;
  site.form.requestSubmit();
}

export function initRegistry() {
  if ($("registry-section")) return;
  $("settings-registry").append(build());
  document.addEventListener("studio:hub-try", tryFromWizard);
  onLangChange(() => {
    const typed = FIELDS.map((id) => $(id).value);
    $("settings-registry").replaceChildren(build());
    FIELDS.forEach((id, i) => { $(id).value = typed[i]; });
  });
}
