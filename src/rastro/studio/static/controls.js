// N4/N5/N12: next-page settings, the job's auto-promote switch, the session stealth grant,
// readable alerts and the observed operation line. Text is always set as text content.
import { $, api, bind, el, requireFile, show, state } from "./dom.js";

import { fillBounds, initBounds } from "./bounds.js";

let recipes = [];

export function fillControls(data) {
  fillBounds(data.bounds);
  $("auto-promote").checked = data.auto_promote === true;
  recipes = data.recipes || [];
  $("pagination-source").replaceChildren(...recipes.map((row, index) =>
    new Option(row.source === null ? "Job recipe" : `Source ${row.source + 1} recipe`, String(index))));
  selectRecipe();
}

function selectRecipe() {
  const row = recipes[Number($("pagination-source").value)];
  $("pagination-next").value = row?.pagination?.next || "";
  $("pagination-cap").value = row?.pagination?.max_pages || 5;
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
        : data.state === "idle" ? "" : `Last ${data.operation} of ${data.file}: ${data.outcome}.`;
      show("operation-progress", text, data.state === "failed" ? "bad" : "muted");
    } catch (error) {
      show("operation-progress", `Progress unavailable: ${error.message}`, "warn");
    }
    if (active) timer = setTimeout(poll, 700);
  }
  show("operation-progress", "Starting…", "muted");
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
  if (!selected) throw new Error("open a job with a declared recipe first");
  const next = $("pagination-next").value.trim();
  if (!next) throw new Error("type the next-link selector, for example a.next::attr(href)");
  const max_pages = Number($("pagination-cap").value);
  const data = await api("/api/pagination", {
    file: requireFile(), sha256: state.sha, source: selected.source, next, max_pages,
  });
  state.sha = data.sha256;
  selected.pagination = data.pagination;
  show("controls-status", `Saved: follow "${next}" up to ${max_pages} page(s) on the next run.`, "good");
}

async function toggle(input, send) {
  input.disabled = true;
  try {
    await send(input.checked);
  } catch (error) {
    input.checked = !input.checked;
    show("controls-status", `Not saved: ${error.message}`, "bad");
  } finally {
    input.disabled = input.dataset.locked === "true";
  }
}

async function saveAutoPromote(enabled) {
  const data = await api("/api/auto-promote", { file: requireFile(), sha256: state.sha, enabled });
  state.sha = data.sha256;
  show("controls-status", enabled
    ? "Auto-promote is on for this job. A repair still has to pass every gate and the semantic judge (a configured model)."
    : "Auto-promote is off for this job. Promote repairs yourself, with a reason.", "good");
}

function showStealth(session) {
  const input = $("allow-stealth");
  input.checked = session.allow_stealth;
  input.dataset.locked = String(session.locked_by_operator);
  input.disabled = session.locked_by_operator;
}

async function saveStealth(enabled) {
  const session = await api("/api/session", { allow_stealth: enabled });
  showStealth(session);
  show("controls-status", session.allow_stealth
    ? "This Studio session may run jobs that ask for stealth. No job file was changed."
    : "This Studio session no longer allows stealth.", "muted");
}

export async function initControls() {
  initBounds();
  showStealth(await api("/api/session"));
  if ($("allow-stealth").disabled) {
    show("controls-status", "Stealth was allowed when Studio started (--allow-stealth or RASTRO_ALLOW_STEALTH).", "muted");
  }
  $("pagination-source").addEventListener("change", selectRecipe);
  bind("save-pagination", "controls-status", savePagination);
  $("auto-promote").addEventListener("change", () => toggle($("auto-promote"), saveAutoPromote));
  $("allow-stealth").addEventListener("change", () => toggle($("allow-stealth"), saveStealth));
}
