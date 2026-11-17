// Per-viewer language and theme (N8). English is the default language; the theme follows the
// system unless the viewer picks light or dark. Storage may be blocked: every access is guarded
// and the page works without it.
import { LANGS, setLang } from "./i18n.js";

const THEMES = ["system", "light", "dark"];

function load(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function save(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Private mode or blocked storage: the choice lasts until reload.
  }
}

export function applyTheme(theme) {
  const value = THEMES.includes(theme) ? theme : "system";
  if (value === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = value;
  return value;
}

export function initPrefs() {
  const langPick = document.getElementById("pref-lang");
  const themePick = document.getElementById("pref-theme");
  const storedLang = load("rastro-studio-lang");
  langPick.value = LANGS.includes(storedLang) ? storedLang : "en";
  setLang(langPick.value);
  themePick.value = applyTheme(load("rastro-studio-theme"));
  langPick.addEventListener("change", () => {
    setLang(langPick.value);
    save("rastro-studio-lang", langPick.value);
  });
  themePick.addEventListener("change", () => {
    save("rastro-studio-theme", applyTheme(themePick.value));
  });
}
