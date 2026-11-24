// Alerts: what happened in the runs, newest first, grouped by day; one plain sentence and one
// action each. The sentence is written here from kind + code, so it follows the language.
import { $, api, el, request, requireFile, show, switchView } from "./dom.js";
import { issueText, lang, onLangChange, t, when } from "./i18n.js";
import { sources } from "./sources.js";
import { actionButton, chip, emptyState } from "./ui.js";

const TONE = { failure: "fail", promote_refused: "fail", drift: "warn", cap: "warn", warning: "warn", quarantine: "warn" };
export const alerts = { list: [], error: "", ready: false };

function host(url) {
  if (!url) return "";
  try {
    return new URL(url).host;
  } catch {
    return String(url);
  }
}

// The sentence for one alert: what happened (kind), then the code's own explanation.
export function sentence(alert) {
  const kind = TONE[alert.kind] ? alert.kind : "warning";
  const what = t(`alert.text.${kind}`, { field: alert.field || "", source: host(alert.source) || t("alert.job") });
  const field = alert.field && !what.includes(alert.field) ? ` ${t("alert.field", { field: alert.field })}` : "";
  return alert.code && alert.code !== alert.kind ? `${what}${field} ${issueText(alert.code)}` : `${what}${field}`;
}

function sourceIndex(url) {
  const found = sources.list.findIndex((s) => s.url === url);
  return found >= 0 ? found + 1 : null;
}

export function actionFor(alert) {
  const action = alert.action;
  if (action === "retry") return [t("alert.action.retry"), () => { switchView("results"); $("load").click(); }];
  if (action === "fix") return [t("alert.action.fix"), () => request("fix", { source: sourceIndex(alert.source) })];
  if (action === "raise_cap") return [t("alert.action.raiseCap"), () => request("settings", { focus: "pagination-cap" })];
  return [t("alert.action.details"), () => switchView("sources")];
}

function day(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return t("alert.unknownDay");
  return new Intl.DateTimeFormat(lang() === "es" ? "es-UY" : "en-US", { dateStyle: "full" }).format(date);
}

function item(alert) {
  const box = el("li", undefined, "alert-item");
  const kind = TONE[alert.kind] ? alert.kind : "warning";
  const head = el("div", undefined, "alert-head");
  head.append(chip(TONE[kind], t(`alert.kind.${kind}`)));
  head.append(el("span", [host(alert.source), when(alert.at)].filter(Boolean).join(" · "), "muted"));
  box.append(head, el("p", sentence(alert)));
  const [label, run] = actionFor(alert);
  box.append(actionButton(label, run, "small"));
  return box;
}

export function renderAlerts() {
  const out = $("alerts-list");
  if (!alerts.ready) return;
  if (alerts.error) {
    out.replaceChildren(emptyState(t("alert.errorTitle"), alerts.error));
    return;
  }
  if (!alerts.list.length) {
    out.replaceChildren(emptyState(t("alert.emptyTitle"), t("alerts.empty")));
    return;
  }
  const groups = new Map();
  for (const alert of alerts.list) {
    const key = day(alert.at);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(alert);
  }
  const parts = [];
  for (const [key, list] of groups) {
    parts.push(el("h3", key));
    const ul = el("ul", undefined, "alert-list");
    ul.append(...list.map(item));
    parts.push(ul);
  }
  out.replaceChildren(...parts);
}

export async function loadAlerts() {
  show("alerts-status", t("alert.loading"), "muted");
  try {
    const data = await api("/api/alerts", { file: requireFile() });
    Object.assign(alerts, { list: data.alerts || [], error: "", ready: true });
  } catch (error) {
    const why = error.code === "not_found" ? t("alert.unavailable") : t("alert.failedToLoad", { why: error.message });
    Object.assign(alerts, { list: [], error: why, ready: true });
  }
  show("alerts-status", "");
  renderAlerts();
}

export function resetAlerts() {
  Object.assign(alerts, { list: [], error: "", ready: false });
  $("alerts-list").replaceChildren();
}

export function initAlerts() {
  $("alerts-refresh").addEventListener("click", () => loadAlerts());
  onLangChange(renderAlerts);
}
