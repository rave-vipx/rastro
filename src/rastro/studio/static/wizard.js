// Guided create in four steps: the page address (with a hub offer first), what you
// want (fields with one example, plus an estimate), review (records, the page snapshot with
// the fields marked, click to fix a field, the site's own data), then name and save, where
// the AI may look once for records behind a click. File names are an advanced detail.
// All source-derived text is set as plain text content; markup is never parsed.
import { $, api, bind, el, request, show, state } from "./dom.js";
import { t } from "./i18n.js";
import { chip, openable } from "./ui.js";
import { estimateCard, exploreCard, hubCard, offerCard, useOffer } from "./correct.js";
import { fixField, installedRows, reviewTable, setExample, showPage } from "./pick.js";

const MAX_FIELDS = 30;
let rowCount = 0;
let edited = false; // the person typed file names: stop filling them from the project name
let found = null; // the last suggestion; review and create read it
let snapshot = null; // {wrap, page} of the last drawn snapshot
let reviewing = null; // a project the Hub just installed into: review it, then open it
const boxes = {};

export function goStep(n) {
  for (let i = 1; i <= 4; i += 1) $(`wiz-step-${i}`).hidden = i !== n;
  for (const item of document.querySelectorAll(".stepper li")) {
    if (Number(item.dataset.step) === n) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  }
  const focus = { 1: "page-url", 2: "add-field", 3: "wiz-next-3", 4: "new-name" }[n];
  $(focus).focus();
}

// A container the wizard owns inside a step, placed before the step's buttons.
function area(step, name) {
  if (boxes[name]) return boxes[name];
  const box = el("div", undefined, `wiz-${name}`);
  const parent = $(step);
  const nav = parent.querySelector(".step-nav");
  if (nav) nav.before(box);
  else parent.append(box);
  boxes[name] = box;
  return box;
}

function input(id, label, value, placeholder) {
  const node = el("input");
  Object.assign(node, { id, type: "text", value, placeholder, maxLength: 1000, spellcheck: false });
  node.setAttribute("aria-label", label);
  return node;
}

function addRow(name = "", example = "") {
  if ($("field-rows").children.length >= MAX_FIELDS) throw new Error(t("wizard.maxFields", { n: MAX_FIELDS }));
  rowCount += 1;
  const row = el("tr");
  const cells = [
    input(`field-name-${rowCount}`, t("wizard.fields.name"), name, t("wizard.fields.namePlaceholder")),
    input(`field-example-${rowCount}`, t("wizard.fields.example"), example, t("wizard.fields.examplePlaceholder")),
  ];
  const key = el("input");
  Object.assign(key, { type: "checkbox", id: `field-key-${rowCount}` });
  key.setAttribute("aria-label", t("wizard.fields.useKey"));
  const remove = el("button", t("wizard.fields.remove"), "small");
  remove.type = "button";
  remove.addEventListener("click", () => { row.remove(); });
  for (const node of [...cells, key, remove]) {
    const td = el("td");
    td.append(node);
    row.append(td);
  }
  $("field-rows").append(row);
  return cells[0];
}

// Live check of the address, DOM-free: {ok, host} or {ok: false, key} for the message.
export function urlState(value) {
  const text = String(value || "").trim();
  if (!text) return { ok: false, key: "wizard.needUrl" };
  const href = openable(text);
  if (!href) return { ok: false, key: "wizard.url.notWeb" };
  return { ok: true, host: new URL(href).hostname };
}

function pageUrl() {
  const checked = urlState($("page-url").value);
  if (!checked.ok) throw new Error(t(checked.key));
  return $("page-url").value.trim();
}

function collect() {
  const example = {};
  const identity = [];
  for (const row of $("field-rows").children) {
    const [name, value, key] = row.querySelectorAll("input");
    const field = name.value.trim();
    if (!field && !value.value.trim()) continue;
    if (!field || !value.value.trim()) throw new Error(t("wizard.needBoth"));
    if (field in example) throw new Error(t("wizard.twice", { field }));
    example[field] = value.value;
    if (key.checked) identity.push(field);
  }
  if (!Object.keys(example).length) throw new Error(t("wizard.needField"));
  return { url: pageUrl(), examples: [example], identity };
}

// "Agenda de fiestas · Valle Azul" -> "agenda-de-fiestas-valle-azul": letters, digits, "-" only.
export function slug(name) {
  const plain = name.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  return plain.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
}

function collectSafe() {
  try { return collect(); } catch { return { identity: [] }; }
}

function fillFileNames() {
  if (edited) return;
  const base = slug($("new-name").value) || "scraper";
  const keyed = collectSafe().identity.length > 0;
  $("new-file").value = `${base}.toml`;
  $("new-dataset").value = keyed ? `${base}.sqlite` : "";
  $("new-memory").value = `${base}-recipes.sqlite`;
  $("new-key-note").hidden = keyed;
}

function defaultName(url) {
  const parsed = new URL(url); // only called with an address urlState accepted
  const leaf = parsed.pathname.split("/").filter(Boolean).pop();
  return leaf ? `${parsed.hostname} ${leaf}` : parsed.hostname;
}

// Step 1: someone may already have built this; the hub is asked before anything is fetched.
async function lookUp(app) {
  reviewing = null;
  const { host } = urlState(pageUrl());
  const box = area("wiz-step-1", "hub");
  show("url-status", t("wizard.hub.searching", { host }), "muted");
  const data = await api("/api/wizard/lookup", { url: pageUrl() }).catch(() => ({ offers: [], hub: "unavailable" }));
  show("url-status", data.hub === "unavailable" ? t("wizard.hub.down") : "", "muted");
  if (!data.offers.length) { box.replaceChildren(); return enterFields(); }
  const offer = data.offers[0];
  box.replaceChildren(hubCard(offer, host, async () => {
    request("hub-try", { offer, url: pageUrl() });
    app.switchView("hub");
  }, async () => { box.replaceChildren(); await enterFields(); }));
}

// Step 2: what you want, with the estimate before anything large is read.
let refresh = null;

function enterFields() {
  const box = area("wiz-step-2", "estimate");
  if (!box.children.length) {
    refresh = estimateCard(box, (more) => api("/api/wizard/estimate", { url: pageUrl(), more }));
  }
  goStep(2);
  return refresh();
}

// The page loads its records from an API: write a job that reads that response instead.
async function showOffers(app, body) {
  const box = area("wiz-step-3", "api");
  box.replaceChildren();
  const data = await api("/api/wizard/offers", { url: body.url, fields: Object.keys(body.examples[0]) })
    .catch(() => ({ offers: [] }));
  if (!data.offers.length) return;
  const offer = data.offers[0];
  box.append(offerCard(offer, () => useOffer(app, body.url, offer, $("new-file").value.trim())));
}

async function suggestRecipe(app) {
  const { identity, ...body } = collect();
  show("suggest-status", t("wizard.fetching"), "muted");
  found = await api("/api/suggest", body);
  const good = found.status === "success";
  show("suggest-status", "");
  const names = Object.keys(found.recipe.fields);
  const out = $("suggest-result");
  out.replaceChildren(chip(good ? "ok" : "warn", t("wizard.found", { n: found.records })),
    reviewTable(names, found.preview));
  if (found.truncated) out.append(el("p", t("wizard.first50"), "muted"));
  if (found.allow_http) out.append(el("p", t("wizard.allowHttp"), "warn"));
  if (identity.length === 0) out.append(el("p", t("wizard.tickKey"), "muted"));
  if (found.explore) out.append(el("p", t("wizard.explore.hint"), "muted"));
  if (!$("new-name").value) $("new-name").value = defaultName(body.url);
  fillFileNames();
  goStep(3);
  const [shot] = await Promise.all([showPage(area("wiz-step-3", "page"), body.url, found.recipe), showOffers(app, body)]);
  snapshot = shot;
}

// Step 4: save; when the page may hide records behind a click, the AI may look once.
async function createJob(app) {
  const body = collect();
  const file = $("new-file").value.trim();
  if (!file) throw new Error(t("wizard.needFile"));
  const dataset = $("new-dataset").value.trim();
  const memory = $("new-memory").value.trim();
  const made = await api("/api/create", {
    ...body, file, dataset: dataset || null, identity: dataset ? body.identity : [],
    recipe_memory: memory || null,
  });
  show("create-status", t(made.allow_http ? "wizard.createdHttp" : "wizard.created", { file: made.file }),
    made.allow_http ? "warn" : "good");
  await app.refreshJobs(made.file);
  if (found && found.explore && state.ai.enabled) return exploreCard(area("wiz-step-4", "explore"), app, made.file);
  await app.openJob(made.file);
  app.switchView("results");
  return undefined;
}

// From the Hub: what someone else built was installed into a project; check its records here.
async function reviewInstalled(app, { spec, file }) {
  const data = await api("/api/preview", { file });
  const { names, rows } = installedRows(data.preview);
  reviewing = file;
  app.switchView("wizard");
  goStep(3);
  $("suggest-result").replaceChildren(el("p", t("wizard.hub.reviewing", { spec, file })),
    chip(rows.length ? "ok" : "warn", t("wizard.found", { n: rows.length })), reviewTable(names, rows));
}

export function initWizard(app) {
  document.addEventListener("studio:review", (event) => reviewInstalled(app, event.detail || {})
    .catch((error) => show("suggest-status", t("msg.error", { why: error.message }), "bad")));
  addRow();
  addRow();
  $("page-url").addEventListener("input", () => {
    const checked = urlState($("page-url").value);
    show("url-status", checked.ok ? t("wizard.url.ok", { host: checked.host }) : t(checked.key), checked.ok ? "muted" : "warn");
  });
  bind("wiz-next-1", "url-status", () => lookUp(app));
  bind("wiz-back-2", "suggest-status", () => goStep(1));
  bind("wiz-back-3", "suggest-status", () => {
    const ctx = { snapshot, found, url: pageUrl(), use: async (field, text) => { setExample($("field-rows"), field, text); await suggestRecipe(app); } };
    if (!fixField(area("wiz-step-3", "fix"), ctx)) goStep(2);
  });
  bind("wiz-next-3", "create-status", async () => {
    if (!reviewing) { fillFileNames(); goStep(4); return; }
    const file = reviewing;
    reviewing = null;
    await app.openJob(file).then(() => app.switchView("results"));
  });
  bind("wiz-back-4", "create-status", () => goStep(3));
  bind("add-field", "suggest-status", () => addRow().focus());
  bind("suggest", "suggest-status", () => suggestRecipe(app));
  bind("create", "create-status", () => createJob(app));
  $("new-name").addEventListener("input", fillFileNames);
  for (const id of ["new-file", "new-dataset", "new-memory"]) {
    $(id).addEventListener("input", () => { edited = true; });
  }
}
