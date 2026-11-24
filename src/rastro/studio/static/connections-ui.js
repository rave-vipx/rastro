// Shared builders for the Settings cards: a titled card, a labelled field, a status line.
import { el } from "./dom.js";
import { t } from "./i18n.js";

export function card(id, titleKey, helpKey, advanced) {
  const box = el("section", undefined, "card");
  box.id = id;
  if (advanced) box.dataset.adv = "";
  const title = el("h2", t(titleKey));
  title.dataset.i18n = titleKey;
  box.append(title);
  if (helpKey) {
    const help = el("p", t(helpKey), "muted");
    help.dataset.i18n = helpKey;
    box.append(help);
  }
  return box;
}

export function field(labelKey, input, advanced) {
  const label = el("label", t(labelKey));
  label.dataset.i18n = labelKey;
  if (advanced) label.dataset.adv = "";
  input.id = input.id || `conn-${labelKey}`;
  label.htmlFor = input.id;
  return [label, input];
}

export function status(box) {
  const line = el("p", "", "muted");
  line.setAttribute("role", "status");
  line.setAttribute("aria-live", "polite");
  box.append(line);
  return (text, tone) => { line.textContent = text; line.className = tone || "muted"; };
}

export async function attempt(say, action) {
  try {
    await action();
  } catch (error) {
    say(t("msg.error", { why: error.message }), "bad");
  }
}

