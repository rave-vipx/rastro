# Proxies, rotation and blocked sources

This guide covers how a job reaches a site through your own proxies, how Rastro rotates
over them, and what to do when a source comes back blocked. The reference for every key is
in [acquisition](acquisition.md#network-egress).

## Proxies

A proxy is an egress profile you declare in the job. Credentials are names of environment
variables, never values:

```toml
[acquisition]
allowed_hosts = ["shop.example"]

[acquisition.egress_profiles.office]
proxy_url = "http://proxy-one.internal.example:3128"
username_env = "RASTRO_OFFICE_PROXY_USER"
password_env = "RASTRO_OFFICE_PROXY_PASSWORD"
```

A proxy sees every request and receives its credentials, so a job that declares one runs
only with your opt-in: `--allow-egress` on the command line, `allow_egress=True` in Python,
or `RASTRO_ALLOW_EGRESS=1`. Credential variables must be `RASTRO_*` names or names you
allow with `--allow-env`. A source picks one profile with `egress = "office"` in its
`[region]`, or uses `direct` (the default).

## Rotation

List two or more profiles in `rotate_egress` to spread a job's traffic over them:

```toml
[acquisition]
allowed_hosts = ["shop.example"]
rotate_egress = ["office", "backup"]

[acquisition.egress_profiles.office]
proxy_url = "http://proxy-one.internal.example:3128"
username_env = "RASTRO_OFFICE_PROXY_USER"
password_env = "RASTRO_OFFICE_PROXY_PASSWORD"

[acquisition.egress_profiles.backup]
proxy_url = "http://proxy-two.internal.example:3128"
```

- **Order.** Each transfer (robots.txt, a page, a redirect hop) of a source whose egress
  is `direct` goes through the next profile in the list, wrapping around. The order is
  shared by every source of the run.
- **Proxy failures.** When a proxy cannot be reached, the retry goes through the next
  profile, within `max_retries`.
- **Refusals are not retried elsewhere.** A 401, 403, 429 or 451 still marks the host as
  refused or cooling down for the whole run, whichever proxy carried it.
- **A source that names a profile keeps it.** Only sources on `direct` egress rotate.
- **Opt-in.** Rotation needs nothing beyond `--allow-egress` for your own jobs. Jobs from
  agent tools, bundles, registry recipes and model output also need the `proxy_rotation`
  grant (`--allow-untrusted ORIGIN:proxy_rotation`).
- **Credentials first.** Every rotated profile's credentials are read before the source's
  first request; a missing one fails the source with `egress_credentials_unavailable`
  before any DNS lookup.

The run report shows `proxy_rotation: true` and `proxies_used` (how many profiles carried
the source) under the source's `acquisition`, and lists `proxy_rotation` in
`capabilities_used`. Reports, logs and results name profiles, never proxy URLs or
credentials. The `egress` recorded for a rotated source, and the key recipe memory uses,
stays `direct`.

## Browser and stealth modes

`render = "browser"` and `render = "stealth"` use egress profiles and rotation too. The
browser launches with the chosen proxy; a source's browser context keeps the proxy it
opened with for all of its pages.

## The acquisition ladder

A refused source does not fail at once. `rastro job run`, `rastro job load` and
`rastro.scrape` climb from the job's own level through stronger ones (browser
impersonation, a browser, a stealth browser, challenge solving and, when the operator
installed one, a solver plugin) and stop at the first level that returns the page. Levels
the job's trust does not allow are skipped as `not_granted`. A robots.txt refusal never
climbs, and neither does a rate limit (429 or Retry-After): the host asked to wait, and the
shared cooldown stands. Recipe memory keeps the lowest level that worked and checks the levels below it
every ten runs. Each level tried is listed in the run report's `acquisition.ladder`; a
source refused at every level reports `ladder_exhausted`. See
[ADR 0039](decisions/0039-acquisition-ladder.md).

## What stays checked

The proxy changes the network path, not what may be fetched:

- Every target URL, including redirects and the resources a browser page loads, must be
  on `allowed_hosts`, and its address is resolved and checked locally before the request
  is sent to the proxy. A target that resolves to a private address is refused.
- The browser guard judges each request by its own URL, never by the proxy hop.
- The proxy resolves the target name itself, so the local address pin cannot bind the
  final connection. Trust the proxy accordingly; see
  [ADR 0008](decisions/0008-explicit-egress.md).

## Reading a blocked source

When a site refuses a source, the run report gives the source `reason: "blocked"` and a
`blocked` entry with a `kind`, the HTTP status when there is one, and a `docs` link to one
of the sections below. The kind comes from Rastro's own issue code, the status and fixed
markers in the refusal (a header or a known phrase); page text never enters the report.

```json
"blocked": {"kind": "cloud_ip_refused", "http_status": 403, "docs": "docs/egress.md#cloud-ip-refused"}
```

## Cloud IP refused

The site refuses traffic from cloud and hosting networks, which is where most servers and
CI runners live. The page usually says your network or autonomous system was banned.

- Run the job from a residential or office connection, or route it through an egress
  profile on such a network.
- Use `rotate_egress` over several such profiles to spread the load.

## Login wall

The page needs a signed-in user (a 401, an authentication header, or a password form).

- For an API that takes a token, add an `[[acquisition.auth]]` header whose value comes
  from an environment variable.
- Rastro does not sign in with a user's password for you.

## Challenge

The site answered with a challenge or captcha page instead of the content.

- Try `render = "stealth"`; add `solve_challenges = true` to let the stealth browser clear
  known challenges. When it clears one, the report shows `challenge_solved: true`.
- Slow down: raise `min_interval_seconds` and lower `max_concurrency`.

## Rate limited

The site answered 429. Rastro waits for its `Retry-After` and does not retry the host
before then.

- Raise `min_interval_seconds`, lower `max_concurrency`, or schedule fewer runs.

## Robots

The site's robots.txt disallows the path for Rastro's user agent.

- Check that you have permission to collect the page. If you do, set
  `[acquisition] robots = "ignore"` for this job.

## Geo blocked

The site answered 451: content unavailable in the region the request came from.

- Use an egress profile in a region where the content is offered, and set the source's
  `[region]` to match.

## Blocked

The site refused access (usually a 403) without a recognisable reason.

- Open the page in a normal browser from the same network to see what it shows.
- Try the steps for [cloud IP refused](#cloud-ip-refused) and [challenge](#challenge).
