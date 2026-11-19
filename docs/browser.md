# Browser controls

Browser sources (`render = "browser"` or `"stealth"`) accept a `[acquisition.browser]` table.
Every key is data; none runs code. The defaults reproduce the behaviour before the table
existed, so a job without it is unchanged.

```toml
[acquisition]
allowed_hosts = ["shop.example"]
render = "browser"

[acquisition.browser]
wait_selector = "#results .card"
wait_state = "visible"
blocked_domains = ["tracker.example"]
block_ads = true
screenshot = true
```

## Waiting for the page

| Key | Effect |
| --- | --- |
| `wait_selector` | Wait for this CSS selector instead of for network quiet. |
| `wait_state` | `attached` (default), `visible`, `hidden` or `detached`. |
| `network_idle` | Also wait for network quiet when a selector is set. |

A selector that never reaches its state fails the render as a transient browser failure; the
run reports it and keeps no empty document.

## What the page may load

`disable_resources` (default true) skips images, media and fonts. `blocked_domains` lists
hosts whose requests are refused, subdomains included. `block_ads` adds a built-in list of
ad-serving domains. These narrow what the guard already allows: the host allowlist, the
private-network checks and the request budget apply to every request regardless.

## Region

The browser context takes its locale and timezone from the job's `[region]`. An unknown
locale (`und`) is left to the browser default.

## Evidence

`screenshot = true` keeps one PNG of the rendered viewport on the acquisition result. It is
optional evidence: a failed or oversized (over 2 MB) capture is dropped and the document is
still used. The image is never printed in logs or reprs. Needs the `screenshots` grant for
jobs that do not come from the operator.

## Grant-only controls

These touch the operator's own browser, so they are granted explicitly, never by the job:

| Key | Needs |
| --- | --- |
| `real_chrome` | The installed Chrome, grant `real_chrome`. |
| `cdp_url_env` | Grant `cdp`. The key names an environment variable; the URL itself never appears in a job file, error or log. |
| `session_profile` | Grant `persistent_session`; see below. |

### Persistent sessions

`session_profile = "shop"` reuses a browser profile (cookies, local storage) between runs.
The name is mapped by the operator: the profile lives at `$RASTRO_PROFILE_DIR/shop`. A name
is a plain lowercase identifier and can never point outside that directory. Without
`RASTRO_PROFILE_DIR` the run refuses to start.

## Steps

Recipe page steps (click, type, wait and so on) run in the same page and under the same
guard. A stealth source runs them after navigation through Scrapling's hook.
