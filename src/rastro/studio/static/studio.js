// Studio entry: session token, views, the project list and opening a project.
// All source-derived text is set as plain text content; markup is never parsed.
import { initPrefs } from "./prefs.js";
import { $, api, bind, show, state, switchView } from "./dom.js";
import { t } from "./i18n.js";
import { initAI } from "./ai.js";
import { fillControls, initControls } from "./controls.js";
import { fillSelectors, initCorrect, resetCorrect } from "./correct.js";
import { fillFixSources, initFix, resetFix } from "./fix.js";
import { initOperate, resetOperate } from "./operate.js";
import { initProvenance } from "./provenance.js";
import { initRegistry } from "./registry.js";
import { initResults, loadRecords, resetResults } from "./results.js";
import { initSources, loadSources, resetSources } from "./sources.js";
import { initAlerts, loadAlerts, resetAlerts } from "./alerts.js";
import { initWizard } from "./wizard.js";

// The token arrives in the URL fragment (never sent to the server); keep it in memory only.
state.token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
history.replaceState(null, "", location.pathname + location.search);

export async function openJob(name) {
  const data = await api("/api/validate", { file: name });
  Object.assign(state, { file: name, sha: data.sha256, hasMemory: data.has_memory, sourceCount: data.sources });
  fillSelectors(data.selectors);
  fillControls(data);
  for (const reset of [resetOperate, resetResults, resetSources, resetAlerts, resetCorrect, resetFix]) reset();
  const notes = [t("open.sources", { n: data.sources })];
  if (!data.has_dataset) notes.push(t("open.noDataset"));
  if (data.has_memory) notes.push(t("open.memory"));
  show("validation", t("open.valid", { notes: notes.join("; ") }), "good");
  await Promise.all([data.has_dataset ? loadRecords() : null, loadSources(), loadAlerts()]);
  fillFixSources(data.sources);
}

async function connect() {
  const data = await api(`/api/connect?file=${encodeURIComponent(state.file || $("files").value)}`);
  show("commands", `${data.cli.join("\n")}\n\n${data.sdk}\n\n# ${data.note}`);
}

export async function refreshJobs(select) {
  const data = await api("/api/jobs");
  if (data.truncated) show("listing", t("open.truncated"));
  show("root", t("open.folder", { name: data.root }), "muted");
  const list = $("files");
  list.replaceChildren(...data.files.map((name) => new Option(name, name)));
  if (select) list.value = select;
  return data;
}

function wireRequests() {
  document.addEventListener("studio:loaded", () => Promise.all([loadSources(), loadAlerts()]));
  document.addEventListener("studio:settings", (event) => {
    switchView("settings");
    const target = event.detail && event.detail.focus ? $(event.detail.focus) : null;
    if (target) target.focus();
  });
}

async function start() {
  initPrefs();
  if (!state.token) {
    show("listing", t("open.noToken"), "bad");
    return;
  }
  await refreshJobs();
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  }
  bind("validate", "validation", () => openJob($("files").value));
  bind("connect", "commands", connect);
  for (const init of [initOperate, initResults, initProvenance, initSources, initAlerts, initCorrect, initFix]) init();
  wireRequests();
  initWizard({ openJob, refreshJobs, switchView });
  initRegistry();
  await initAI();
  await initControls();
}

start().catch((error) => show("listing", t("msg.error", { why: error.message }), "bad"));
