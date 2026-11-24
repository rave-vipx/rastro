// Named components shared by the views: status shapes, chips, empty states and the
// placeholders for links and images that come from data. Text is always plain text content.
import { el } from "./dom.js";
import { t } from "./i18n.js";

// A status is a shape plus a word, never a color alone: circle ok, triangle warning,
// octagon failure, minus-circle "nothing there".
const SHAPES = { ok: "circle", warn: "triangle", fail: "octagon", none: "minus", info: "dot" };

export function shape(tone) {
  const node = el("span", undefined, `ico ico-${SHAPES[tone] || "dot"}`);
  node.setAttribute("aria-hidden", "true");
  return node;
}

export function chip(tone, text) {
  const node = el("span", undefined, `chip chip-${tone}`);
  node.append(shape(tone), document.createTextNode(text));
  return node;
}

// An empty value always says why it is empty.
export function emptyCell(reason) {
  if (reason === "source_failed") {
    const node = el("span", undefined, "empty-cell empty-failed");
    node.append(shape("fail"), document.createTextNode(t("cell.sourceFailed")));
    return node;
  }
  const key = reason === "not_published" ? "cell.notPublished" : "cell.noReason";
  const node = el("span", undefined, "empty-cell");
  node.append(shape("none"), document.createTextNode(t(key)));
  return node;
}

export function trustMarker() {
  const node = el("span", undefined, "trust");
  node.append(shape("warn"), document.createTextNode(t("trust.short")));
  node.title = t("trust.explain");
  return node;
}

// Links and images from data never load on their own. The URL is shown as text; opening it is
// an explicit click, in a new tab, without a referrer. Only http(s) addresses can be opened.
export function openable(value) {
  try {
    const url = new URL(String(value));
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

const IMAGE = /\.(avif|gif|jpe?g|png|svg|webp)(\?|#|$)/i;

export function blockedUrl(value) {
  const box = el("span", undefined, "blocked");
  const image = IMAGE.test(String(value));
  box.append(el("span", t(image ? "blocked.image" : "blocked.link"), "blocked-label"));
  box.append(el("code", value));
  const href = openable(value);
  if (href) {
    const open = el("button", t(image ? "blocked.openImage" : "blocked.open"), "small");
    open.type = "button";
    open.addEventListener("click", (event) => {
      event.stopPropagation();
      window.open(href, "_blank", "noopener,noreferrer");
    });
    box.append(open);
  }
  return box;
}

export function emptyState(title, text, ...actions) {
  const box = el("div", undefined, "empty-state");
  box.append(el("h3", title));
  if (text) box.append(el("p", text, "muted"));
  if (actions.length) {
    const row = el("div", undefined, "row");
    row.append(...actions);
    box.append(row);
  }
  return box;
}

// Loading: skeleton lines for the shape of what is coming, plus a sentence a screen reader
// hears. With reduced motion the skeleton stays still and the sentence still reads.
export function loadingState(text, lines = 3) {
  const box = el("div", undefined, "loading-state");
  box.setAttribute("aria-busy", "true");
  const sketch = el("div", undefined, "skel-lines");
  sketch.setAttribute("aria-hidden", "true");
  for (let i = 0; i < lines; i += 1) sketch.append(el("div", undefined, "skel"));
  const status = el("p", text || t("state.loading"), "muted");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  box.append(sketch, status);
  return box;
}

// Error: what happened, what to do, and one button. Announced at once (role="alert").
export function errorState(what, todo, retry) {
  const box = el("div", undefined, "error-state");
  box.setAttribute("role", "alert");
  const head = el("p", undefined, "error-what");
  head.append(shape("fail"), document.createTextNode(what || t("state.errorTitle")));
  box.append(head);
  if (todo) box.append(el("p", todo, "muted"));
  if (retry) box.append(actionButton(t("state.retry"), retry, "primary"));
  return box;
}

export function actionButton(label, run, className) {
  const node = el("button", label, className);
  node.type = "button";
  node.addEventListener("click", async (event) => {
    event.stopPropagation();
    node.disabled = true;
    try {
      await run();
    } finally {
      node.disabled = false;
    }
  });
  return node;
}
