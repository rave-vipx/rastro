# ADR 0031: operator network permissions

- **Status:** accepted for 0.7.0.

## Problem

Real sources sit on internal hosts (a private API, a staging server on 8443), publish a
robots.txt that the owner of the data has told the operator to ignore, or need a bearer
token. The acquisition policy refused all of that: public addresses only, ports 80 and 443,
robots always enforced, no credentials. A job file may come from someone else, so none of
these may become something a job can switch on.

## Decision

- **Operator only.** Private network, extra ports and the robots override are fields of
  `OperatorTrust`, set by a root CLI flag, an SDK keyword or a `RASTRO_*` variable, like
  `--allow-egress`. They are not part of the job schema, so a job, bundle, MCP argument or
  model output that names one is rejected as an unknown key. They reach the policy as a
  private attribute of a per-operation copy, never as serialised policy data. Default
  behavior is unchanged without a grant.
- **Metadata stays blocked.** The private-network grant is an explicit allow-list (RFC 1918,
  ULA, loopback, CGNAT). Link-local, unspecified, multicast, reserved, mapped and transition
  addresses and scoped `%` forms remain denied, because `169.254.169.254` is the cloud
  credential endpoint and no internal source needs it. Metadata services that live inside
  the opened ranges (AWS IMDS over IPv6 `fd00:ec2::254`, Alibaba Cloud `100.100.100.200`)
  are denied by exact address. Host allowlist, DNS pinning and
  per-hop checks do not change.
- **Robots is an assertion, not a bypass.** `--ignore-robots HOST` skips robots.txt for an
  exact host. Pacing, the 401/403/451 stop and 429 cooldowns remain. Each use is logged once
  (host only) and flagged in `metrics.robots_overridden`, so the operator's claim of
  permission is auditable.
- **Auth is declared by the job, authorised by the name rule.** `[[acquisition.auth]]`
  names an environment variable that must pass the existing secret-name rule, so it needs no
  new flag. The value is read per operation, held only in the operation, and sent to one
  exact host over HTTPS (or loopback HTTP with the private-network grant). It is withheld
  from robots.txt and cross-host redirects, and curl is told not to copy custom headers to a
  proxy's CONNECT request.
- **No browser auth.** A browser holds credentials in a profile and sends them from page
  scripts to any origin the page contacts. Honouring "exact host only" there needs request
  interception that this slice does not build, so the job is refused at load time.

## Consequences

- Granted ports now appear in the canonical request URL (previously the port was dropped,
  which was harmless while only 80 and 443 were possible).
- Hub bundles carry no acquisition block, so the auth declaration is not exported.
- A hostname that resolves to a private address needs the grant for the whole run; there is
  no per-host private grant. Use `allowed_hosts` to bound it.
