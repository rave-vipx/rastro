// "Ask AI": shown only when Studio runs with a model. The model proposes a recipe from field
// names alone; Studio keeps it only if it extracts valid records, then fills the example
// values so "Save and open" re-verifies it deterministically.
// All source-derived text is set as plain text content; markup is never parsed.
import { $, api, bind, el, show, state, table } from "./dom.js";
import { t } from "./i18n.js";

function fieldNames() {
  const names = [];
  for (const row of $("field-rows").children) {
    const name = row.querySelector("input").value.trim();
    if (name && !names.includes(name)) names.push(name);
  }
  if (!names.length) throw new Error(t("ai.needName"));
  if (!$("page-url").value.trim()) throw new Error(t("wizard.needUrl"));
  return names;
}

function fillExamples(row) {
  for (const line of $("field-rows").children) {
    const [name, value] = line.querySelectorAll("input");
    const found = row[name.value.trim()];
    if (found !== undefined && found !== null && !value.value.trim()) value.value = String(found);
  }
}

async function askRecipe() {
  show("suggest-status", t("ai.asking"), "muted");
  const data = await api("/api/ai/suggest", { url: $("page-url").value.trim(), fields: fieldNames() });
  const out = $("suggest-result");
  if (!data.found) {
    show("suggest-status", t("ai.notFound", { why: data.failure, n: data.calls }), "warn");
    out.replaceChildren();
    return;
  }
  const names = Object.keys(data.recipe.fields);
  out.replaceChildren(
    el("h4", t("ai.proposed")),
    el("p", t("wizard.eachRecord", { selector: data.recipe.record_selector || t("ai.wholePage") })),
    table([t("col.field"), t("wizard.selector")], names.map((n) => [n, data.recipe.fields[n]])),
    el("h4", t("ai.preview")),
    table(names, data.preview.map((row) => names.map((n) => row[n]))),
  );
  if (data.preview.length) fillExamples(data.preview[0]);
  show("suggest-status", t("ai.verified", { n: data.records }), "good");
}

export async function initAI() {
  const status = await api("/api/ai");
  state.ai = status;
  if (!status.enabled) return;
  for (const id of ["ask-ai", "ai-notice"]) $(id).hidden = false;
  show("ai-notice", t(status.remote ? "ai.noticeRemote" : "ai.noticeLocal"), status.remote ? "warn" : "muted");
  bind("ask-ai", "suggest-status", askRecipe);
}
