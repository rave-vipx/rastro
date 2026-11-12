// Run, health and repair, and the dataset browser for the open job.
// All source-derived text is set as plain text content; markup is never parsed.
import { $, api, bind, el, pill, requireFile, show, state, table } from "./dom.js";

const PAGE_SIZE = 25;
let offset = 0;

export function resetOperate() {
  offset = 0;
  for (const id of ["run-result", "health-result", "repair-result", "dataset-result"]) {
    $(id).replaceChildren();
  }
  for (const id of ["run-status", "health-status", "dataset-status"]) show(id, "");
}

function origin(source) {
  if (!source.recipe_origin) return "-";
  const version = source.recipe_version === null ? "new" : `v${source.recipe_version}`;
  return `${source.recipe_origin === "memory" ? "recipe memory" : "job file"} ${version}`;
}

function issues(list) {
  return (list || []).map((i) => i.code + (i.field ? `(${i.field})` : "")).join(", ") || "none";
}

async function extract() {
  const data = await api("/api/extract", { file: requireFile() });
  const j = data.job;
  show("run-status", `Extract ${j.status}: ${j.records_accepted} accepted, ${j.records_rejected} rejected. Nothing was written.`,
    j.status === "success" ? "good" : "warn");
  const rows = data.sources.map((s) => [s.source_index, s.source_url, s.status, s.records, origin(s), issues(s.issues)]);
  $("run-result").replaceChildren(table(["#", "source", "status", "records", "recipe", "issues"], rows));
}

async function load() {
  const { result } = await api("/api/load", { file: requireFile(), drift: $("drift").checked });
  show("run-status", `Load ${result.job.status} into dataset "${result.dataset}".`,
    result.job.status === "success" ? "good" : "warn");
  const rows = result.sources.map((s, i) => {
    const counts = Object.entries(s.counts).map(([k, v]) => `${k} ${v}`).join(", ");
    const drift = result.drift && result.drift[i] ? `${result.drift[i].verdict} (${result.drift[i].risk})` : "not measured";
    return [i + 1, s.source_url, s.status, counts, origin(s), drift, issues(s.issues)];
  });
  const out = $("run-result");
  out.replaceChildren(table(["#", "source", "status", "changes", "recipe", "drift", "issues"], rows));
  if (result.stale_sources.length) out.append(el("p", `${result.stale_sources.length} source(s) are stale.`, "warn"));
}

async function health() {
  const data = await api("/api/health", { file: requireFile() });
  const out = $("health-result");
  const rows = data.sources.map((s, i) => {
    const status = s.status ? (s.status.stale ? pill("stale", false) : pill("fresh", true)) : "no dataset yet";
    const versions = s.versions.map((v) => `v${v.version} ${v.state}`).join(", ") || "-";
    const actions = el("div", undefined, "row");
    if (data.has_memory) {
      actions.append(button("Propose repair", () => repair(i + 1)));
      actions.append(button("Write to job file", () => apply(i + 1)));
    }
    return [i + 1, s.source_url, origin(s), status, s.status?.reason || "", versions, actions];
  });
  out.replaceChildren(table(["#", "source", "serving recipe", "status", "reason", "stored versions", "actions"], rows));
  show("health-status", data.has_memory ? "" : "Add recipe_memory to the job to propose and promote repairs.", "muted");
}

function button(label, action) {
  const node = el("button", label);
  node.type = "button";
  node.addEventListener("click", async () => {
    node.disabled = true;
    try { await action(); } catch (error) { show("health-status", `Error: ${error.message}`, "bad"); }
    finally { node.disabled = false; }
  });
  return node;
}

async function repair(source) {
  show("health-status", `Proposing a repair for source ${source}…`, "muted");
  const { report } = await api("/api/repair", { file: requireFile(), source });
  const item = report.sources[0];
  const out = $("repair-result");
  out.replaceChildren(el("h3", `Repair for source ${source}: ${item.outcome || "done"}`));
  if (item.issues && item.issues.length) out.append(el("p", `issues: ${issues(item.issues)}`, "warn"));
  if (!item.comparisons.length) out.append(el("p", "No candidate recipe was found.", "warn"));
  for (const comparison of item.comparisons) out.append(candidate(source, comparison, item.promoted_version));
  show("health-status", item.promoted_version ? `Version ${item.promoted_version} now serves.` : "Review the candidates below.", "muted");
}

function candidate(source, comparison, promoted) {
  const box = el("div", undefined, "candidate");
  const ev = comparison.evaluation;
  box.append(el("h3", `Candidate v${comparison.candidate_version} (replaces v${comparison.previous_version})`));
  if (comparison.diff) {
    const [oldRecord, newRecord] = comparison.diff.record_selector;
    const rows = [["(record)", oldRecord, newRecord]];
    for (const f of comparison.diff.fields) rows.push([f.field, f.old, f.new]);
    const grid = table(["field", "old selector", "new selector"], rows);
    grid.querySelectorAll("tbody tr").forEach((tr, i) => {
      if (i > 0 && comparison.diff.fields[i - 1].changed) tr.className = "changed";
    });
    box.append(grid);
  }
  if (ev) {
    box.append(el("p", `Decision: ${ev.decision}. ${ev.current_records} records now; ${ev.historical_passed}/${ev.historical_fixtures} past samples pass.`,
      ev.decision === "promote" ? "good" : ev.decision === "reject" ? "bad" : "warn"));
    const gates = ev.regressions.map((r) => [r.name, pill(r.passed ? "pass" : "fail", r.passed)]);
    if (gates.length) box.append(table(["gate", "result"], gates));
    if (ev.reasons.length) box.append(el("p", `Reasons: ${ev.reasons.join("; ")}`));
  }
  if (promoted === comparison.candidate_version) box.append(el("p", "This version is now serving.", "good"));
  else if (!ev || ev.decision !== "reject") box.append(promoteForm(source, comparison.candidate_version));
  return box;
}

function promoteForm(source, version) {
  const form = el("div", undefined, "row");
  const id = `reason-${source}-${version}`;
  const label = el("label", "Reason");
  label.htmlFor = id;
  const reason = el("input");
  Object.assign(reason, { id, type: "text", maxLength: 500, size: 32 });
  const overrideLabel = el("label", undefined, "check");
  const override = el("input");
  override.type = "checkbox";
  overrideLabel.append(override, " Override failed gates");
  const warning = el("p", "", "warn");
  warning.setAttribute("role", "alert");
  override.addEventListener("change", () => {
    warning.textContent = override.checked
      ? "Overriding activates a candidate that did not pass every gate. The reason is recorded."
      : "";
  });
  const go = button("Promote", async () => {
    if (!reason.value.trim()) throw new Error("give a reason before promoting");
    const { report } = await api("/api/promote", {
      file: requireFile(), source, version, reason: reason.value, override_gates: override.checked,
    });
    const done = report.sources[0].promoted_version;
    show("health-status", done ? `Version ${done} now serves. Use "Write to job file" to save it there.` : "Not promoted: the candidate failed its gates.", done ? "good" : "warn");
  });
  form.append(label, reason, overrideLabel, go, warning);
  return form;
}

async function apply(source) {
  const data = await api("/api/apply", { file: requireFile(), source, sha256: state.sha });
  state.sha = data.sha256;
  show("health-status", data.changed ? `Recipe v${data.recipe_version} written to the job file.` : "The job file already holds the serving recipe.", "good");
}

async function records(delta) {
  offset = Math.max(0, offset + delta);
  const data = await api("/api/records", { file: requireFile(), offset, limit: PAGE_SIZE });
  if (data.total && offset >= data.total) offset = Math.max(0, data.total - PAGE_SIZE);
  const last = Math.min(offset + data.records.length, data.total);
  show("dataset-status", `Records ${data.total ? offset + 1 : 0}-${last} of ${data.total} in "${data.dataset}". ${data.stale_sources} stale source(s). Values come from the source pages.`, "muted");
  const rows = data.records.map((r) => [
    ...data.fields.map((name) => r.fields[name]), r.last_observed_at, r.stale ? pill("stale", false) : "",
  ]);
  $("dataset-result").replaceChildren(table([...data.fields, "last seen", "stale"], rows));
}

async function download(format) {
  const data = await api("/api/export", { file: requireFile(), format });
  const url = URL.createObjectURL(new Blob([data.content], { type: data.media_type }));
  const link = el("a");
  Object.assign(link, { href: url, download: data.filename });
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  show("dataset-status", `Downloaded ${data.filename}.`, "good");
}

export function initOperate() {
  bind("extract", "run-status", extract);
  bind("load", "run-status", load);
  bind("health", "health-status", health);
  bind("dataset", "dataset-status", () => records(-offset));
  bind("prev-page", "dataset-status", () => records(-PAGE_SIZE));
  bind("next-page", "dataset-status", () => records(PAGE_SIZE));
  bind("export-json", "dataset-status", () => download("json"));
  bind("export-html", "dataset-status", () => download("html"));
}
