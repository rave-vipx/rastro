// New scraper wizard in four steps: URL, fields with one example each, a preview of
// the records, then a project name. File names are an advanced detail filled from the name.
// All source-derived text is set as plain text content; markup is never parsed.
import { $, api, bind, el, show, table } from "./dom.js";
import { t } from "./i18n.js";
import { openable } from "./ui.js";

const MAX_FIELDS = 30;
let rowCount = 0;
let edited = false; // the person typed file names: stop filling them from the project name

export function goStep(n) {
  for (let i = 1; i <= 4; i += 1) $(`wiz-step-${i}`).hidden = i !== n;
  for (const item of document.querySelectorAll(".stepper li")) {
    if (Number(item.dataset.step) === n) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  }
  const focus = { 1: "page-url", 2: "add-field", 3: "wiz-next-3", 4: "new-name" }[n];
  $(focus).focus();
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

function pageUrl() {
  const url = $("page-url").value.trim();
  if (!url) throw new Error(t("wizard.needUrl"));
  if (!openable(url)) throw new Error(t("wizard.badUrl"));
  return url;
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

function confidenceText(note) {
  const parts = [note.method];
  if (note.ambiguous) parts.push(t("wizard.ambiguous"));
  parts.push(t("wizard.withValue", { n: note.records_with_value }));
  if (note.note) parts.push(note.note);
  return parts.join("; ");
}

// "Agenda de fiestas · Valle Azul" -> "agenda-de-fiestas-valle-azul": letters, digits, "-" only.
export function slug(name) {
  const plain = name.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  return plain.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
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

function collectSafe() {
  try {
    return collect();
  } catch {
    return { identity: [] };
  }
}

function defaultName(url) {
  try {
    const parsed = new URL(url);
    const leaf = parsed.pathname.split("/").filter(Boolean).pop();
    return leaf ? `${parsed.hostname} ${leaf}` : parsed.hostname;
  } catch {
    return "";
  }
}

async function suggestRecipe() {
  const { identity, ...body } = collect();
  show("suggest-status", t("wizard.fetching"), "muted");
  const data = await api("/api/suggest", body);
  show("suggest-status", t("wizard.suggested", { status: data.status, n: data.records }),
    data.status === "success" ? "good" : "warn");
  const names = Object.keys(data.recipe.fields);
  const recipe = el("details", undefined, "fold");
  recipe.append(el("summary", t("wizard.recipe")), el("p", t("wizard.eachRecord", { selector: data.recipe.record_selector })),
    table([t("col.field"), t("wizard.selector"), t("wizard.confidence")], names.map((n) =>
      [n, data.recipe.fields[n], confidenceText(data.confidence[n] || { method: "-", records_with_value: 0 })])));
  const out = $("suggest-result");
  out.replaceChildren(el("p", t("wizard.found", { n: data.records }), data.status === "success" ? "good" : "warn"),
    table(names, data.preview.map((row) => names.map((n) => row[n])), t("wizard.previewCaption")), recipe);
  if (data.truncated) out.append(el("p", t("wizard.first50"), "muted"));
  if (data.allow_http) out.append(el("p", t("wizard.allowHttp"), "warn"));
  if (identity.length === 0) out.append(el("p", t("wizard.tickKey"), "muted"));
  if (!$("new-name").value) $("new-name").value = defaultName(body.url);
  fillFileNames();
  goStep(3);
}

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
  await app.openJob(made.file);
  app.switchView("results");
}

export function initWizard(app) {
  addRow();
  addRow();
  bind("wiz-next-1", "url-status", () => { pageUrl(); show("url-status", ""); goStep(2); });
  bind("wiz-back-2", "suggest-status", () => goStep(1));
  bind("wiz-back-3", "suggest-status", () => goStep(2));
  bind("wiz-next-3", "create-status", () => { fillFileNames(); goStep(4); });
  bind("wiz-back-4", "create-status", () => goStep(3));
  bind("add-field", "suggest-status", () => addRow().focus());
  bind("suggest", "suggest-status", suggestRecipe);
  bind("create", "create-status", () => createJob(app));
  $("new-name").addEventListener("input", fillFileNames);
  for (const id of ["new-file", "new-dataset", "new-memory"]) {
    $(id).addEventListener("input", () => { edited = true; });
  }
}
