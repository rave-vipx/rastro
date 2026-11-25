// The review page: a snapshot of the page with each field's matches outlined, cell <-> box
// sync, and "click what you want" to fix a field (buttons over the snapshot plus a keyboard
// list). The PNG is decoded with createImageBitmap and drawn on a canvas, so no image URL is
// ever loaded. Page text is untrusted: it is only ever set as plain text content.
import { api, el } from "./dom.js";
import { t } from "./i18n.js";
import { confirmPick } from "./correct.js";

// Five outlines that differ without color (solid, dashed, double, dotted, thick): .hl1-.hl5.
const MAX_MARKED = 5;

// Data mapping, DOM-free: field -> outline number (1-5), label and boxes; sixth field on unmarked.
export function highlightModel(highlights) {
  return (highlights || []).map((item, i) => ({
    field: item.field,
    n: i < MAX_MARKED ? i + 1 : 0,
    boxes: i < MAX_MARKED ? item.boxes || [] : [],
    matches: item.matches || 0,
  }));
}

// A box in page pixels -> percentages of the snapshot, so it scales with the canvas.
export function boxStyle(box, width, height) {
  const pct = (v, of) => `${Math.max(0, Math.min(100, (v / of) * 100)).toFixed(3)}%`;
  return { left: pct(box.x, width), top: pct(box.y, height), width: pct(box.w, width), height: pct(box.h, height) };
}

function place(node, box, size) {
  Object.assign(node.style, boxStyle(box, size.w, size.h));
}

async function bitmap(base64) {
  const raw = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  return createImageBitmap(new Blob([raw], { type: "image/png" }));
}

const marks = new Map(); // field -> [box nodes]

// Turn one field's outlines up while its cell has focus or the pointer.
export function focusField(field, on) {
  for (const node of marks.get(field) || []) {
    node.dataset.on = on ? "true" : "false";
  }
}

function frame(size) {
  const wrap = el("div", undefined, "snapshot");
  wrap.dataset.w = String(size.w);
  return wrap;
}

// Draw the snapshot with the field outlines into `holder`. Returns the frame, or null when
// there is no snapshot (no browser): the caller says so in words.
export async function drawSnapshot(holder, page) {
  marks.clear();
  holder.replaceChildren();
  if (!page || !page.screenshot) return null;
  const image = await bitmap(page.screenshot);
  const size = { w: image.width, h: image.height };
  const canvas = el("canvas");
  Object.assign(canvas, { width: size.w, height: size.h });
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", t("wizard.review.shot"));
  canvas.getContext("2d").drawImage(image, 0, 0);
  const wrap = frame(size);
  wrap.append(canvas);
  for (const item of highlightModel(page.highlights)) {
    const nodes = item.boxes.map((box, i) => {
      const mark = el("div", undefined, `hl hl${item.n}`);
      place(mark, box, size);
      if (i === 0) {
        mark.append(el("span", item.field, "hl-label"));
      }
      mark.setAttribute("aria-hidden", "true");
      wrap.append(mark);
      return mark;
    });
    marks.set(item.field, nodes);
  }
  holder.append(wrap, el("p", t("wizard.review.shotHelp"), "muted"));
  wrap.size = size;
  return wrap;
}

// Pick mode: each candidate element is a button over the snapshot and an entry in a list
// for the keyboard. `choose(element)` receives {path, text}. Returns a function that ends it.
export function pickMode(wrap, list, elements, field, choose) {
  const buttons = [];
  list.replaceChildren(el("h4", t("wizard.pick.title", { field })), el("p", t("wizard.pick.keyboard"), "muted"));
  const items = el("ul", undefined, "pick-list");
  for (const element of elements) {
    const label = t("wizard.pick.element", { text: element.text });
    if (wrap && wrap.size && element.boxes && element.boxes.length) {
      const spot = el("button", undefined, "pick-spot");
      spot.type = "button";
      spot.setAttribute("aria-label", label);
      place(spot, element.boxes[0], wrap.size);
      spot.addEventListener("click", () => choose(element));
      wrap.append(spot);
      buttons.push(spot);
    }
    const entry = el("li");
    const pick = el("button", element.text, "small");
    pick.type = "button";
    pick.addEventListener("click", () => choose(element));
    entry.append(pick);
    items.append(entry);
  }
  list.append(items);
  if (!elements.length) list.append(el("p", t("wizard.pick.none"), "muted"));
  return () => {
    for (const spot of buttons) spot.remove();
    list.replaceChildren();
  };
}

// Step 3: records as a table whose cells light up their box on the page snapshot.
export function reviewTable(names, rows) {
  const wrap = el("div", undefined, "scroll");
  const grid = el("table");
  grid.append(el("caption", t("wizard.previewCaption"), "sr"));
  const head = el("tr");
  for (const name of names) head.append(el("th", name));
  const body = el("tbody");
  for (const row of rows) {
    const line = el("tr");
    for (const name of names) {
      const cell = el("td", row[name] === null || row[name] === undefined ? "" : String(row[name]));
      cell.tabIndex = 0;
      cell.dataset.f = name;
      for (const [kind, on] of [["focus", true], ["blur", false], ["mouseenter", true], ["mouseleave", false]]) {
        cell.addEventListener(kind, () => focusField(name, on));
      }
      line.append(cell);
    }
    body.append(line);
  }
  const thead = el("thead");
  thead.append(head);
  grid.append(thead, body);
  wrap.append(grid);
  return wrap;
}

export async function showPage(shot, url, recipe) {
  shot.replaceChildren(el("p", t("wizard.review.loading"), "muted"));
  try {
    const page = await api("/api/wizard/page", { url, fields: recipe.fields, record_selector: recipe.record_selector });
    const wrap = await drawSnapshot(shot, page);
    if (!wrap) shot.replaceChildren(el("p", t("wizard.review.noShot"), "muted"));
    return { wrap, page };
  } catch (error) {
    shot.replaceChildren(el("p", t("msg.error", { why: error.message }), "warn"));
    return null;
  }
}

// "Correct a field": click it on the snapshot (or pick it from the list), confirm, re-check.
// Returns false when there is nothing to click on (no snapshot): the caller goes back to
// the examples instead. `use(field, text)` gets the chosen element's text.
export function fixField(box, { snapshot, found, url, use }) {
  const elements = snapshot && snapshot.page.elements ? snapshot.page.elements : [];
  if (!snapshot || !snapshot.wrap || !elements.length) return false;
  const names = Object.keys(found.recipe.fields);
  const choose = el("select");
  choose.setAttribute("aria-label", t("wizard.pick.field"));
  choose.append(...names.map((n) => new Option(n, n)));
  const list = el("div");
  let stop = () => {};
  const begin = () => {
    stop();
    const field = choose.value || names[0];
    stop = pickMode(snapshot.wrap, list, elements, field, async (element) => {
      const picked = await api("/api/wizard/pick", { url, path: element.path });
      const before = found.preview.map((row) => String(row[field] ?? ""));
      confirmPick(list, picked, before, {
        use: async () => { stop(); box.replaceChildren(); await use(field, picked.sample[0] || element.text); },
        other: async () => begin(),
        cancel: async () => { stop(); box.replaceChildren(); },
      });
    });
  };
  choose.addEventListener("change", begin);
  box.replaceChildren(choose, list);
  begin();
  return true;
}

// Records of a project's preview (accepted ones only), as field names and rows for review.
export function installedRows(preview) {
  const names = [];
  const rows = [];
  for (const source of preview.sources || []) {
    for (const record of source.records || []) {
      if (record.status === "rejected") continue;
      const row = {};
      for (const field of record.fields) {
        row[field.name] = field.value;
        if (!names.includes(field.name)) names.push(field.name);
      }
      rows.push(row);
    }
  }
  return { names, rows };
}

// Put the picked text as the field's example, so the next check finds that element.
export function setExample(rows, field, value) {
  for (const row of rows.children) {
    const [name, example] = row.querySelectorAll("input");
    if (name.value.trim() === field) example.value = value;
  }
}
