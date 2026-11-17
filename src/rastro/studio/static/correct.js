// Correct a field by hand: preview sample records with how each field was found, try another
// selector, and save it to the job file (digest-checked). Values are plain text content.
import { $, api, bind, el, requireFile, show, state, table } from "./dom.js";
import { t } from "./i18n.js";

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
    box.append(el("p", t("correct.rejected", { n: record.index, why }), "rejected"));
    return box;
  }
  box.append(el("p", t("correct.accepted", { n: record.index })));
  const rows = record.fields.map((f) => [
    f.name + (f.identity ? ` ${t("correct.key")}` : ""), f.value === null ? "" : String(f.value),
    f.method || "-", f.locator || "-", f.validation, t(f.loaded ? "correct.yes" : "correct.no"),
  ]);
  const heads = ["field", "value", "method", "locator", "validation", "loaded"].map((k) => t(`col.${k}`));
  box.append(table(heads, rows));
  return box;
}

function renderPreview(preview) {
  const out = $("records");
  out.replaceChildren();
  for (const source of preview.sources) {
    out.append(el("h3", `${source.source_url} [${source.status}]`));
    for (const issue of source.issues) out.append(el("p", t("correct.issue", { code: issue.code }), "rejected"));
    for (const record of source.records) out.append(renderRecord(record));
  }
  if (preview.truncated) out.append(el("p", t("correct.truncated"), "muted"));
}

export function fillSelectors(rows) {
  selectors = rows;
  const pick = $("selector-pick");
  pick.replaceChildren();
  rows.forEach((row, i) => {
    const where = row.source === null ? t("correct.recipe") : t("correct.source", { n: row.source + 1 });
    pick.append(new Option(`${where} / ${row.field}`, String(i)));
  });
  showSelector();
}

function showSelector() {
  const row = selectors[Number($("selector-pick").value)];
  $("selector-text").value = row ? row.selector : "";
}

async function preview() {
  const data = await api("/api/preview", { file: requireFile(), edit: currentEdit() });
  state.sha = data.sha256;
  renderPreview(data.preview);
  show("accepted", "");
}

async function accept() {
  const edit = currentEdit();
  if (!edit) throw new Error(t("correct.changeFirst"));
  const data = await api("/api/accept", { file: requireFile(), edit, sha256: state.sha });
  state.sha = data.sha256;
  fillSelectors(data.selectors);
  show("accepted", t("correct.saved"), "good");
}

export function resetCorrect() {
  $("records").replaceChildren();
  show("accepted", "");
}

export function initCorrect() {
  bind("preview", "accepted", preview);
  bind("repreview", "accepted", preview);
  bind("accept", "accepted", accept);
  $("selector-pick").addEventListener("change", showSelector);
}
