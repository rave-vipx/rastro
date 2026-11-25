// Advanced, Settings: the page steps the project replays before reading (often found by the
// AI's look), one by one with Delete, and "Let the AI look again". Text is set as text only.
import { $, api, el, show, state } from "./dom.js";
import { onLangChange, t } from "./i18n.js";
import { actionButton } from "./ui.js";
import { exploreCard } from "./correct.js";

const EMPTY = { steps: [], explore: false };
let shown = EMPTY;
let app = null;

// DOM-free: one plain sentence per recorded step.
export function stepText(step) {
  const known = ["click", "type", "select", "scroll", "wait", "follow"].includes(step.action);
  const name = step.action === "wait" && !step.selector ? "pause" : known ? step.action : "other";
  return t(`steps.${name}`, { where: step.selector || "", text: step.text ?? step.value ?? "", n: step.times ?? 1 });
}

async function remove(step) {
  const data = await api("/api/steps/delete", { file: state.file, sha256: state.sha, source: step.source, index: step.index });
  state.sha = data.sha256;
  shown = { ...shown, steps: data.steps };
  renderSteps();
  show("controls-status", t("steps.deleted"), "good");
}

export function renderSteps() {
  const box = $("steps-section");
  box.replaceChildren(el("h3", t("steps.title")), el("p", t("steps.help"), "muted"));
  if (!state.file) return;
  if (!shown.steps.length) box.append(el("p", t("steps.none"), "muted"));
  const list = el("ol", undefined, "steps-list");
  for (const step of shown.steps) {
    const item = el("li");
    item.append(el("span", stepText(step)), actionButton(t("steps.delete"), () => remove(step), "small"));
    list.append(item);
  }
  if (shown.steps.length) box.append(list);
  if (shown.explore && state.ai && state.ai.enabled && app) {
    const host = el("div");
    box.append(actionButton(t("steps.again"), () => exploreCard(host, app, state.file), "small"), host);
  }
}

export async function loadSteps() {
  try { shown = await api("/api/steps", { file: state.file }); } catch { shown = EMPTY; }
  renderSteps();
}

export function initSteps(studioApp) {
  app = studioApp;
  onLangChange(renderSteps);
}
