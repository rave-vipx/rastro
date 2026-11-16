// N10: sharing recipes is explicit; every source-derived value stays plain text.
import { $, api, el, requireFile, table } from "./dom.js";

function input(row, id, label, placeholder) {
  const caption = el("label", label);
  caption.htmlFor = id;
  const field = el("input");
  field.id = id;
  field.type = "text";
  field.maxLength = 1024;
  field.placeholder = placeholder;
  field.spellcheck = false;
  row.append(caption, field);
  return field;
}

function status(id) {
  const node = el("p");
  node.id = id;
  node.setAttribute("role", "status");
  node.setAttribute("aria-live", "polite");
  return node;
}

async function action(button, output, message, run) {
  button.disabled = true;
  output.textContent = message;
  output.className = "";
  try {
    await run();
    output.className = "good";
  } catch (error) {
    output.textContent = `Error: ${error.message}`;
    output.className = "bad";
  } finally {
    button.disabled = false;
  }
}

function trustText(trust) {
  return `Replay ${trust.replay_verified ? "verified" : "not verified"}; ` +
    `sample ${trust.has_sample ? "included" : "missing"}; ` +
    `${trust.days_since_verified} days since verification; format ${trust.format}`;
}

function renderEntries(entries, registry, results, output) {
  if (!entries.length) {
    results.replaceChildren(el("p", "No recipes match this search."));
    return;
  }
  const rows = entries.map((entry) => {
    const install = el("button", "Install as candidate into open job");
    install.type = "button";
    install.addEventListener("click", () => action(install, output, "Checking and installing…", async () => {
      const file = requireFile();
      const result = await api("/api/registry/install", {
        registry, spec: `${entry.id}@${entry.version}`, file,
      });
      output.textContent = `${result.id}@${result.version}: ${result.reused ? "reused" : "installed"} ` +
        `${result.state} in ${file}. Nothing promoted; auto-promote unchanged. ` + trustText(result.trust);
    }));
    return [entry.id, entry.version, entry.source_patterns.join(", "), entry.author.name,
      entry.license, trustText(entry.trust), install];
  });
  results.replaceChildren(table(
    ["Recipe", "Version", "Sources", "Author", "License", "Published trust indicators", "Action"],
    rows, "Registry recipes",
  ));
}

function searchForm(section) {
  const form = el("form");
  const row = el("div", undefined, "row");
  const registry = input(row, "registry-location", "Registry directory or HTTPS URL", "examples/registry");
  registry.required = true;
  const query = input(row, "registry-query", "Search recipes or sources", "listing");
  const search = el("button", "Search registry", "primary");
  search.type = "submit";
  row.append(search);
  const output = status("registry-status");
  const installStatus = status("registry-install-status");
  const results = el("div");
  results.id = "registry-results";
  form.append(row);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    action(search, output, "Searching registry…", async () => {
      results.replaceChildren();
      installStatus.textContent = "";
      const location = registry.value.trim();
      const data = await api("/api/registry/search", { registry: location, query: query.value });
      renderEntries(data.entries, location, results, installStatus);
      output.textContent = `${data.entries.length} recipe version(s) found.`;
    });
  });
  section.append(form, output, results, installStatus);
}

function publishForm(section) {
  section.append(el("h3", "Publish an exported public bundle"));
  section.append(el("p", "Choose a bundle you exported with public metadata and a sample you may share. " +
    "Publishing copies that sample and recipe into your local registry. It does not run git or push.", "warn"));
  const form = el("form");
  const row = el("div", undefined, "row");
  const bundle = input(row, "registry-bundle", "Exported bundle directory", "exports/my-recipe");
  bundle.required = true;
  const destination = input(row, "registry-destination", "Local registry directory", "examples/registry");
  destination.required = true;
  const publish = el("button", "Publish bundle locally");
  publish.type = "submit";
  row.append(publish);
  const output = status("registry-publish-status");
  form.append(row);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    action(publish, output, "Verifying and publishing…", async () => {
      const data = await api("/api/registry/publish", {
        bundle: bundle.value.trim(), registry: destination.value.trim(),
      });
      output.textContent = `Published ${data.entry.id}@${data.entry.version} locally, including its sample. Nothing pushed.`;
    });
  });
  section.append(form, output);
}

export function initRegistry() {
  if ($("registry-section")) return;
  const section = el("section", undefined, "card");
  section.id = "registry-section";
  section.setAttribute("aria-labelledby", "registry-heading");
  const heading = el("h2", "Registry");
  heading.id = "registry-heading";
  section.append(heading, el("p", "Search a directory under this Studio folder or an HTTPS registry URL. " +
    "Open a job with recipe memory before installing. Local paths are relative to the Studio folder.", "muted"));
  section.append(el("p", "Registry metadata is a publisher's claim, not a safety guarantee. Install checks digests, " +
    "selector policy and offline replay. New recipes enter as candidates; promotion needs a separate decision. " +
    "Installing never changes auto-promote or grants network access.", "warn"));
  searchForm(section);
  publishForm(section);
  $("view-jobs").append(section);
}
