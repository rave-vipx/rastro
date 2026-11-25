// Hub data mapping with no DOM: an offer from /api/registry/offers becomes what a card shows.
// Everything here is plain data; the card text comes from untrusted hub files, so it is only
// ever shown as text, and names that are not known permissions are counted, never echoed.

export const CAPABILITIES = [
  "stealth", "solve_challenges", "proxy_rotation", "impersonation", "http3", "unbounded_crawl",
  "robots_ignore", "page_actions", "network_capture", "screenshots", "persistent_session",
  "real_chrome", "cdp", "solver_plugins",
];
// Touch the person's own browser or code: only the command line can allow these.
export const GRANT_ONLY = ["persistent_session", "real_chrome", "cdp", "solver_plugins"];

export function daysSince(iso, today = new Date()) {
  const then = Date.parse(`${iso}T00:00:00Z`);
  if (Number.isNaN(then)) return null;
  const now = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  return Math.max(0, Math.round((now - then) / 86400000));
}

export function permissions(names) {
  const list = Array.isArray(names) ? names : [];
  const known = [...new Set(list.filter((name) => CAPABILITIES.includes(name)))];
  return {
    allowable: known.filter((name) => !GRANT_ONLY.includes(name)),
    blocked: known.filter((name) => GRANT_ONLY.includes(name)),
    unknown: list.filter((name) => !CAPABILITIES.includes(name)).length,
  };
}

export function siteOf(offer) {
  const pattern = (offer.source_patterns || [])[0] || "";
  return pattern.split("/")[0];
}

export function cardModel(offer, today = new Date()) {
  const card = offer.card || null;
  const health = offer.health || null;
  const needs = card ? card.engine_needs : null;
  return {
    id: offer.id,
    version: offer.version,
    spec: `${offer.id}@${offer.version}`,
    site: siteOf(offer),
    registry: offer.registry,
    fields: card ? card.fields.map((f) => ({ name: f.name, description: f.description })) : [],
    health: health ? (health.passed ? "ok" : "warn") : "none",
    days: health ? daysSince(health.last_verified, today) : null,
    browser: Boolean(needs) && needs.render !== "http",
    needs: permissions(needs ? needs.capabilities : []),
    technical: needs ? `${offer.id}@${offer.version} · ${needs.render} · ${needs.representation}` : "",
  };
}

export function asksPermission(model) {
  const { allowable, blocked, unknown } = model.needs;
  return allowable.length + blocked.length + unknown > 0;
}

// What the person typed for a site: a bare host, or a page address (only its host is used).
export function siteQuery(raw) {
  const value = String(raw || "").trim();
  if (!value) return "";
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `https:\/\/${value}`; // no literal scheme: scripts must not name external URLs
}
