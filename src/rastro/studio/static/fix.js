// Fix a site: find a proposed recipe, compare it field by field, then apply it with a
// reason or discard it. Applying re-runs every check; Studio cannot override a failed one.
import { $, api, el, requireFile, show, state, switchView } from "./dom.js";
import { advanced, onLangChange, t } from "./i18n.js";
import { showAlerts, withProgress } from "./controls.js";
import { sources } from "./sources.js";
import { actionButton, chip, emptyState } from "./ui.js";

const DECISION = { promote: ["ok", "fix.decision.promote"], quarantine: ["warn", "fix.decision.quarantine"],
  reject: ["fail", "fix.decision.reject"] };

function status(text, tone) {
  show("fix-status", text, tone || "muted");
}

export function fillFixSources(count) {
  const pick = $("fix-source");
  const options = [];
  for (let i = 1; i <= count; i += 1) {
    const url = sources.list[i - 1]?.url;
    options.push(new Option(url ? t("fix.sourceNamed", { n: i, url }) : t("fix.sourceN", { n: i }), String(i)));
  }
  const before = pick.value;
  pick.replaceChildren(...options);
  if (before && Number(before) <= count) pick.value = before;
  $("fix-find").disabled = !state.hasMemory || !count;
  $("fix-memory-note").hidden = !!state.hasMemory;
}

export function resetFix() {
  $("fix-result").replaceChildren();
  $("fix-alerts").replaceChildren();
  status("");
}

function code(text) {
  return text === null || text === undefined ? el("span", t("fix.none"), "muted") : el("code", text);
}

// Before/after per field. The repair report names how each field is found (its locator), not
// the values, so the change column says whether the way of reading that field changed.
function comparisonTable(diff) {
  const wrap = el("div", undefined, "scroll");
  const grid = el("table", undefined, "before-after");
  const head = el("tr");
  for (const key of ["fix.col.field", "fix.col.before", "fix.col.after", "fix.col.change"]) {
    const th = el("th", t(key));
    th.scope = "col";
    head.append(th);
  }
  grid.append(el("thead"), el("tbody"));
  grid.tHead.append(head);
  const [oldRecord, newRecord] = diff.record_selector;
  const rows = [{ field: t("fix.recordRow"), old: oldRecord, new: newRecord, changed: oldRecord !== newRecord },
    ...diff.fields];
  for (const row of rows) {
    const line = el("tr", undefined, row.changed ? "changed" : "");
    const cells = [el("strong", row.field), code(row.old), code(row.new),
      row.changed ? chip("warn", t("fix.changed")) : chip("ok", t("fix.same"))];
    for (const [i, node] of cells.entries()) {
      const td = el("td");
      td.dataset.label = t(["fix.col.field", "fix.col.before", "fix.col.after", "fix.col.change"][i]);
      td.append(node);
      line.append(td);
    }
    grid.tBodies[0].append(line);
  }
  wrap.append(grid);
  return wrap;
}

function checks(ev) {
  const row = el("div", undefined, "checks");
  const [tone, key] = DECISION[ev.decision] || DECISION.quarantine;
  row.append(chip(tone, t(key)));
  row.append(el("span", t("fix.records", { n: ev.current_records })));
  row.append(el("span", t("fix.history", { passed: ev.historical_passed, total: ev.historical_fixtures })));
  for (const gate of ev.regressions || []) {
    row.append(chip(gate.passed ? "ok" : "fail", `${gate.name}: ${t(gate.passed ? "fix.pass" : "fix.fail")}`));
  }
  return row;
}

async function writeToJob(source) {
  const data = await api("/api/apply", { file: requireFile(), source, sha256: state.sha });
  state.sha = data.sha256;
  status(data.changed ? t("fix.written", { n: data.recipe_version }) : t("fix.alreadyWritten"), "good");
}

// One approve button. The reason field is a technical detail: it shows only with Advanced on;
// otherwise Studio records a fixed reason. Promotion still re-runs every check.
function decision(source, version, box) {
  const form = el("div", undefined, "decision");
  const id = `fix-reason-${source}-${version}`;
  const reason = el("input");
  Object.assign(reason, { id, type: "text", maxLength: 500, required: true, value: t("fix.defaultReason") });
  if (advanced()) {
    const label = el("label", t("fix.reason"));
    label.htmlFor = id;
    form.append(label, reason);
  }
  const apply = actionButton(t(advanced() ? "fix.apply" : "fix.approve"), async () => {
    try {
      if (!reason.value.trim()) throw new Error(t("fix.reasonNeeded"));
      const data = await withProgress(() => api("/api/promote", { file: requireFile(), source, version, reason: reason.value }));
      showAlerts("fix-alerts", data.alerts);
      const done = data.report.sources[0].promoted_version;
      if (!done) return status(t("fix.refused"), "warn");
      status(t("fix.applied", { n: done }), "good");
      form.replaceChildren(el("p", t("fix.serving"), "good"), actionButton(t("fix.write"), () => writeToJob(source).catch(fail)));
    } catch (error) {
      fail(error);
    }
  }, "primary");
  const discard = actionButton(t("fix.discard"), () => {
    box.replaceChildren(el("p", t("fix.discarded"), "muted"));
    status(t("fix.discarded"));
  });
  const buttons = el("div", undefined, "row");
  buttons.append(apply, discard);
  form.append(buttons, el("p", t(advanced() ? "fix.applyNote" : "fix.approveHelp"), "muted"));
  return form;
}

function candidate(source, comparison, promoted) {
  const box = el("article", undefined, "candidate");
  const ev = comparison.evaluation;
  box.append(el("h3", t("fix.proposal", { n: comparison.candidate_version, old: comparison.previous_version })));
  if (ev) box.append(checks(ev));
  if (comparison.diff) box.append(comparisonTable(comparison.diff));
  if (ev && ev.reasons.length) box.append(el("p", t("fix.reasons", { reasons: ev.reasons.join("; ") }), "muted"));
  if (promoted === comparison.candidate_version) {
    box.append(el("p", t("fix.serving"), "good"), actionButton(t("fix.write"), () => writeToJob(source).catch(fail)));
  } else if (ev && ev.decision === "reject") box.append(el("p", t("fix.rejected"), "bad"));
  else box.append(decision(source, comparison.candidate_version, box));
  return box;
}

function fail(error) {
  status(t("msg.error", { why: error.message }), "bad");
}

function showReport(source, report, heading) {
  const item = report.sources[0];
  const out = $("fix-result");
  out.replaceChildren(el("h3", heading));
  if (item.issues && item.issues.length) out.append(el("p", t("fix.issues", { codes: item.issues.map((i) => i.code).join(", ") }), "warn"));
  if (!item.comparisons.length) out.append(emptyState(t("fix.noneFound"), t("fix.noneFoundHelp")));
  for (const comparison of item.comparisons) out.append(candidate(source, comparison, item.promoted_version));
  const usable = item.comparisons.some((c) => !c.evaluation || c.evaluation.decision !== "reject");
  if (state.ai.enabled && !usable && !item.promoted_version) {
    out.append(actionButton(t("fix.askAi"), () => askModel(source).catch(fail)));
  }
  status(item.promoted_version ? t("fix.applied", { n: item.promoted_version }) : t("fix.review"),
    item.promoted_version ? "good" : "muted");
}

async function findFix() {
  const source = Number($("fix-source").value);
  const notice = state.ai.enabled ? ` ${t(state.ai.remote ? "fix.modelRemote" : "fix.model")}` : "";
  status(t("fix.looking", { n: source }) + notice);
  const data = await withProgress(() => api("/api/repair", { file: requireFile(), source }));
  showAlerts("fix-alerts", data.alerts);
  showReport(source, data.report, t("fix.resultTitle", { n: source }));
}

// Repair with the model: the same gates and the semantic judge; promoted on its own only when
// the job allows automatic promotion and the judge agrees.
async function askModel(source) {
  status(t("fix.asking", { n: source }) + (state.ai.remote ? ` ${t("fix.modelRemote")}` : ""));
  const { report } = await api("/api/ai/repair", { file: requireFile(), source });
  showReport(source, report, t("fix.aiTitle", { n: source, calls: report.sources[0].inference_calls || 0 }));
}

export function initFix() {
  $("fix-find").addEventListener("click", () => {
    $("fix-find").disabled = true;
    findFix().catch(fail).finally(() => { $("fix-find").disabled = !state.hasMemory; });
  });
  document.addEventListener("studio:fix", (event) => {
    switchView("repair");
    const source = event.detail && event.detail.source;
    if (source) $("fix-source").value = String(source);
    $("fix-find").focus();
  });
  onLangChange(() => fillFixSources(state.sourceCount || 0));
}
