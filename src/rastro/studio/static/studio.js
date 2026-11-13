// Studio entry: session token, views, job list, preview and selector correction.
// All source-derived text is set as plain text content; markup is never parsed.
import { $, api, bind, el, requireFile, show, state, table } from "./dom.js";
import { initOperate, resetOperate } from "./operate.js";
import { initWizard } from "./wizard.js";

// The token arrives in the URL fragment (never sent to the server); keep it in memory only.
state.token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
history.replaceState(null, "", location.pathname + location.search);
let selectors = [];

function currentEdit() {
  const picked = selectors[Number($("selector-pick").value)];
  const selector = $("selector-text").value;
  if (!picked || selector === "" || selector === picked.selector) return undefined;
  return { source: picked.source, field: picked.field, selector };
}

function renderRecord(record) {
  const box = el("div");
  if (record.status === "rejected") {
    const why = record.reasons.map((r) => `${r.code}(${r.field || "-"})`).join(", ");
    box.append(el("p", `Record ${record.index} rejected: ${why}`, "rejected"));
    return box;
  }
  box.append(el("p", `Record ${record.index} accepted`));
  const rows = record.fields.map((f) => [
    f.name + (f.identity ? " (key)" : ""), f.value === null ? "" : String(f.value),
    f.method || "-", f.locator || "-", f.validation, f.loaded ? "yes" : "no",
  ]);
  box.append(table(["field", "value", "method", "locator", "validation", "loaded"], rows));
  return box;
}

function renderPreview(preview) {
  const out = $("records");
  out.replaceChildren();
  for (const source of preview.sources) {
    out.append(el("h3", `${source.source_url} [${source.status}]`));
    for (const issue of source.issues) out.append(el("p", `issue: ${issue.code}`, "rejected"));
    for (const record of source.records) out.append(renderRecord(record));
  }
  if (preview.truncated) out.append(el("p", "Long values and lists were shortened.", "muted"));
}

function fillSelectors(rows) {
  selectors = rows;
  const pick = $("selector-pick");
  pick.replaceChildren();
  rows.forEach((row, i) => {
    const label = `${row.source === null ? "recipe" : "source " + (row.source + 1)} / ${row.field}`;
    pick.append(new Option(label, String(i)));
  });
  showSelector();
}

function showSelector() {
  const row = selectors[Number($("selector-pick").value)];
  $("selector-text").value = row ? row.selector : "";
}

export async function openJob(name) {
  const data = await api("/api/validate", { file: name });
  state.file = name;
  state.sha = data.sha256;
  fillSelectors(data.selectors);
  resetOperate();
  const notes = [`${data.sources} source(s)`];
  if (!data.has_dataset) notes.push("no [dataset] block: preview and load need one");
  if (data.has_memory) notes.push("recipe memory on: repairs are available");
  show("validation", `Valid job. ${notes.join("; ")}.`, "good");
}

async function preview() {
  const data = await api("/api/preview", { file: requireFile(), edit: currentEdit() });
  state.sha = data.sha256;
  renderPreview(data.preview);
  show("accepted", "");
}

async function accept() {
  const edit = currentEdit();
  if (!edit) throw new Error("change a selector first");
  const data = await api("/api/accept", { file: requireFile(), edit, sha256: state.sha });
  state.sha = data.sha256;
  fillSelectors(data.selectors);
  show("accepted", "Saved. The corrected job validated and replaced the file.", "good");
}

async function connect() {
  const data = await api(`/api/connect?file=${encodeURIComponent(requireFile())}`);
  show("commands", `${data.cli.join("\n")}\n\n${data.sdk}\n\n# ${data.note}`);
}

export async function refreshJobs(select) {
  const data = await api("/api/jobs");
  if (data.truncated) show("listing", "Only the first 500 job files are listed.");
  show("root", `Folder: ${data.root}`, "muted");
  const list = $("files");
  list.replaceChildren(...data.files.map((name) => new Option(name, name)));
  if (select) list.value = select;
}

export function switchView(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    const active = tab.dataset.view === name;
    tab.setAttribute("aria-pressed", String(active));
    $(`view-${tab.dataset.view}`).hidden = !active;
  }
}

async function start() {
  if (!state.token) {
    show("listing", "Open the address printed in the terminal (it carries the session token).", "bad");
    return;
  }
  await refreshJobs();
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  }
  bind("validate", "validation", () => openJob($("files").value));
  bind("preview", "accepted", preview);
  bind("repreview", "accepted", preview);
  bind("accept", "accepted", accept);
  bind("connect", "commands", connect);
  $("selector-pick").addEventListener("change", showSelector);
  initOperate();
  initWizard({ openJob, refreshJobs, switchView });
}

start().catch((error) => show("listing", `Error: ${error.message}`, "bad"));
