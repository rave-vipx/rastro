// Settings: AI model, notification channels, schedule in words, and (Advanced) other routes,
// site rules and per-origin switches. Built from JS and appended to the Settings view; all
// text is plain text content and a reply names variables, never secrets.
import { $, api, el, state } from "./dom.js";
import { lang, onLangChange, t, when } from "./i18n.js";
import { channelState, currentModel, grantRows, modelOptions, proxyRows, scheduleLine } from "./connections-data.js";
import { routesCard, grantsCard } from "./connections-advanced.js";
import { attempt, card, field, status } from "./connections-ui.js";
import { actionButton, chip } from "./ui.js";

const CHANNELS = ["email", "slack", "telegram", "webhook"];
const cache = {};

function modelCard() {
  const box = card("conn-model", "conn.model.title", "conn.model.help");
  const say = status(box);
  const list = el("div");
  const now = el("p");
  const consent = el("input");
  consent.type = "checkbox";
  consent.id = "conn-consent";
  const inputs = Object.fromEntries(["name", "url", "keyName", "price"].map((key) => [key, el("input")]));
  inputs.price.type = "number";
  inputs.price.step = "0.01";
  const draw = (models) => {
    const options = modelOptions(models);
    const cost = (c) => t("conn.model.in", { cost: t(c.key, c.vars) });
    list.replaceChildren(...(options.length ? options.map((o) => {
      const row = el("div", undefined, "row");
      row.append(el("span", `${o.model} · ${cost(o.cost)}`), actionButton(t("conn.model.use"), () => attempt(say, async () => {
        await api("/api/models", { kind: "local", spec: o.spec });
        say(t("conn.model.chosen"), "good");
        await refresh();
      }), "small"));
      return row;
    }) : [el("p", t("conn.model.noneFound"), "muted")]));
    const now_ = currentModel(models);
    now.textContent = t(now_.key, now_.vars);
  };
  async function refresh() {
    cache.models = await api("/api/models");
    draw(cache.models);
  }
  const connect = actionButton(t("conn.model.connect"), () => attempt(say, async () => {
    if (!consent.checked) throw new Error(t("conn.model.needConsent"));
    await api("/api/models", {
      kind: "remote", consent: true, model: inputs.name.value.trim(), base_url: inputs.url.value.trim(),
      api_key_env: inputs.keyName.value.trim(), cost_per_call: inputs.price.value ? Number(inputs.price.value) : undefined,
    });
    say(t("conn.model.chosen"), "good");
    await refresh();
  }));
  const head = el("h3", t("conn.model.local"));
  const provider = el("h3", t("conn.model.provider"));
  const consentLabel = el("label", t("conn.model.consent"), "check");
  consentLabel.htmlFor = "conn-consent";
  box.append(now, head, list, provider,
    ...field("conn.model.name", inputs.name), ...field("conn.model.url", inputs.url),
    ...field("conn.model.keyName", inputs.keyName, true), ...field("conn.model.price", inputs.price),
    consent, consentLabel, connect);
  box.refresh = refresh;
  box.redraw = () => cache.models && draw(cache.models);
  return box;
}

function channelsCard() {
  const box = card("conn-channels", "conn.chan.title", "conn.chan.help");
  const list = el("div");
  box.append(list);
  async function refresh() {
    cache.channels = (await api("/api/channels")).channels;
    draw();
  }
  function draw() {
    list.replaceChildren(...(cache.channels || []).map((row) => {
      const view = channelState(row);
      const line = el("div", undefined, "row");
      const state_ = chip(view.tone, t(view.key, { ...view.vars, at: view.vars.at ? when(view.vars.at) : "" }));
      const names = el("small", t("conn.chan.vars", { names: row.env.join(", ") }), "muted");
      names.dataset.adv = "";
      const test = actionButton(t("conn.chan.test"), async () => {
        state_.textContent = t("conn.chan.sending");
        try {
          const result = await api("/api/channels/test", { channel: row.channel });
          row.last = result;
        } catch (error) {
          row.last = { sent: false, reason: error.message, at: new Date().toISOString() };
        }
        draw();
      }, "small");
      line.append(el("strong", t(`conn.chan.${row.channel}`)), state_, test, names);
      return line;
    }));
  }
  box.refresh = refresh;
  box.redraw = draw;
  return box;
}

function scheduleCard() {
  const box = card("conn-schedule", "conn.sched.title", "conn.sched.help");
  const say = status(box);
  const words = el("input");
  words.id = "conn-words";
  const summary = el("p");
  const runs = el("ul");
  const draw = (row) => {
    const line = scheduleLine(row);
    summary.textContent = t(line.key, line.vars);
    runs.replaceChildren(...line.runs.map((iso) => el("li", when(iso))));
  };
  async function refresh() {
    if (!state.file) return say(t("conn.sched.open"));
    const row = await api(`/api/schedule?file=${encodeURIComponent(state.file)}&language=${lang()}`);
    draw(row);
    words.value = row.words || "";
  }
  const check = actionButton(t("conn.sched.check"), () => attempt(say, async () => {
    draw(await api("/api/schedule/preview", { file: state.file, words: words.value, language: lang() }));
  }), "small");
  const save = actionButton(t("conn.sched.save"), () => attempt(say, async () => {
    const data = await api("/api/schedule/save", { file: state.file, sha256: state.sha, words: words.value });
    state.sha = data.sha256;
    say(t("conn.sched.saved"), "good");
    await refresh();
  }));
  box.append(...field("conn.sched.label", words), check, save, summary, runs);
  box.refresh = () => attempt(say, refresh);
  return box;
}

export async function initConnections() {
  const host = $("view-settings");
  if (!host || host.querySelector("#conn-model")) return;
  const cards = [modelCard(), channelsCard(), scheduleCard(), routesCard(), grantsCard()];
  host.append(...cards);
  const refreshAll = () => Promise.all(cards.map((c) => c.refresh && c.refresh().catch(() => {})));
  onLangChange(() => { for (const c of cards) if (c.redraw) c.redraw(); });
  host.addEventListener("view-shown", refreshAll);
  await refreshAll();
}

export async function refreshConnections() {
  const host = $("view-settings");
  if (host) host.dispatchEvent(new CustomEvent("view-shown"));
}
