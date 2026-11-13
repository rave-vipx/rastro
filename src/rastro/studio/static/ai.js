// "Ask AI": shown only when Studio runs with a model. The model proposes a recipe from field
// names alone; Studio keeps it only if it extracts valid records, then fills the example
// values so "Create job" re-verifies it deterministically.
// All source-derived text is set as plain text content; markup is never parsed.
import { $, api, bind, el, show, state, table } from "./dom.js";

function fieldNames() {
  const names = [];
  for (const row of $("field-rows").children) {
    const name = row.querySelector("input").value.trim();
    if (name && !names.includes(name)) names.push(name);
  }
  if (!names.length) throw new Error("name at least one field");
  if (!$("page").value) throw new Error("pick a saved page first");
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
  show("suggest-status", "Asking the model for a recipe…", "muted");
  const data = await api("/api/ai/suggest", {
    page: $("page").value, url: $("page-url").value.trim(), fields: fieldNames(),
  });
  const out = $("suggest-result");
  if (!data.found) {
    show("suggest-status", `The model found no verified recipe (${data.failure}, ${data.calls} call(s)). Give example values and use Suggest.`, "warn");
    out.replaceChildren();
    return;
  }
  const names = Object.keys(data.recipe.fields);
  out.replaceChildren(
    el("h3", "Recipe proposed by the model, verified on the page"),
    el("p", `Each record: ${data.recipe.record_selector || "(whole page)"}`),
    table(["field", "selector"], names.map((n) => [n, data.recipe.fields[n]])),
    el("h3", "Preview (values come from the page; check they mean what each field says)"),
    table(names, data.preview.map((row) => names.map((n) => row[n]))),
  );
  if (data.preview.length) fillExamples(data.preview[0]);
  show("suggest-status", `Verified: ${data.records} records. Example values were filled from the first record; check them, then Create job.`, "good");
}

export async function initAI() {
  const status = await api("/api/ai");
  state.ai = status;
  if (!status.enabled) return;
  for (const id of ["ask-ai", "ai-notice"]) $(id).hidden = false;
  show("ai-notice", status.remote
    ? "Ask AI sends a page outline (tags, classes and short text) to a remote model, as allowed by --allow-remote."
    : "Ask AI sends a page outline to the configured local model.", status.remote ? "warn" : "muted");
  bind("ask-ai", "suggest-status", askRecipe);
}
