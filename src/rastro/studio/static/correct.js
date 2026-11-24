// Correct a field by hand: preview sample records with how each field was found, try another
// selector, and save it to the job file (digest-checked). Values are plain text content.
// Also the guided create's words: estimate, hub offer, the site's own data, the AI's
// narration and the "use it on all rows?" confirmation after a click on the page.
import { $, api, bind, el, requireFile, show, state, table } from "./dom.js";
import { advanced, t, when } from "./i18n.js";
import { actionButton } from "./ui.js";

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

// ---- Guided create: words for the data the wizard routes return (DOM-free) ----

export function estimateText(found) {
  const n = found.requests_max;
  if (found.minutes_max < 1) return t("wizard.est.quick", { n });
  const low = Math.max(1, Math.floor(found.minutes_min));
  return t("wizard.est.text", { n, min: low, max: Math.max(low, Math.ceil(found.minutes_max)) });
}

const VERBS = ["click", "type", "select", "scroll", "wait", "follow"];

export function narrate(event) {
  const verb = t(`wizard.verb.${VERBS.includes(event.action) ? event.action : "step"}`);
  const target = event.target || "";
  if (event.kind === "observed") return t("wizard.ev.observed", { n: event.records });
  if (event.kind === "stopped") return t("wizard.ev.stopped", { n: event.records });
  if (event.kind === "kept") return t("wizard.ev.kept", { verb, target, n: event.added });
  return t("wizard.ev.dropped", { verb, target });
}

export function hubLines(offer, host) {
  const lines = [t("wizard.hub.title", { host })];
  if (offer.last_verified) lines.push(t("wizard.hub.meta", { date: when(offer.last_verified) }));
  const fields = offer.card && offer.card.fields ? offer.card.fields.map((f) => f.name) : [];
  if (fields.length) lines.push(t("wizard.hub.fields", { fields: fields.join(", ") }));
  lines.push(t("wizard.hub.version", { id: offer.id, version: offer.version }));
  const needs = offer.card && offer.card.engine_needs ? offer.card.engine_needs.capabilities : [];
  if (needs && needs.length) lines.push(t("wizard.hub.needs", { needs: needs.join(", ") }));
  return lines;
}

export function offerLines(offer) {
  const lines = [t("wizard.api.count", { n: offer.records })];
  lines.push(offer.covers.length ? t("wizard.api.covers", { fields: offer.covers.join(", ") }) : t("wizard.api.none"));
  if (advanced()) lines.push(t("wizard.api.where", { where: `${offer.host}${offer.path}`, pointer: offer.record_path || "/" }));
  return lines;
}

// "Got it: <text>. Use it on all N rows?" with the field's values before and after.
export function confirmPick(box, picked, before, actions) {
  box.replaceChildren();
  const status = el("p", t("correct.pick.got", { text: picked.sample[0] || "", n: picked.matches }));
  status.setAttribute("role", "status");
  const pair = (title, values) => {
    const part = el("div");
    part.append(el("h4", title), el("p", values.slice(0, 3).join(" · ") || "-", "muted"));
    return part;
  };
  const row = el("div", undefined, "row");
  row.append(actionButton(t("correct.pick.use"), actions.use, "primary"),
    actionButton(t("correct.pick.other"), actions.other), actionButton(t("wizard.pick.cancel"), actions.cancel));
  box.append(status, pair(t("correct.pick.before"), before), pair(t("correct.pick.after"), picked.sample), row);
}

// The AI looks once for records behind a click; its narration, then keep or leave it.
export function exploreCard(box, app, file) {
  const log = el("ol", undefined, "explore-log");
  log.setAttribute("aria-live", "polite");
  const consent = el("input");
  consent.type = "checkbox";
  const consentLabel = el("label");
  consentLabel.append(consent, document.createTextNode(` ${t("wizard.explore.consent")}`));
  const open = actionButton(t("wizard.explore.skip"), () => app.openJob(file).then(() => app.switchView("results")));
  const go = actionButton(t("wizard.explore.go"), async () => {
    log.replaceChildren(el("li", t("wizard.explore.working")));
    try {
      const data = await api("/api/wizard/explore", { file, allow_remote: consent.checked });
      log.replaceChildren(...data.events.map((event) => el("li", narrate(event))));
      box.append(el("p", t(data.kept ? "wizard.explore.result" : "wizard.explore.nothing", { before: data.before, after: data.after })));
      if (data.kept) box.append(actionButton(t("wizard.explore.keep"), async () => {
        await api("/api/wizard/explore/keep", { file });
        show("create-status", t("wizard.explore.kept"), "good");
        await app.openJob(file);
        app.switchView("results");
      }, "primary"));
    } catch (error) { log.replaceChildren(el("li", t("msg.error", { why: error.message }))); }
  }, "primary");
  box.replaceChildren(el("p", t("wizard.explore.offer")), el("p", t("wizard.explore.key"), "muted"));
  if (state.ai.remote) box.append(consentLabel);
  const row = el("div", undefined, "row");
  row.append(go, open);
  box.append(row, el("h4", t("wizard.explore.log")), log);
}

export function hubCard(offer, host, onTry, onMine) {
  const card = el("div", undefined, "card hub-offer");
  const [title, ...rest] = hubLines(offer, host);
  card.append(el("h4", title), ...rest.map((line) => el("p", line, "muted")), el("p", t("wizard.hub.trust")));
  const row = el("div", undefined, "row");
  row.append(actionButton(t("wizard.hub.try"), onTry, "primary"), actionButton(t("wizard.hub.mine"), onMine));
  card.append(row);
  return card;
}

export function offerCard(offer, onUse) {
  const card = el("div", undefined, "card api-offer");
  card.append(el("h4", t("wizard.api.title")), ...offerLines(offer).map((line) => el("p", line)),
    el("p", t("wizard.api.later"), "muted"));
  if (offer.covers.length) card.append(actionButton(t("wizard.api.use"), onUse, "primary"));
  return card;
}

// "Use this data instead": a job that reads the response the page loads, then its results.
export async function useOffer(app, url, offer, file) {
  const made = await api("/api/wizard/offers/use", { url, host: offer.host, path: offer.path, fields: offer.covers, file });
  show("suggest-status", t("wizard.api.made", { file: made.file }), "good");
  await app.refreshJobs(made.file);
  await app.openJob(made.file);
  app.switchView("results");
}

// "More pages?" (Yes / Not sure / No) and the estimate sentence; returns a refresh function.
export function estimateCard(box, fetchEstimate) {
  let more = false;
  const out = el("p");
  out.setAttribute("aria-live", "polite");
  const refresh = async () => {
    out.textContent = t("state.loading");
    try { out.textContent = estimateText(await fetchEstimate(more)); }
    catch (error) { out.textContent = t("msg.error", { why: error.message }); }
  };
  const group = el("fieldset");
  group.append(el("legend", t("wizard.est.more")));
  const choices = [["wizard.est.yes", true], ["wizard.est.unsure", true], ["wizard.est.no", false]].map(([key, value]) => {
    const choice = el("button", t(key), "small");
    choice.type = "button";
    choice.setAttribute("aria-pressed", String(!value));
    choice.addEventListener("click", () => {
      for (const other of choices) other.setAttribute("aria-pressed", String(other === choice));
      more = value;
      return refresh();
    });
    return choice;
  });
  group.append(...choices);
  box.append(el("h4", t("wizard.est.title")), group, out);
  return refresh;
}
