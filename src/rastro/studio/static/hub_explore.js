// Hub, Explore: find what others built for a site, try it or install it as a candidate.
// Anything that asks for extra permissions stops at a plain-language prompt first; allowing
// is per site and lasts until Studio closes. Hub text is only ever shown as plain text.
import { api, el, requireFile, request, switchView } from "./dom.js";
import { t } from "./i18n.js";
import { actionButton, chip, emptyState, errorState, loadingState } from "./ui.js";
import { asksPermission, cardModel, siteQuery } from "./hub_model.js";

export function permissionPrompt(model, choose) {
  const box = el("div", undefined, "note note-warn");
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", t("hub.perm.title"));
  const { allowable, blocked, unknown } = model.needs;
  box.append(el("strong", t("hub.perm.title")), el("p", t("hub.perm.intro", { site: model.site })));
  const list = el("ul");
  for (const name of [...allowable, ...blocked]) list.append(el("li", t(`hub.cap.${name}`)));
  box.append(list);
  if (blocked.length) box.append(el("p", t("hub.perm.blocked")));
  if (unknown) box.append(el("p", t("hub.perm.unknown", { n: unknown })));
  const row = el("div", undefined, "row");
  const button = (label, value, className) => {
    const node = el("button", label, className);
    node.type = "button";
    node.addEventListener("click", () => { box.remove(); choose(value); });
    return node;
  };
  if (allowable.length) row.append(button(t("hub.perm.allow", { site: model.site }), "site", "primary"));
  row.append(button(t("hub.perm.without"), "without", allowable.length ? "" : "primary"),
    button(t("hub.perm.cancel"), null));
  box.append(row);
  return box;
}

function askPermission(host, model) {
  if (!asksPermission(model)) return Promise.resolve("without");
  return new Promise((resolve) => {
    const box = permissionPrompt(model, resolve);
    host.append(box);
    box.querySelector?.("button")?.focus();
  });
}

async function install(host, status, model, go) {
  const file = requireFile();
  const choice = await askPermission(host, model);
  if (choice === null) return;
  const allow = choice === "site" ? model.needs.allowable : [];
  status.className = "";
  status.textContent = t("hub.installing");
  const done = await api("/api/registry/install", {
    registry: model.registry, spec: model.spec, file, allow, site: model.site,
  });
  const allowed = done.allowed_for_site.length
    ? t("hub.allowedFor", { site: model.site }) : t("hub.allowedNone");
  status.className = "good";
  status.textContent = t("hub.installed", { spec: model.spec, file, allowed });
  request("loaded");
  if (go) {
    request("review", { spec: model.spec, file });
    switchView("sources");
  }
}

function cardNode(model, status) {
  const card = el("article", undefined, "card");
  const head = el("div", undefined, "card-head");
  head.append(el("h3", model.site), chip("info", t("hub.card.version", { v: model.version })));
  card.append(head);
  const names = model.fields.map((f) => f.name).join(", ");
  card.append(el("p", names ? t("hub.card.brings", { fields: names }) : t("hub.card.noFields"), "muted"));
  const chips = el("div", undefined, "row");
  chips.append(chip(model.health, t(`hub.health.${model.health}`)));
  if (model.days !== null) {
    chips.append(chip("info", model.days ? t("hub.verified", { n: model.days }) : t("hub.verified.today")));
  }
  if (model.browser) chips.append(chip("info", t("hub.needs.browser")));
  if (asksPermission(model)) chips.append(chip("warn", t("hub.needs.perms")));
  card.append(chips);
  if (model.fields.length) {
    const more = el("details");
    more.append(el("summary", t("hub.card.fieldsTitle")));
    const list = el("ul");
    for (const f of model.fields) list.append(el("li", `${f.name}: ${f.description}`));
    more.append(list);
    card.append(more);
  }
  const tech = el("p", model.technical, "muted");
  tech.dataset.adv = "";
  card.append(tech);
  const host = el("div");
  const row = el("div", undefined, "row");
  const act = (label, go, className) => actionButton(label, async () => {
    try { await install(host, status, model, go); } catch (error) {
      status.className = "bad";
      status.textContent = t("msg.error", { why: error.message });
    }
  }, className);
  row.append(act(t("hub.try"), true, "primary"), act(t("hub.install"), false, "small"));
  card.append(row, host);
  return card;
}

export function renderOffers(results, status, offers, site, onBuild) {
  if (!offers.length) {
    results.replaceChildren(emptyState(t("hub.none", { site }), "", actionButton(t("hub.build"), onBuild, "primary")));
    return;
  }
  const grid = el("div", undefined, "hub-cards");
  for (const offer of offers) grid.append(cardNode(cardModel(offer), status));
  results.replaceChildren(grid);
}

export function exploreSection() {
  const section = el("section");
  const row = el("div", undefined, "row");
  const label = el("label", t("hub.site.label"));
  label.htmlFor = "hub-site";
  const site = el("input");
  Object.assign(site, { id: "hub-site", type: "search", maxLength: 2048, spellcheck: false,
    placeholder: t("hub.site.placeholder") });
  const go = el("button", t("hub.site.search"), "primary");
  go.type = "submit";
  row.append(label, site, go);
  const address = el("div");
  address.dataset.adv = "";
  const addrLabel = el("label", t("hub.addr"));
  addrLabel.htmlFor = "hub-address";
  const addr = el("input", undefined, "wide");
  Object.assign(addr, { id: "hub-address", type: "text", maxLength: 1024, spellcheck: false });
  address.append(addrLabel, addr, el("p", t("hub.addr.hint"), "muted small-text"));
  const status = el("p");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const results = el("div");
  results.id = "hub-results";
  const form = el("form");
  form.append(row, address);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = siteQuery(site.value);
    if (!url) return;
    go.disabled = true;
    status.textContent = "";
    results.replaceChildren(loadingState(t("hub.searching"), 2));
    try {
      const body = { url, registry: addr.value.trim() || undefined };
      const data = await api("/api/registry/offers", body);
      const shown = site.value.trim();
      renderOffers(results, status, data.offers, shown, () => switchView("wizard"));
      status.textContent = data.offers.length ? t("hub.found", { n: data.offers.length, site: shown }) : "";
    } catch (error) {
      results.replaceChildren(errorState(t("msg.error", { why: error.message }), "", undefined));
    } finally {
      go.disabled = false;
    }
  });
  section.append(form, status, results);
  return section;
}
