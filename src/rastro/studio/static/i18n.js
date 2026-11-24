// Studio copy in English (default) and Rioplatense Spanish (N8). Static text only:
// elements carry data-i18n="key" (textContent), data-i18n-placeholder or data-i18n-label.
// Copy lives in one JSON file per view under i18n/, each with "en", "es" and an "advanced"
// overlay: the technical wording shown only while the Advanced switch is on.
const NAMES = ["core", "wizard", "results", "health", "settings", "hub", "run"];

// fetch() works in every browser that runs Studio; JSON module imports do not.
async function load(name) {
  const response = await fetch(new URL(`./i18n/${name}.json`, import.meta.url));
  if (!response.ok) throw new Error(`copy_${name}_${response.status}`);
  return response.json();
}

const VIEWS = await Promise.all(NAMES.map(load));

function merge(pick) {
  return { en: Object.assign({}, ...VIEWS.map((v) => pick(v).en)),
    es: Object.assign({}, ...VIEWS.map((v) => pick(v).es)) };
}

export const MESSAGES = merge((view) => view);
const ADVANCED = merge((view) => view.advanced);

export const LANGS = ["en", "es"];
let current = "en";
let technical = false;

const listeners = [];

// t("key") or t("key", {n: 3}): "{n}" placeholders are replaced with plain text values.
export function t(key, vars) {
  const overlay = technical ? ADVANCED[current][key] ?? ADVANCED.en[key] : undefined;
  const text = overlay ?? MESSAGES[current][key] ?? MESSAGES.en[key] ?? key;
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name) => (name in vars ? String(vars[name]) : whole));
}

export function has(key) {
  return key in MESSAGES.en;
}

// A plain sentence for an issue code (docs/issues.md); unknown codes are named, not hidden.
export function issueText(code) {
  return has(`code.${code}`) ? t(`code.${code}`) : t("code.unknown", { code });
}

// Views that build their text in JS re-render when the language changes.
export function onLangChange(render) {
  listeners.push(render);
}

// Dates and times in the viewer's language; an unparsable value is shown as given.
export function when(iso) {
  if (!iso) return t("time.never");
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return new Intl.DateTimeFormat(current === "es" ? "es-UY" : "en-US",
    { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function lang() {
  return current;
}

export function applyI18n(root = document) {
  for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of root.querySelectorAll("[data-i18n-placeholder]")) {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  }
  for (const el of root.querySelectorAll("[data-i18n-label]")) {
    el.setAttribute("aria-label", t(el.dataset.i18nLabel));
  }
}

export function setLang(next) {
  current = LANGS.includes(next) ? next : "en";
  document.documentElement.lang = current;
  applyI18n();
  for (const render of listeners) render();
}

export function advanced() {
  return technical;
}

// The Advanced switch: technical wording and [data-adv] sections appear only while it is on.
export function setAdvanced(on) {
  technical = Boolean(on);
  if (technical) document.documentElement.dataset.advanced = "on";
  else delete document.documentElement.dataset.advanced;
  applyI18n();
  for (const render of listeners) render();
}
