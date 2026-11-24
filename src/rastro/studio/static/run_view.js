// The running read on the Sites view: what POST /api/run/status says, rebuilt on every poll.

import { api, el, requireFile } from "./dom.js";
import { t } from "./i18n.js";
import { actionButton, chip, errorState, loadingState } from "./ui.js";

const POLL_MS = 1000;
export const live = { snap: null, timer: null, panel: null };

export function clock(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(whole / 60);
  return minutes ? `${minutes}m ${String(whole % 60).padStart(2, "0")}s` : `${whole}s`;
}

// DOM-free mapping from a status snapshot to what the panel shows.
export function runView(snap) {
  if (!snap || snap.state === "idle") return { running: false, idle: true, lines: [], sample: [] };
  const running = snap.state === "running";
  const lines = [];
  const progress = { pages: snap.pages, records: snap.records };
  const head = { running: "run.live.progress", done: "run.live.done", stopped: "run.live.stopped" }[snap.state];
  if (head) lines.push(t(head, progress));
  if (running || snap.state === "done") lines.push(t("run.live.sites", { done: snap.sources_done, total: snap.sources_total }));
  const used = clock(snap.seconds_used);
  const budget = snap.budget_seconds;
  lines.push(budget ? t("run.live.timeOf", { used, budget: clock(budget) }) : t("run.live.time", { used }));
  if (snap.failed_sources) lines.push(t("run.live.failedSites", { n: snap.failed_sources }));
  return {
    running, idle: false, tone: { failed: "fail", stopped: "warn" }[snap.state] || (running ? "none" : "ok"),
    fraction: running && budget ? Math.min(1, snap.seconds_used / budget) : null,
    lines, sample: snap.sample || [], error: snap.state === "failed" ? t("run.live.failed", { why: snap.error?.message || snap.error?.error || "" }) : "",
    resume: snap.will_continue ? t("run.live.continue") : "",
  };
}

function sampleList(rows) {
  const box = el("div");
  box.append(el("h4", t("run.live.sample")));
  const list = el("ul", undefined, "live-sample");
  for (const row of rows) list.append(el("li", Object.values(row).join(" · ")));
  box.append(rows.length ? list : el("p", t("run.live.noResults"), "muted"));
  return box;
}

export function renderRun() {
  const panel = live.panel;
  if (!panel) return;
  const view = runView(live.snap);
  if (view.idle) {
    panel.replaceChildren(el("p", t("run.live.cacheHint"), "muted small-text"));
  } else {
    const body = view.error ? errorState(view.error, "", () => startRun()) : el("div");
    if (!view.error) {
      body.append(...(view.running && !live.snap.pages ? [loadingState(t("run.live.title"), 1)] : []));
      body.append(chip(view.tone, t("run.live.title")), ...view.lines.map((line) => el("p", line)));
      if (view.fraction !== null) {
        const bar = el("progress");
        bar.max = 1;
        bar.value = view.fraction;
        body.append(bar);
      }
      if (view.resume) body.append(el("p", view.resume, "warn"));
      if (view.sample.length || view.running) body.append(sampleList(view.sample));
    }
    panel.replaceChildren(body);
  }
  const row = el("div", undefined, "row");
  row.append(view.running
    ? actionButton(t("run.live.stop"), () => stopRun(), "small")
    : actionButton(t("run.live.start"), () => startRun(), "primary small"));
  panel.append(row);
}

async function poll() {
  try {
    live.snap = await api("/api/run/status", {});
  } catch (error) {
    live.snap = { state: "failed", error: { message: error.message }, sample: [], seconds_used: 0 };
  }
  renderRun();
  if (live.snap.state === "running") live.timer = setTimeout(poll, POLL_MS);
}

async function startRun() {
  clearTimeout(live.timer);
  try {
    live.snap = await api("/api/run/start", { file: requireFile() });
  } catch (error) {
    live.snap = { state: "failed", error: { message: error.message }, sample: [], seconds_used: 0 };
  }
  renderRun();
  if (live.snap.state === "running") live.timer = setTimeout(poll, POLL_MS);
}

async function stopRun() {
  clearTimeout(live.timer);
  try {
    live.snap = await api("/api/run/stop", {});
  } catch (error) {
    live.snap = { state: "failed", error: { message: error.message }, sample: [], seconds_used: 0 };
  }
  renderRun();
}

