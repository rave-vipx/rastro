// Settings data mapping with no DOM: server replies become rows of copy keys and values, so the
// rendering (connections.js) only places them. Nothing here reads a secret; only variable names.

export function costOf(cost) {
  if (cost === "free") return { key: "conn.model.free", vars: {} };
  if (typeof cost === "number") return { key: "conn.model.cost", vars: { usd: cost.toFixed(2) } };
  return { key: "conn.model.costUnknown", vars: {} };
}

export function modelOptions(models) {
  return (models.detected || []).map((found) => ({
    spec: found.spec,
    provider: found.provider,
    model: found.models[0],
    others: found.models.length - 1,
    cost: costOf(found.cost),
  }));
}

export function currentModel(models) {
  if (!models.current) return { key: "conn.model.none", vars: {} };
  return { key: models.current.remote ? "conn.model.usingRemote" : "conn.model.usingLocal", vars: {} };
}

export function channelState(row) {
  if (!row.configured) return { tone: "warn", key: "conn.chan.missing", vars: {} };
  const last = row.last;
  if (!last) return { tone: "muted", key: "conn.chan.ready", vars: {} };
  if (last.sent) return { tone: "ok", key: "conn.chan.sent", vars: { at: last.at } };
  return { tone: "bad", key: "conn.chan.failed", vars: { at: last.at, why: last.reason || "" } };
}

export function scheduleLine(row) {
  if (!row.set) return { key: "conn.sched.none", vars: {}, runs: [] };
  return { key: "conn.sched.words", vars: { words: row.words }, runs: row.next.slice(0, 3) };
}

export function proxyRows(data) {
  return (data.proxies || []).map((row) => ({
    name: row.name,
    address: `${row.host}:${row.port}`,
    env: row.env,
    canTest: data.allowed === true,
  }));
}

// One row per untrusted origin and capability; a locked one comes from the operator.
export function grantRows(data) {
  return (data.origins || []).flatMap((origin) => origin.capabilities.map((cap) => ({
    id: `${origin.origin}:${cap.name}`, origin: origin.origin, name: cap.name, on: cap.on, locked: cap.locked,
  })));
}
