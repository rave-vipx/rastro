// One results cell: what a stored value means (text, free, several tiers, a link, or empty
// with its reason) and how it is drawn. cellModel is pure so it can be tested without a page.
import { el } from "./dom.js";
import { t } from "./i18n.js";
import { blockedUrl, emptyCell, trustMarker } from "./ui.js";

const STATES = new Set(["published", "free", "not_published", "source_failed"]);
const FREE = new Set(["free", "gratis"]);
const LIVE = new Set(["en_vivo", "live", "live_now"]);
const URL_LIKE = /^https?:\/\/\S+$/i;
const DATE_LIKE = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?/;

function blank(value) {
  return value === null || value === undefined || (typeof value === "string" && !value.trim());
}

// The readable text of one tier: a string, or the price-like entry of an object.
export function tierText(item) {
  if (item === null || item === undefined) return "";
  if (typeof item !== "object") return String(item);
  for (const key of ["precio", "price", "text", "nombre", "name"]) {
    if (typeof item[key] === "string" || typeof item[key] === "number") return String(item[key]);
  }
  const first = Object.values(item).find((v) => typeof v === "string");
  return first ?? JSON.stringify(item);
}

// Several prices in one value: a list, or text such as "Free / $ 160 / $ 199".
export function tiers(value) {
  if (Array.isArray(value)) return value;
  if (typeof value === "string") {
    const parts = value.split(" / ").map((part) => part.trim()).filter(Boolean);
    return parts.length > 1 ? parts : null;
  }
  return null;
}

export function cellModel(value, reason) {
  if (blank(value)) {
    if (reason === "source_failed") return { kind: "source_failed" };
    return { kind: reason === "not_published" ? "not_published" : "empty" };
  }
  if (typeof value === "object" && !Array.isArray(value) && STATES.has(value.state)) {
    if (value.state !== "published" && value.state !== "free") return { kind: value.state };
    if (blank(value.text)) return { kind: "empty" };
    const model = cellModel(value.text, reason);
    return value.state === "free" ? { kind: "free", text: String(value.text) } : model;
  }
  const several = tiers(value);
  if (several && several.length > 1) {
    return { kind: "tiers", text: tierText(several[0]), n: several.length };
  }
  if (Array.isArray(value)) return several.length ? cellModel(several[0], reason) : { kind: "empty" };
  if (typeof value === "object") return { kind: "text", text: JSON.stringify(value) };
  const text = String(value);
  if (URL_LIKE.test(text.trim())) return { kind: "url", text: text.trim() };
  if (FREE.has(text.trim().toLowerCase())) return { kind: "free", text };
  return { kind: "text", text };
}

export function isLive(record) {
  return Object.values(record.fields || {}).some((v) => typeof v === "string" && LIVE.has(v));
}

// The first field whose values look like dates; the date filter uses it.
export function dateField(fields, records) {
  return fields.find((name) =>
    records.some((r) => typeof r.fields[name] === "string" && DATE_LIKE.test(r.fields[name])));
}

// Where a record came from: the host of its first provenance entry, if the server sent one.
export function sourceOf(record) {
  const first = Object.values(record.provenance || {})[0];
  if (!first || !first.source_url) return "";
  try {
    return new URL(first.source_url).host;
  } catch {
    return String(first.source_url);
  }
}

export function flagged(record, field) {
  return (record.flags || []).some((flag) => flag.field === field);
}

export function renderCell(record, field) {
  const value = record.fields[field];
  if (typeof value === "string" && LIVE.has(value)) return liveChip();
  const model = cellModel(value, (record.empty || {})[field]);
  const box = el("span", undefined, "cell");
  if (model.kind === "text") box.append(document.createTextNode(model.text));
  else if (model.kind === "free") box.append(el("strong", model.text));
  else if (model.kind === "tiers") {
    box.append(document.createTextNode(model.text), " ", el("span", t("cell.tiers", { n: model.n }), "tag"));
  } else if (model.kind === "url") box.append(blockedUrl(model.text));
  else box.append(emptyCell(model.kind === "empty" ? null : model.kind));
  if (flagged(record, field)) box.append(" ", trustMarker());
  return box;
}

export function liveChip() {
  return el("span", t("cell.live"), "chip chip-accent");
}
