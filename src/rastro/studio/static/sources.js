// Sources: one row per source with its status in words, last fetch, counts, warnings and one
// next action (N5). Reads POST /api/sources; a missing route or an error is said, never hidden.
import { $, api, el, request, requireFile, show, switchView } from "./dom.js";
import { issueText, onLangChange, t, when } from "./i18n.js";
import { actionButton, chip, emptyState } from "./ui.js";

const TONE = { ok: "ok", warning: "warn", capped: "warn", stale: "warn", failed: "fail", never: "none" };
// Failure codes grouped into the plain words the brief asks for; others say "Failed".
const ISSUE_GROUP = {
  robots_denied: "robots", invalid_robots: "unreadable", unsupported_robots_rule: "unreadable",
  invalid_json: "unreadable", empty_document: "unreadable", invalid_encoding: "unreadable",
  unsupported_content_type: "unreadable", challenge_unsolved: "unreadable",
  missing_fields: "structure", validation_error: "structure", record_path_not_found: "structure",
  access_restricted: "denied", host_access_restricted: "denied", rate_limited: "denied",
};
export const sources = { list: [], error: "", ready: false };

export function sourceStatus(source) {
  const tone = TONE[source.status] || "none";
  if (source.status === "failed") {
    const group = ISSUE_GROUP[source.issue_code] || "other";
    return { tone, word: t(`source.issue.${group}`) };
  }
  const word = t(`source.status.${TONE[source.status] ? source.status : "unknown"}`);
  return { tone, word };
}

export function counts(source) {
  if (source.status === "capped" && source.cap !== null && source.cap !== undefined) {
    return t("source.cappedDetail", { read: source.read, cap: source.cap });
  }
  if (source.status === "never") return t("source.neverDetail");
  return t("source.counts", { read: source.read ?? 0, rejected: source.rejected ?? 0 });
}

function place(url) {
  try {
    const parsed = new URL(url);
    return { host: parsed.host, path: parsed.pathname };
  } catch {
    return { host: String(url), path: "" };
  }
}

function details(source) {
  const box = el("div", undefined, "source-details");
  box.hidden = true;
  box.append(el("code", source.url));
  const codes = [source.issue_code, ...(source.warnings || [])].filter(Boolean);
  for (const code of codes) {
    const line = el("p");
    line.append(el("code", code), " ", issueText(code));
    box.append(line);
  }
  if (!codes.length) box.append(el("p", t("source.noIssues"), "muted"));
  return box;
}

function actions(source, index, more) {
  const row = el("div", undefined, "row");
  const toggle = actionButton(t("source.action.details"), () => {
    more.hidden = !more.hidden;
    toggle.setAttribute("aria-expanded", String(!more.hidden));
  }, "small");
  toggle.setAttribute("aria-expanded", "false");
  if (source.status === "failed" || source.status === "stale") {
    row.append(actionButton(t("source.action.retry"), () => { switchView("results"); $("load").click(); }, "small"));
    row.append(actionButton(t("source.action.fix"), () => request("fix", { source: index }), "small"));
  } else if (source.status === "capped") {
    row.append(actionButton(t("source.action.raiseCap"), () => request("settings", { focus: "pagination-cap" }), "small"));
  } else if (source.status === "never") {
    row.append(actionButton(t("source.action.load"), () => { switchView("results"); $("load").click(); }, "small"));
  }
  row.append(toggle);
  return row;
}

function sourceRow(source, index) {
  const { host, path } = place(source.url);
  const item = el("li", undefined, `source-row tone-${TONE[source.status] || "none"}`);
  const name = el("div", undefined, "source-name");
  name.append(el("strong", host), el("span", path, "muted block"));
  const status = sourceStatus(source);
  const state = el("div");
  state.append(chip(status.tone, status.word));
  for (const code of source.warnings || []) state.append(el("p", issueText(code), "warn small-text"));
  const fetched = el("div", undefined, "source-meta");
  fetched.append(el("span", t("source.lastFetch", { at: when(source.last_fetch) })), el("span", counts(source), "muted block"));
  const more = details(source);
  item.append(name, state, fetched, actions(source, index, more), more);
  return item;
}

function summary(list) {
  const ok = list.filter((s) => s.status === "ok").length;
  const attention = list.filter((s) => ["failed", "stale", "capped", "warning"].includes(s.status)).length;
  return el("p", t("source.summary", { ok, total: list.length, attention }), "summary");
}

export function renderSources() {
  const out = $("sources-list");
  if (!sources.ready) return;
  if (sources.error) {
    out.replaceChildren(emptyState(t("source.errorTitle"), sources.error));
    return;
  }
  if (!sources.list.length) {
    out.replaceChildren(emptyState(t("source.emptyTitle"), t("source.emptyHelp")));
    return;
  }
  const list = el("ul", undefined, "source-list");
  list.append(...sources.list.map((s, i) => sourceRow(s, i + 1)));
  out.replaceChildren(summary(sources.list), list);
}

export async function loadSources() {
  show("sources-status", t("source.loading"), "muted");
  try {
    const data = await api("/api/sources", { file: requireFile() });
    Object.assign(sources, { list: data.sources || [], error: "", ready: true });
  } catch (error) {
    const missing = error.code === "not_found";
    Object.assign(sources, { list: [], ready: true,
      error: missing ? t("source.unavailable") : t("source.failedToLoad", { why: error.message }) });
  }
  show("sources-status", "");
  renderSources();
}

export function resetSources() {
  Object.assign(sources, { list: [], error: "", ready: false });
  $("sources-list").replaceChildren();
}

export function initSources() {
  $("sources-refresh").addEventListener("click", () => loadSources());
  onLangChange(renderSources);
}
