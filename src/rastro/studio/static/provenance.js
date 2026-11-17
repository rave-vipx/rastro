// Provenance panel: for the chosen record, where each value came from, when it was observed and
// with which extraction (N5, DATA-004). Flags and imports are explained; values stay plain text.
import { $, el } from "./dom.js";
import { onLangChange, t, when } from "./i18n.js";
import { renderCell, tierText, tiers } from "./cells.js";
import { onRecordSelected, view } from "./results.js";
import { blockedUrl, chip, shape } from "./ui.js";

let current = null;
let returnTo = null;

function line(label, value) {
  const row = el("div", undefined, "kv");
  row.append(el("dt", label));
  const dd = el("dd");
  dd.append(value instanceof Node ? value : document.createTextNode(String(value)));
  row.append(dd);
  return row;
}

function availability(item) {
  const flag = item.disponible ?? item.available;
  if (flag === true) return chip("ok", t("prov.available"));
  if (flag === false) return chip("none", t("prov.soldOut"));
  return null;
}

function tierList(value) {
  const list = el("ul", undefined, "tiers");
  for (const item of tiers(value)) {
    const entry = el("li");
    const name = typeof item === "object" && item ? item.nombre ?? item.name : null;
    entry.append(el("strong", tierText(item)));
    if (name && name !== tierText(item)) entry.append(" · ", String(name));
    const conditions = typeof item === "object" && item ? item.condiciones ?? item.conditions : null;
    if (conditions) entry.append(el("span", String(conditions), "muted block"));
    const state = typeof item === "object" && item ? availability(item) : null;
    if (state) entry.append(" ", state);
    list.append(entry);
  }
  return list;
}

function flagNote(record, field) {
  const flag = (record.flags || []).find((f) => f.field === field);
  if (!flag) return null;
  const box = el("div", undefined, "note note-warn");
  box.append(shape("warn"), el("strong", t("trust.short")), el("p", t("trust.explain")));
  if ((flag.rules || []).length) box.append(el("p", t("prov.rules", { rules: flag.rules.join(", ") }), "muted"));
  return box;
}

function emptyNote(reason) {
  if (reason === "source_failed") return el("p", t("prov.why.sourceFailed"), "muted");
  if (reason === "not_published") return el("p", t("prov.why.notPublished"), "muted");
  return null;
}

function fieldBlock(record, field, shared) {
  const box = el("section", undefined, "prov-field");
  box.append(el("h4", field));
  const value = record.fields[field];
  const several = tiers(value);
  box.append(several && several.length > 1 ? tierList(value) : renderCell(record, field));
  const why = emptyNote((record.empty || {})[field]);
  if (why) box.append(why);
  const flag = flagNote(record, field);
  if (flag) box.append(flag);
  if (!shared) box.append(originMeta((record.provenance || {})[field]));
  return box;
}

function originMeta(origin) {
  const meta = el("dl", undefined, "prov-meta");
  if (!origin) {
    meta.append(line(t("prov.page"), t("prov.notReported")));
    return meta;
  }
  if (origin.source_url) meta.append(line(t("prov.page"), blockedUrl(origin.source_url)));
  meta.append(line(t("prov.observed"), when(origin.observed_at)));
  const how = [origin.method, origin.locator].filter(Boolean).join(" · ");
  if (how) meta.append(line(t("prov.method"), how));
  if (origin.recipe_version !== undefined && origin.recipe_version !== null) {
    meta.append(line(t("prov.recipe"), t("prov.version", { n: origin.recipe_version })));
  }
  return meta;
}

// One origin for every field (the usual case) is shown once, above the fields.
function sharedOrigin(record) {
  const origins = view.fields.map((field) => (record.provenance || {})[field]);
  const first = JSON.stringify(origins[0]);
  return origins.length && origins[0] && origins.every((o) => JSON.stringify(o) === first) ? origins[0] : null;
}

function importedNote(record) {
  if (record.origin !== "adapter") return null;
  const box = el("div", undefined, "note");
  const observed = Object.values(record.provenance || {})[0]?.observed_at || record.last_observed_at;
  box.append(el("strong", t("prov.importedTitle")),
    el("p", t("prov.imported", { imported: when(view.imported), observed: when(observed) })));
  return box;
}

function render() {
  const record = current;
  const body = $("prov-body");
  if (!record) return;
  const title = view.fields.length ? record.fields[view.fields[0]] : "";
  $("prov-record").textContent = title === null || title === undefined ? "" : String(title);
  const parts = [importedNote(record)];
  if (record.stale) parts.push(el("p", t("prov.stale"), "note note-warn"));
  if (!record.provenance) parts.push(el("p", t("prov.notSent"), "muted"));
  parts.push(el("p", t("prov.lastSeen", { at: when(record.last_observed_at) }), "muted"));
  const shared = sharedOrigin(record);
  if (shared) parts.push(el("h3", t("prov.allFields")), originMeta(shared));
  for (const field of view.fields) parts.push(fieldBlock(record, field, shared));
  if (record.truncated) parts.push(el("p", t("prov.truncated"), "muted"));
  body.replaceChildren(...parts.filter(Boolean));
}

export function openProvenance(record, row) {
  current = record;
  for (const selected of document.querySelectorAll("tr.selected")) selected.classList.remove("selected");
  if (row && row.classList) row.classList.add("selected");
  returnTo = row && row.querySelector ? row.querySelector(".row-open") : null;
  render();
  $("provenance").hidden = false;
  $("prov-title").focus();
}

export function closeProvenance() {
  $("provenance").hidden = true;
  current = null;
  for (const selected of document.querySelectorAll("tr.selected")) selected.classList.remove("selected");
  if (returnTo) returnTo.focus();
}

export function initProvenance() {
  onRecordSelected(openProvenance);
  $("prov-close").addEventListener("click", closeProvenance);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("provenance").hidden) closeProvenance();
  });
  onLangChange(render);
}
