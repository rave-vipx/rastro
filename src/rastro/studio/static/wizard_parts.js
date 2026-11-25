// Guided create building blocks with no step logic: the boxes the wizard owns inside a
// step, field rows, the live address check and file-name slugs.
import { $, el } from "./dom.js";
import { t } from "./i18n.js";
import { openable } from "./ui.js";

const MAX_FIELDS = 30;
let rowCount = 0;
const boxes = {};

// A container the wizard owns inside a step, placed before the step's buttons.
export function area(step, name) {
  if (boxes[name]) return boxes[name];
  const box = el("div", undefined, `wiz-${name}`);
  const parent = $(step);
  const nav = parent.querySelector(".step-nav");
  if (nav) nav.before(box);
  else parent.append(box);
  boxes[name] = box;
  return box;
}

function input(id, label, value, placeholder) {
  const node = el("input");
  Object.assign(node, { id, type: "text", value, placeholder, maxLength: 1000, spellcheck: false });
  node.setAttribute("aria-label", label);
  return node;
}

export function addRow(name = "", example = "") {
  if ($("field-rows").children.length >= MAX_FIELDS) throw new Error(t("wizard.maxFields", { n: MAX_FIELDS }));
  rowCount += 1;
  const row = el("tr");
  const cells = [
    input(`field-name-${rowCount}`, t("wizard.fields.name"), name, t("wizard.fields.namePlaceholder")),
    input(`field-example-${rowCount}`, t("wizard.fields.example"), example, t("wizard.fields.examplePlaceholder")),
  ];
  const key = el("input");
  Object.assign(key, { type: "checkbox", id: `field-key-${rowCount}` });
  key.setAttribute("aria-label", t("wizard.fields.useKey"));
  const remove = el("button", t("wizard.fields.remove"), "small");
  remove.type = "button";
  remove.addEventListener("click", () => { row.remove(); });
  for (const node of [...cells, key, remove]) {
    const td = el("td");
    td.append(node);
    row.append(td);
  }
  $("field-rows").append(row);
  return cells[0];
}

// Live check of the address, DOM-free: {ok, host} or {ok: false, key} for the message.
export function urlState(value) {
  const text = String(value || "").trim();
  if (!text) return { ok: false, key: "wizard.needUrl" };
  const href = openable(text);
  if (!href) return { ok: false, key: "wizard.url.notWeb" };
  return { ok: true, host: new URL(href).hostname };
}

// "Agenda de fiestas · Valle Azul" -> "agenda-de-fiestas-valle-azul": letters, digits, "-" only.
export function slug(name) {
  const plain = name.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  return plain.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
}
