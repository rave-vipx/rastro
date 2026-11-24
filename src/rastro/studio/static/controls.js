// Next-page settings, the job's auto-promote switch, the session stealth grant,
// readable alerts and the observed operation line. Text is always set as text content.
import { $, api, bind, el, requireFile, show, state } from "./dom.js";
import { t } from "./i18n.js";

import { fillBounds, initBounds } from "./bounds.js";
import { initConnections, refreshConnections } from "./connections.js";

let recipes = [];
let paginationDefault;

export function fillControls(data) {
  refreshConnections();
  fillBounds(data.bounds);
  paginationDefault = data.bounds.limits.pagination.max_pages.default;
  $("auto-promote").checked = data.auto_promote === true;
  recipes = data.recipes || [];
  $("pagination-source").replaceChildren(...recipes.map((row, index) =>
    new Option(row.source === null ? t("settings.jobRecipe") : t("settings.sourceRecipe", { n: row.source + 1 }), String(index))));
  selectRecipe();
}

function selectRecipe() {
  const row = recipes[Number($("pagination-source").value)];
  $("pagination-next").value = row?.pagination?.next || "";
  $("pagination-cap").value = row?.pagination?.max_pages ?? paginationDefault ?? "";
}

export function showAlerts(id, messages) {
  $(id).replaceChildren(...(messages || []).map((message) => el("p", message, "warn")));
}

// The server reports only what it observed: the operation, the job file and the outcome.
// It has no page or scroll ordinal to give yet, so none is shown.
export async function withProgress(action) {
  let active = true;
  let timer;
  async function poll() {
    try {
      const data = await api("/api/progress");
      const text = data.state === "running" ? data.detail
        : data.state === "idle" ? "" : t("progress.last", { operation: data.operation, file: data.file, outcome: data.outcome });
      show("operation-progress", text, data.state === "failed" ? "bad" : "muted");
    } catch (error) {
      show("operation-progress", t("progress.unavailable", { why: error.message }), "warn");
    }
    if (active) timer = setTimeout(poll, 700);
  }
  show("operation-progress", t("progress.starting"), "muted");
  timer = setTimeout(poll, 250);
  try {
    return await action();
  } finally {
    active = false;
    clearTimeout(timer);
    await poll();
  }
}

async function savePagination() {
  const selected = recipes[Number($("pagination-source").value)];
  if (!selected) throw new Error(t("settings.needRecipe"));
  const next = $("pagination-next").value.trim();
  if (!next) throw new Error(t("settings.needNext"));
  const max_pages = Number($("pagination-cap").value);
  const data = await api("/api/pagination", {
    file: requireFile(), sha256: state.sha, source: selected.source, next, max_pages,
  });
  state.sha = data.sha256;
  selected.pagination = data.pagination;
  show("controls-status", t("settings.savedNext", { next, n: max_pages }), "good");
}

async function toggle(input, send, statusId) {
  input.disabled = true;
  try {
    await send(input.checked);
  } catch (error) {
    input.checked = !input.checked;
    show(statusId, t("settings.notSaved", { why: error.message }), "bad");
  } finally {
    input.disabled = input.dataset.locked === "true";
  }
}

async function saveAutoPromote(enabled) {
  const data = await api("/api/auto-promote", { file: requireFile(), sha256: state.sha, enabled });
  state.sha = data.sha256;
  show("auto-promote-status", t(enabled ? "switch.autoOn" : "switch.autoOff"), "good");
}

function showStealth(session) {
  const input = $("allow-stealth");
  if (!input) return;
  input.checked = session.allow_stealth;
  input.dataset.locked = String(session.locked_by_operator);
  input.disabled = session.locked_by_operator;
}

async function saveStealth(enabled) {
  const session = await api("/api/session", { allow_stealth: enabled });
  showStealth(session);
  show("controls-status", t(session.allow_stealth ? "switch.stealthOn" : "switch.stealthOff"), "muted");
}

export async function initControls() {
  initBounds();
  initConnections();
  showStealth(await api("/api/session"));
  if ($("allow-stealth")?.disabled) show("controls-status", t("switch.stealthLocked"), "muted");
  $("pagination-source").addEventListener("change", selectRecipe);
  bind("save-pagination", "controls-status", savePagination);
  $("auto-promote").addEventListener("change", () => toggle($("auto-promote"), saveAutoPromote, "auto-promote-status"));
  $("allow-stealth")?.addEventListener("change", () => toggle($("allow-stealth"), saveStealth, "controls-status"));
}
