// What changed since the last load, and why a source did not finish, in plain words.
// chipsModel and partialLines are pure; mountChanges draws them above the results table.
import { el } from "./dom.js";
import { t } from "./i18n.js";
import { chip, emptyState } from "./ui.js";

const KINDS = [["new", "ok", "+"], ["changed", "warn", "↔"], ["removed", "fail", "−"]];

// first: nothing to compare yet; quiet: compared and nothing moved; ready: chips to show.
export function chipsModel(summary) {
  if (!summary || summary.first_run) return { state: "first", chips: [] };
  const chips = KINDS.map(([kind, tone, sign]) => ({
    kind, tone, sign, count: summary[kind] || 0,
    filterable: kind !== "changed",  // the store keeps no earlier values to list changed rows
  }));
  return { state: chips.some((c) => c.count) ? "ready" : "quiet", chips };
}

export function partialLines(list) {
  return (list || []).map(({ host, why }) => ({ host, why }));
}

let box = null;
let notes = null;

function mount() {
  if (box) return;
  box = el("div");
  notes = el("div");
  notes.setAttribute("role", "status");
  const anchor = document.getElementById("export-status");
  if (anchor && anchor.before) anchor.before(box, notes);
}

function chipButton(item, active, pick) {
  const label = `${item.sign} ${t(`changes.${item.kind}`, { n: item.count })}`;
  if (!item.filterable) return chip(item.tone, label);
  const button = el("button", undefined, "chip-button");
  button.type = "button";
  button.setAttribute("aria-pressed", String(active === item.kind));
  button.disabled = item.count === 0;
  button.append(chip(item.tone, label));
  button.addEventListener("click", () => pick(active === item.kind ? "" : item.kind));
  return button;
}

export function renderChanges(summary, active, pick) {
  mount();
  const model = chipsModel(summary);
  const row = el("div", undefined, "row");
  if (model.state === "first") {
    box.replaceChildren(emptyState(t("changes.firstTitle"), t("changes.firstHelp")));
  } else if (model.state === "quiet") {
    box.replaceChildren(el("p", t("changes.quiet"), "muted"));
  } else {
    row.append(el("span", t("changes.title"), "label"), ...model.chips.map((c) => chipButton(c, active, pick)));
    box.replaceChildren(row);
  }
  const lines = partialLines(summary && summary.partial);
  notes.replaceChildren(...lines.map((line) => el("p", t(`partial.${line.why}`, { host: line.host }), "muted")));
}

export function clearChanges() {
  if (box) box.replaceChildren();
  if (notes) notes.replaceChildren();
}
