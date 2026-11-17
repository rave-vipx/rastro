// Run (extract, load) and the technical health table for the open job; Results shows the
// stored records and Fix handles repairs. Source-derived text is always plain text content.
import { $, api, bind, el, request, requireFile, show, table } from "./dom.js";
import { t } from "./i18n.js";
import { showAlerts, withProgress } from "./controls.js";
import { loadRecords } from "./results.js";
import { actionButton, chip } from "./ui.js";

import { spiderLine } from "./bounds.js";

export function resetOperate() {
  for (const id of ["run-result", "health-result"]) $(id).replaceChildren();
  for (const id of ["run-status", "health-status", "operation-progress", "failure-command"]) show(id, "");
  for (const id of ["run-alerts", "health-alerts"]) $(id).replaceChildren();
  $("failure-hint").hidden = true;
}

export function origin(source) {
  if (!source.recipe_origin) return "-";
  const version = source.recipe_version === null ? t("run.newRecipe") : `v${source.recipe_version}`;
  return t(source.recipe_origin === "memory" ? "run.fromMemory" : "run.fromJob", { version });
}

export function issues(list) {
  return (list || []).map((i) => i.code + (i.field ? `(${i.field})` : "")).join(", ") || t("run.noIssues");
}

function headers(keys) {
  return keys.map((key) => t(`col.${key}`));
}

async function extract() {
  const data = await withProgress(() => api("/api/extract", { file: requireFile() }));
  showAlerts("run-alerts", data.alerts);
  const j = data.job;
  show("run-status", `${t("run.extracted", { status: j.status, accepted: j.records_accepted, rejected: j.records_rejected })} ${spiderLine(data.sources)}`,
    j.status === "success" ? "good" : "warn");
  const rows = data.sources.map((s) => [s.source_index, s.source_url, s.status, s.records, origin(s), issues(s.issues)]);
  $("run-result").replaceChildren(table(headers(["n", "source", "status", "records", "recipe", "issues"]), rows));
}

async function load() {
  const data = await withProgress(() => api("/api/load", { file: requireFile(), drift: $("drift").checked }));
  const { result } = data;
  showAlerts("run-alerts", data.alerts);
  // Green only for a clean load: open drift ("degraded") or a storage error is a warning.
  const open = (result.open_drift || []).length;
  show("run-status", t("run.loaded", { status: result.status, dataset: result.dataset })
    + (open ? ` ${t("run.openDrift", { n: open })}` : ""), result.status === "success" ? "good" : "warn");
  const rows = result.sources.map((s, i) => {
    const counts = Object.entries(s.counts).map(([k, v]) => `${k} ${v}`).join(", ");
    const drift = result.drift && result.drift[i] ? `${result.drift[i].verdict} (${result.drift[i].risk})` : t("run.notMeasured");
    return [i + 1, s.source_url, s.status, counts, origin(s), drift, issues(s.issues)];
  });
  const out = $("run-result");
  out.replaceChildren(table(headers(["n", "source", "status", "changes", "recipe", "drift", "issues"]), rows));
  if (result.stale_sources.length) out.append(el("p", t("run.stale", { n: result.stale_sources.length }), "warn"));
  await loadRecords();
  request("loaded", {});
}

function freshness(s) {
  if (!s.status) return t("health.noDataset");
  if (s.status.stale) return chip("warn", t("health.stale"));
  if (s.open_drift) return chip("warn", t("health.drift", { risk: s.open_drift.risk }));
  return chip("ok", t("health.fresh"));
}

async function health() {
  const data = await api("/api/health", { file: requireFile() });
  showAlerts("health-alerts", data.alerts);
  show("failure-command", data.on_failure_command);
  $("failure-hint").hidden = false;
  const rows = data.sources.map((s, i) => {
    const versions = s.versions.map((v) => `v${v.version} ${v.state}`).join(", ") || "-";
    const fix = data.has_memory ? actionButton(t("source.action.fix"), () => request("fix", { source: i + 1 }), "small") : "";
    const drift = s.open_drift ? t("health.driftReview", { risk: s.open_drift.risk }) : t("health.none");
    return [i + 1, s.source_url, origin(s), freshness(s), s.status?.last_success || t("time.never"),
      s.status?.reason || "", drift, versions, fix];
  });
  $("health-result").replaceChildren(table(headers(["n", "source", "serving", "status", "lastSuccess",
    "whyStale", "drift", "versions", "actions"]), rows));
  show("health-status", data.has_memory ? "" : t("fix.needsMemory"), "muted");
}

export function initOperate() {
  bind("extract", "run-status", extract);
  bind("load", "run-status", load);
  bind("health", "health-status", health);
}
