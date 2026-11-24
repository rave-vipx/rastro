// Hub, Publish: turn the open project into a proposal for the hub's repository. Every field
// needs a description and the sample must be made up. The proposal is written into a local hub
// folder; nothing is sent and no data or credentials are included.
import { api, el, state } from "./dom.js";
import { t } from "./i18n.js";

function field(form, id, label, value = "", extra = {}) {
  const caption = el("label", label);
  caption.htmlFor = id;
  const input = el("input", undefined, "wide");
  Object.assign(input, { id, type: "text", maxLength: 1024, spellcheck: false, value, ...extra });
  const wrap = el("div");
  wrap.append(caption, input);
  form.append(wrap);
  return input;
}

function describedFields(names, notes) {
  const wrap = el("div", undefined, "fields");
  const inputs = {};
  for (const name of names) {
    const caption = el("label", t("hub.pub.fieldLabel", { name }));
    caption.htmlFor = `hub-field-${name}`;
    const input = el("input");
    Object.assign(input, { id: `hub-field-${name}`, type: "text", maxLength: 500, required: true,
      value: notes[name] || "" });
    wrap.append(caption, input);
    inputs[name] = input;
  }
  return { wrap, inputs };
}

export function descriptionsOf(inputs) {
  return Object.fromEntries(Object.entries(inputs).map(([name, input]) => [name, input.value.trim()]));
}

export function ready(descriptions, sample) {
  const values = Object.values(descriptions);
  return Boolean(sample.trim()) && values.length > 0 && values.every(Boolean);
}

export function publishSection() {
  const section = el("section", undefined, "card");
  section.setAttribute("aria-labelledby", "hub-pub-title");
  const title = el("h2", t("hub.pub.title"));
  title.id = "hub-pub-title";
  const note = el("div", undefined, "note note-warn");
  note.append(el("strong", t("hub.pub.strong")), el("p", t("hub.pub.proposal")));
  const list = el("ul");
  for (const key of ["u1", "u2", "u3", "u4"]) list.append(el("li", t(`hub.pub.${key}`)));
  section.append(title, note, el("h3", t("hub.pub.uploads")), list, el("p", t("hub.pub.never"), "muted"));
  const form = el("form");
  const project = el("p", state.file ? t("hub.pub.project", { file: state.file }) : t("hub.pub.noProject"));
  form.append(project);
  const source = field(form, "hub-source", t("hub.pub.source"), "1", { type: "number", min: 1, max: 999 });
  const sample = field(form, "hub-sample", t("hub.pub.sample"), "", { required: true });
  const destination = field(form, "hub-destination", t("hub.pub.destination"), "", { required: true });
  const id = field(form, "hub-id", t("hub.pub.id"), "", { required: true });
  const version = field(form, "hub-version", t("hub.pub.version"), "1.0.0", { required: true });
  const author = field(form, "hub-author", t("hub.pub.author"), "", { required: true });
  const license = field(form, "hub-license", t("hub.pub.license"), "", { required: true });
  const load = el("button", t("hub.pub.load"));
  load.type = "button";
  const slot = el("div");
  slot.id = "hub-field-notes";
  const status = el("p");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const send = el("button", t("hub.pub.publish"), "primary");
  send.type = "submit";
  let inputs = {};
  load.addEventListener("click", async () => {
    try {
      const data = await api("/api/registry/fields", { file: state.file });
      const kept = descriptionsOf(inputs);
      const built = describedFields(data.fields, kept);
      inputs = built.inputs;
      slot.replaceChildren(el("h3", t("hub.pub.fields")), built.wrap);
    } catch (error) {
      status.className = "bad";
      status.textContent = t("msg.error", { why: error.message });
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const descriptions = descriptionsOf(inputs);
    if (!state.file || !ready(descriptions, sample.value)) {
      status.className = "bad";
      status.textContent = t("hub.pub.needAll");
      return;
    }
    send.disabled = true;
    status.className = "";
    status.textContent = t("hub.pub.publishing");
    try {
      const data = await api("/api/registry/publish-job", {
        file: state.file, source: Number(source.value), sample: sample.value.trim(),
        registry: destination.value.trim(), id: id.value.trim(), version: version.value.trim(),
        author: author.value.trim(), license: license.value.trim(), descriptions,
      });
      status.className = "good";
      status.textContent = t("hub.pub.done", { spec: `${data.entry.id}@${data.entry.version}`,
        folder: destination.value.trim() });
      const next = el("pre");
      next.textContent = data.next_steps.join("\n");
      status.after(el("p", t("hub.pub.next")), next);
    } catch (error) {
      status.className = "bad";
      status.textContent = t("msg.error", { why: error.message });
    } finally {
      send.disabled = false;
    }
  });
  const row = el("div", undefined, "row");
  row.append(load, send);
  form.append(slot, row, status);
  section.append(form);
  return section;
}
