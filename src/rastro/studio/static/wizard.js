// New scraper wizard: saved page + example values -> suggested recipe -> new job file.
// All source-derived text is set as plain text content; markup is never parsed.
import { $, api, bind, el, show, table } from "./dom.js";

const MAX_FIELDS = 30;
let rowCount = 0;

function addRow(name = "", example = "") {
  if ($("field-rows").children.length >= MAX_FIELDS) throw new Error(`at most ${MAX_FIELDS} fields`);
  rowCount += 1;
  const row = el("tr");
  const cells = [
    input(`field-name-${rowCount}`, "Field name", name, "title"),
    input(`field-example-${rowCount}`, "Example value", example, "copy a value from the page"),
  ];
  const key = el("input");
  Object.assign(key, { type: "checkbox", id: `field-key-${rowCount}` });
  key.setAttribute("aria-label", "Use as record key");
  const remove = el("button", "Remove");
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

function input(id, label, value, placeholder) {
  const node = el("input");
  Object.assign(node, { id, type: "text", value, placeholder, maxLength: 1000, spellcheck: false });
  node.setAttribute("aria-label", label);
  return node;
}

function collect() {
  const example = {};
  const identity = [];
  for (const row of $("field-rows").children) {
    const [name, value, key] = row.querySelectorAll("input");
    const field = name.value.trim();
    if (!field && !value.value.trim()) continue;
    if (!field || !value.value.trim()) throw new Error("every field needs a name and an example value");
    if (field in example) throw new Error(`field "${field}" appears twice`);
    example[field] = value.value;
    if (key.checked) identity.push(field);
  }
  if (!Object.keys(example).length) throw new Error("add at least one field with an example");
  if (!$("page").value) throw new Error("pick a saved page first");
  return { page: $("page").value, url: $("page-url").value.trim(), examples: [example], identity };
}

function confidenceText(note) {
  const parts = [note.method];
  if (note.ambiguous) parts.push("ambiguous");
  parts.push(`${note.records_with_value} records with a value`);
  if (note.note) parts.push(note.note);
  return parts.join("; ");
}

async function suggestRecipe() {
  const { identity, ...body } = collect();
  const data = await api("/api/suggest", body);
  show("suggest-status", `Suggested recipe (${data.status}): ${data.records} records found.`,
    data.status === "success" ? "good" : "warn");
  const out = $("suggest-result");
  const names = Object.keys(data.recipe.fields);
  const rows = names.map((n) => [n, data.recipe.fields[n], confidenceText(data.confidence[n] || { method: "-", records_with_value: 0 })]);
  out.replaceChildren(
    el("h3", "Recipe"),
    el("p", `Each record: ${data.recipe.record_selector}`),
    table(["field", "selector", "confidence"], rows),
    el("h3", "Preview (values come from the page)"),
    table(names, data.preview.map((row) => names.map((n) => row[n]))),
  );
  if (data.truncated) out.append(el("p", "Only the first 50 records are shown.", "muted"));
  if (!$("new-file").value) {
    const page = body.page.replace(/\.html?$/i, ".toml");
    $("new-file").value = page;
  }
  if (identity.length === 0) out.append(el("p", "Tick a record key to store records in a dataset.", "muted"));
}

async function createJob(app) {
  const body = collect();
  const file = $("new-file").value.trim();
  if (!file) throw new Error("name the job file");
  const dataset = $("new-dataset").value.trim();
  const made = await api("/api/create", {
    ...body, file, dataset: dataset || null, identity: dataset ? body.identity : [],
  });
  show("create-status", `Created ${made.file}. Opening it…`, "good");
  await app.refreshJobs(made.file);
  app.switchView("jobs");
  await app.openJob(made.file);
}

async function loadPages() {
  const data = await api("/api/pages");
  $("page").replaceChildren(...data.files.map((name) => new Option(name, name)));
  if (!data.files.length) show("suggest-status", "No saved .html pages in this folder yet.", "warn");
  else if (data.truncated) show("suggest-status", "Only the first 500 pages are listed.", "muted");
}

export function initWizard(app) {
  addRow();
  addRow();
  bind("add-field", "suggest-status", () => addRow().focus());
  bind("suggest", "suggest-status", suggestRecipe);
  bind("create", "create-status", () => createJob(app));
  loadPages().catch((error) => show("suggest-status", `Error: ${error.message}`, "bad"));
}
