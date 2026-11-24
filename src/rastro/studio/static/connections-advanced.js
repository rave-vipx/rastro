// Advanced Settings cards: other routes (egress), site rules and per-origin switches.
import { api, el, state } from "./dom.js";
import { t } from "./i18n.js";
import { grantRows, proxyRows } from "./connections-data.js";
import { attempt, card, status } from "./connections-ui.js";
import { actionButton } from "./ui.js";

const cache = {};

export function routesCard() {
  const box = card("conn-routes", "conn.proxy.title", undefined, true);
  const say = status(box);
  const list = el("div");
  const robots = el("input");
  robots.type = "checkbox";
  robots.id = "conn-robots";
  robots.checked = true;
  const robotsLabel = el("label", t("conn.robots.obey"), "check");
  robotsLabel.htmlFor = "conn-robots";
  robotsLabel.dataset.i18n = "conn.robots.obey";
  const warn = el("p", t("conn.robots.warn"), "warn");
  warn.dataset.i18n = "conn.robots.warn";
  robots.addEventListener("change", () => attempt(say, async () => {
    if (!state.file) { robots.checked = !robots.checked; throw new Error(t("conn.robots.needFile")); }
    const data = await api("/api/robots", { file: state.file, sha256: state.sha, mode: robots.checked ? "obey" : "ignore" });
    state.sha = data.sha256;
    say(t("conn.robots.saved"), "good");
  }));
  async function refresh() {
    if (!state.file) return;
    cache.proxies = await api(`/api/proxies?file=${encodeURIComponent(state.file)}`);
    draw();
  }
  function draw() {
    const rows = proxyRows(cache.proxies || {});
    list.replaceChildren(...(rows.length ? rows.map((row) => {
      const line = el("div", undefined, "row");
      const test = actionButton(t("conn.proxy.test"), () => attempt(say, async () => {
        const data = await api("/api/proxies/test", { file: state.file, name: row.name });
        say(t(data.reachable ? "conn.proxy.ok" : "conn.proxy.down", { name: row.name }), data.reachable ? "good" : "bad");
      }), "small");
      test.disabled = !row.canTest;
      line.append(el("strong", row.name), el("span", row.address), el("small", row.env.join(", "), "muted"), test);
      return line;
    }) : [el("p", t("conn.proxy.none"), "muted")]));
    if ((cache.proxies || {}).allowed === false) say(t("conn.proxy.locked"));
  }
  const rulesTitle = el("h3", t("conn.robots.title"));
  rulesTitle.dataset.i18n = "conn.robots.title";
  box.append(list, rulesTitle, robots, robotsLabel, warn);
  box.refresh = () => attempt(say, refresh);
  box.redraw = draw;
  return box;
}

export function grantsCard() {
  const box = card("conn-grants", "conn.grants.title", "conn.grants.help", true);
  const say = status(box);
  const list = el("div");
  box.append(list);
  async function refresh() {
    cache.grants = await api("/api/grants");
    draw();
  }
  function draw() {
    list.replaceChildren(...grantRows(cache.grants || {}).map((row) => {
      const box_ = el("input");
      box_.type = "checkbox";
      box_.id = `grant-${row.id}`;
      box_.checked = row.on;
      box_.disabled = row.locked;
      box_.addEventListener("change", () => attempt(say, async () => {
        cache.grants = await api("/api/grants", { origin: row.origin, capability: row.name, on: box_.checked });
        draw();
      }));
      const label = el("label", `${t(`conn.origin.${row.origin}`)} · ${t(`conn.cap.${row.name}`)}${row.locked ? ` (${t("conn.grants.locked")})` : ""}`, "check");
      label.htmlFor = box_.id;
      const line = el("div", undefined, "row");
      line.append(box_, label);
      return line;
    }));
  }
  box.refresh = () => attempt(say, refresh);
  box.redraw = draw;
  return box;
}

