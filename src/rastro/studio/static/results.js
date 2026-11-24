// Results: the stored records with search, filters, pages and export.
// Records are read in bounded chunks and filtered here; the page says when only part is covered.
import { $, api, bind, el, requireFile, show } from "./dom.js";
import { onLangChange, t, when } from "./i18n.js";
import { dateField, isLive, renderCell, sourceOf } from "./cells.js";
import { chip, emptyState } from "./ui.js";

const CHUNK = 100;
const MAX_LOCAL = 2000;
const DAY = 86_400_000;
export const view = { records: [], fields: [], total: 0, page: 0, error: "", ready: false, imported: null };
let onSelect = () => {};

export function onRecordSelected(handler) {
  onSelect = handler;
}

export function resetResults() {
  Object.assign(view, { records: [], fields: [], total: 0, page: 0, error: "", ready: false, imported: null });
  $("dataset-result").replaceChildren();
  $("provenance").hidden = true;
  show("dataset-status", "");
  show("rs-range", "");
}

export async function loadRecords() {
  const file = requireFile();
  show("dataset-status", t("results.loading"), "muted");
  try {
    const first = await api("/api/records", { file, offset: 0, limit: CHUNK });
    const records = [...first.records];
    const wanted = Math.min(first.total, MAX_LOCAL);
    while (records.length < wanted) {
      const next = await api("/api/records", { file, offset: records.length, limit: CHUNK });
      if (!next.records.length) break;
      records.push(...next.records);
    }
    Object.assign(view, { records, fields: first.fields, total: first.total, page: 0, error: "",
      ready: true, imported: first.imported_at || null, stale: first.stale_sources || 0 });
  } catch (error) {
    Object.assign(view, { records: [], fields: [], total: 0, error: error.message, ready: true });
  }
  fillSources();
  render();
}

function fillSources() {
  const pick = $("rs-source");
  const before = pick.value;
  const hosts = [...new Set(view.records.map(sourceOf).filter(Boolean))].sort();
  pick.replaceChildren(new Option(t("results.allSources"), ""), ...hosts.map((h) => new Option(h, h)));
  pick.value = hosts.includes(before) ? before : "";
  $("rs-source-wrap").hidden = hosts.length < 2;
  $("rs-date-wrap").hidden = !dateField(view.fields, view.records);
}

function inDateRange(value, range, now) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;
  const diff = date.getTime() - now.getTime();
  if (range === "week") return diff >= -DAY / 4 && diff <= 7 * DAY;
  if (range === "month") return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth();
  if (range === "weekend") {
    const day = date.getDay();
    return diff >= -DAY / 4 && diff <= 7 * DAY && (day === 0 || day === 5 || day === 6);
  }
  return true;
}

export function filtered() {
  const query = $("rs-search").value.trim().toLowerCase();
  const source = $("rs-source").value;
  const status = $("rs-state").value;
  const range = $("rs-date").value;
  const dated = dateField(view.fields, view.records);
  const now = new Date();
  return view.records.filter((record) => {
    if (source && sourceOf(record) !== source) return false;
    if (status === "live" && !isLive(record)) return false;
    if (status === "upcoming" && isLive(record)) return false;
    if (status === "stale" && !record.stale) return false;
    if (range && dated && !inDateRange(record.fields[dated], range, now)) return false;
    if (!query) return true;
    return Object.values(record.fields).some((v) => v !== null && JSON.stringify(v).toLowerCase().includes(query));
  });
}

function row(record) {
  const line = el("tr");
  line.addEventListener("click", () => onSelect(record, line));
  view.fields.forEach((name, i) => {
    const cell = el("td");
    cell.dataset.label = name;
    if (i === 0) {
      const open = el("button", undefined, "row-open");
      open.type = "button";
      open.append(renderCell(record, name));
      open.setAttribute("aria-label", t("results.openRecord", { name: open.textContent }));
      open.addEventListener("click", (event) => { event.stopPropagation(); onSelect(record, line); });
      cell.append(open);
    } else cell.append(renderCell(record, name));
    line.append(cell);
  });
  const where = el("td");
  where.dataset.label = t("results.col.source");
  where.append(sourceOf(record) || t("results.noSource"));
  if (record.stale) where.append(" ", chip("warn", t("results.stale")));
  line.append(where);
  return line;
}

function grid(records) {
  const wrap = el("div", undefined, "scroll");
  const table = el("table", undefined, "results");
  table.append(el("caption", t("results.caption"), "sr"));
  const head = el("tr");
  for (const name of [...view.fields, t("results.col.source")]) {
    const th = el("th", name);
    th.scope = "col";
    head.append(th);
  }
  table.append(el("thead"), el("tbody"));
  table.tHead.append(head);
  table.tBodies[0].append(...records.map(row));
  wrap.append(table);
  return wrap;
}

function clearFilters() {
  for (const id of ["rs-search", "rs-source", "rs-state", "rs-date"]) $(id).value = "";
  view.page = 0;
  render();
}

export function render() {
  const out = $("dataset-result");
  if (!view.ready) return;
  if (view.error) {
    show("dataset-status", "");
    show("rs-range", "");
    out.replaceChildren(emptyState(t("results.none"), t("results.noneHelp", { why: view.error })));
    return;
  }
  const all = filtered();
  const size = Number($("rs-per-page").value) || 25;
  const pages = Math.max(1, Math.ceil(all.length / size));
  view.page = Math.min(Math.max(0, view.page), pages - 1);
  const start = view.page * size;
  const shown = all.slice(start, start + size);
  show("rs-range", t("results.range", { from: all.length ? start + 1 : 0, to: start + shown.length, total: all.length }));
  const notes = [t("results.untrusted")];
  if (all.length !== view.records.length) notes.unshift(t("results.filtered", { n: view.records.length }));
  if (view.total > view.records.length) notes.unshift(t("results.partial", { n: view.records.length, total: view.total }));
  if (view.stale) notes.push(t("results.staleSources", { n: view.stale }));
  if (view.imported) notes.unshift(t("results.imported", { at: when(view.imported) }));
  show("dataset-status", notes.join(" "), "muted");
  $("prev-page").disabled = view.page === 0;
  $("next-page").disabled = view.page >= pages - 1;
  if (!view.records.length) {
    out.replaceChildren(emptyState(t("results.empty"), t("results.emptyHelp")));
  } else if (!all.length) {
    const clear = el("button", t("results.clear"));
    clear.type = "button";
    clear.addEventListener("click", clearFilters);
    out.replaceChildren(emptyState(t("results.noMatch"), "", clear));
  } else out.replaceChildren(grid(shown));
}

async function download(format) {
  const data = await api("/api/export", { file: requireFile(), format });
  const url = URL.createObjectURL(new Blob([data.content], { type: data.media_type }));
  const link = el("a");
  Object.assign(link, { href: url, download: data.filename });
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  show("export-status", t("results.downloaded", { file: data.filename }), "good");
}

export function initResults() {
  for (const id of ["rs-search", "rs-source", "rs-state", "rs-date", "rs-per-page"]) {
    $(id).addEventListener(id === "rs-search" ? "input" : "change", () => { view.page = 0; render(); });
  }
  $("prev-page").addEventListener("click", () => { view.page -= 1; render(); });
  $("next-page").addEventListener("click", () => { view.page += 1; render(); });
  bind("dataset", "dataset-status", loadRecords);
  bind("export-json", "export-status", () => download("json"));
  bind("export-html", "export-status", () => download("html"));
  onLangChange(() => { if (view.ready) { fillSources(); render(); } });
}
