// Shared helpers. All source-derived text is set as plain text content; markup is never parsed.
export const $ = (id) => document.getElementById(id);
export const state = { token: "", file: "", sha: null };

export async function api(path, body) {
  const options = body === undefined
    ? {}
    : {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Studio-Token": state.token },
        body: JSON.stringify(body),
      };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(`${data.error || "error"}${data.message ? ": " + data.message : ""}`);
  }
  return data;
}

export function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

export function show(id, text, className) {
  const node = $(id);
  node.textContent = text;
  node.className = className || "";
}

// A table from a header list and rows of cell values (strings, numbers or DOM nodes).
export function table(headers, rows, caption) {
  const wrap = el("div", undefined, "scroll");
  const grid = el("table");
  if (caption) grid.append(el("caption", caption, "sr"));
  const head = el("tr");
  for (const name of headers) {
    const cell = el("th", name);
    cell.scope = "col";
    head.append(cell);
  }
  grid.append(el("thead"));
  grid.tHead.append(head);
  const tbody = el("tbody");
  for (const row of rows) {
    const line = el("tr");
    for (const value of row) {
      const cell = el("td");
      if (value instanceof Node) cell.append(value);
      else cell.textContent = value === null || value === undefined ? "" : String(value);
      line.append(cell);
    }
    tbody.append(line);
  }
  grid.append(tbody);
  wrap.append(grid);
  return wrap;
}

export function pill(text, good) {
  return el("span", text, `pill ${good ? "good" : "bad"}`);
}

// Run an action with its button disabled; errors go to the status element.
export function bind(buttonId, statusId, action) {
  const button = $(buttonId);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await action();
    } catch (error) {
      show(statusId, `Error: ${error.message}`, "bad");
    } finally {
      button.disabled = false;
    }
  });
}

export function requireFile() {
  if (!state.file) throw new Error("open a job file first");
  return state.file;
}
