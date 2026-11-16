// Bounded discovery and scrolling; the server validates the domain policies.
import { $, api, bind, requireFile, show, state } from "./dom.js";
let spider = {};

export function fillBounds(data) {
  for (const [kind, name, id] of [
    ["spider", "max_depth", "spider-depth"], ["spider", "max_pages", "spider-pages"],
    ["scroll", "max_steps", "scroll-steps"], ["scroll", "settle_ms", "scroll-settle"],
  ]) {
    const limit = data.limits[kind][name];
    const input = $(id);
    input.min = limit.min;
    input.max = limit.max;
    input.step = limit.step;
    input.value = data[kind]?.[name] ?? limit.default;
    show(id + "-range", `(${limit.min}–${limit.max}, step ${limit.step})`);
  }
  spider = data.spider || {};
  $("spider-enabled").checked = spider.mode === "spider";
  $("spider-links").value = spider.link_field || spider.links || "a::attr(href)";
  $("spider-link-label").textContent = spider.link_field ? "Link field" : "Link selector";
  $("spider-same-host").checked = spider.same_host !== false;
  $("spider-allow").value = (spider.allow || []).join("\n");
  $("spider-deny").value = (spider.deny || []).join("\n");
  $("scroll-enabled").checked = !!data.scroll;
  $("scroll-controls").disabled = data.scroll_disabled;
  show("scroll-reason", data.scroll_disabled ? "Scroll needs browser rendering; HTTP ignores scroll and stealth does not support it." : "Stops when no new records appear, or at the step cap.");
}

async function save(kind, policy) {
  const data = await api("/api/bounds", {file: requireFile(), sha256: state.sha, kind, policy});
  state.sha = data.sha256;
  show("controls-status", "Saved bounded " + kind + " settings.", "good");
}

export function initBounds() {
  bind("save-spider", "controls-status", async () => {
    const paths = (id) => $(id).value.split("\n").map(s => s.trim()).filter(Boolean);
    const policy = {...spider, mode: $("spider-enabled").checked ? "spider" : "list",
      max_depth: Number($("spider-depth").value), max_pages: Number($("spider-pages").value),
      same_host: $("spider-same-host").checked, allow: paths("spider-allow"), deny: paths("spider-deny")};
    policy[spider.link_field ? "link_field" : "links"] = $("spider-links").value;
    await save("spider", policy);
    spider = policy;
  });
  bind("save-scroll", "controls-status", () => save("scroll", $("scroll-enabled").checked ? {
    max_steps: Number($("scroll-steps").value), settle_ms: Number($("scroll-settle").value),
    stop_when: "no_new_records",
  } : null));
}

export function spiderLine(sources) {
  return sources.flatMap(s => (s.spider?.pages || []).map(p =>
    `${p.outcome}: ${p.host}${p.path}`)).join("; ");
}
