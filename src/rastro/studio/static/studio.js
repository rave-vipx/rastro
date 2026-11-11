"use strict";
// All source-derived text is set as plain text content; markup is never parsed.
const $ = (id) => document.getElementById(id);
// The token arrives in the URL fragment (never sent to the server); keep it in memory only.
const token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
history.replaceState(null, "", location.pathname + location.search);
let sha = null;
let selectors = [];

async function api(path, body) {
  const options = body === undefined
    ? {}
    : {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Studio-Token": token },
        body: JSON.stringify(body),
      };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(`${data.error || "error"}${data.message ? ": " + data.message : ""}`);
  }
  return data;
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function show(id, text) { $(id).textContent = text; }

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
  const table = el("table");
  const head = el("tr");
  for (const name of ["field", "value", "method", "locator", "validation", "loaded"]) {
    head.append(el("th", name));
  }
  table.append(head);
  for (const f of record.fields) {
    const row = el("tr");
    const cells = [f.name + (f.identity ? "*" : ""), f.value === null ? "" : String(f.value),
      f.method || "-", f.locator || "-", f.validation, f.loaded ? "yes" : "no"];
    for (const text of cells) row.append(el("td", text));
    table.append(row);
  }
  box.append(table);
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
}

function fillSelectors(rows) {
  selectors = rows;
  const pick = $("selector-pick");
  pick.replaceChildren();
  rows.forEach((row, i) => {
    const label = `${row.source === null ? "recipe" : "source " + row.source} / ${row.field}`;
    pick.append(new Option(label, String(i)));
  });
  showSelector();
}

function showSelector() {
  const row = selectors[Number($("selector-pick").value)];
  $("selector-text").value = row ? row.selector : "";
}

async function guarded(target, action) {
  try { await action(); } catch (error) { show(target, `Error: ${error.message}`); }
}

async function validate() {
  const data = await api("/api/validate", { file: $("files").value });
  sha = data.sha256;
  fillSelectors(data.selectors);
  show("validation", data.has_dataset ? "Valid job." : "Valid job, but it has no [dataset] block: preview needs one.");
}

async function preview() {
  const data = await api("/api/preview", { file: $("files").value, edit: currentEdit() });
  sha = data.sha256;
  renderPreview(data.preview);
}

async function accept() {
  const edit = currentEdit();
  if (!edit) throw new Error("change a selector first");
  const data = await api("/api/accept", { file: $("files").value, edit, sha256: sha });
  sha = data.sha256;
  fillSelectors(data.selectors);
  show("accepted", "Written. The corrected job validated and replaced the file.");
}

async function connect() {
  const data = await api(`/api/connect?file=${encodeURIComponent($("files").value)}`);
  show("commands", `${data.cli.join("\n")}\n\n${data.sdk}\n\n# ${data.note}`);
}

async function start() {
  if (!token) {
    show("listing", "Open the URL printed in the terminal (it carries the session token).");
    return;
  }
  const data = await api("/api/jobs");
  if (data.truncated) show("listing", "Only the first 500 job files are listed.");
  show("root", `Root: ${data.root}`);
  for (const name of data.files) $("files").append(new Option(name, name));
}

$("validate").onclick = () => guarded("validation", validate);
$("preview").onclick = () => guarded("validation", preview);
$("repreview").onclick = () => guarded("validation", preview);
$("accept").onclick = () => guarded("accepted", accept);
$("connect").onclick = () => guarded("commands", connect);
$("selector-pick").onchange = showSelector;
guarded("validation", start);
