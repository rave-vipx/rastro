// Home: the projects Studio recognizes in this folder, with health, last run, records and
// sources with problems. Loading, empty folder and unknown databases are explained.
import { $, api, el, show } from "./dom.js";
import { onLangChange, t, when } from "./i18n.js";
import { lightTone, lightWord } from "./alerts.js";
import { actionButton, chip, emptyState } from "./ui.js";

const HEALTH = { ok: "ok", warnings: "warn", failing: "fail", unknown: "none" };
const home = { projects: [], others: [], truncated: false, fallback: false, error: "", ready: false };
let app = null;

function stem(file) {
  return file.split("/").pop().replace(/\.(toml|json)$/i, "");
}

function card(project) {
  const box = el("li", undefined, "project-card");
  const health = HEALTH[project.health] ? project.health : "unknown";
  box.append(el("h3", project.name || stem(project.file)), el("p", project.file, "muted mono"));
  if (project.light) box.append(chip(lightTone(project.light), lightWord(project.light)));
  else box.append(chip(HEALTH[health], t(`home.health.${health}`)));
  const facts = el("dl", undefined, "facts");
  const fact = (label, value) => {
    const row = el("div");
    row.append(el("dt", label), el("dd", value));
    facts.append(row);
  };
  if (home.fallback) fact(t("home.lastRun"), t("home.notReported"));
  else {
    fact(t("home.lastRun"), project.last_run ? when(project.last_run) : t("time.never"));
    fact(t("home.records"), String(project.records));
    fact(t("home.problems"), t("home.problemCount", { n: project.sources_with_problems, total: project.sources_total }));
  }
  box.append(facts);
  box.append(actionButton(t("home.view"), () => openProject(project.file), "primary"));
  return box;
}

async function openProject(file) {
  try {
    await app.openJob(file);
    $("files").value = file;
    app.switchView("results");
  } catch (error) {
    show("home-status", t("msg.error", { why: error.message }), "bad");
  }
}

function startButtons() {
  return [
    actionButton(t("home.startUrl"), () => app.switchView("wizard"), "primary"),
    actionButton(t("home.connectExport"), () => { $("export-help").open = true; $("export-help-summary").focus(); }),
  ];
}

function others() {
  const box = el("section", undefined, "note");
  box.append(el("h3", t("home.otherTitle")), el("p", t("home.otherHelp")));
  const list = el("ul");
  for (const path of home.others) {
    const item = el("li");
    item.append(el("code", path));
    list.append(item);
  }
  box.append(list);
  return box;
}

export function renderHome() {
  const out = $("home-list");
  if (!home.ready) return;
  if (home.error) {
    out.replaceChildren(emptyState(t("home.errorTitle"), home.error));
    return;
  }
  const parts = [];
  if (home.fallback) parts.push(el("p", t("home.fallback"), "note"));
  if (home.projects.length) {
    const list = el("ul", undefined, "project-grid");
    list.append(...home.projects.map(card));
    parts.push(list);
  } else if (!home.others.length) {
    parts.push(emptyState(t("home.emptyTitle"), t("home.emptyHelp"), ...startButtons()));
  }
  if (home.others.length) parts.push(others());
  if (home.truncated) parts.push(el("p", t("home.truncated"), "muted"));
  out.replaceChildren(...parts);
}

export async function loadHome() {
  $("home-list").replaceChildren(el("p", t("home.loading"), "muted loading"));
  try {
    const data = await api("/api/projects");
    Object.assign(home, { projects: data.projects || [], others: data.other_databases || [],
      truncated: !!data.truncated, fallback: false, error: "", ready: true });
  } catch (error) {
    if (error.code !== "not_found") {
      Object.assign(home, { ready: true, error: t("home.failed", { why: error.message }) });
    } else {
      // Older Studio: list job files only, and say that health is not reported.
      const jobs = await api("/api/jobs").catch(() => ({ files: [] }));
      Object.assign(home, { projects: jobs.files.map((file) => ({ file, name: stem(file), health: "unknown" })),
        others: [], truncated: !!jobs.truncated, fallback: true, error: "", ready: true });
    }
  }
  renderHome();
}

export function initHome(studio) {
  app = studio;
  $("home-start").replaceChildren(...startButtons());
  onLangChange(() => {
    $("home-start").replaceChildren(...startButtons());
    renderHome();
  });
}
