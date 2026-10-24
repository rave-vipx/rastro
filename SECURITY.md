# Security policy

## Report a vulnerability

Please use [GitHub private vulnerability reporting](https://github.com/rave-vipx/rastro/security/advisories/new), or email **admin@rave-app.com** if that channel is unavailable. Include the affected commit or version, a minimal synthetic reproduction, and the impact. Avoid posting credentials or sensitive page contents in a public issue.

Rastro is pre-alpha. Security fixes currently target `main`; no stable release or support SLA is offered yet.

## Current boundary

The first milestone processes caller-supplied HTML synchronously. It opens no network connections, executes no JavaScript, resolves no remote JSON-LD contexts, and exposes no management server. A `Source` URL is provenance metadata, **not** an authorization check for future network fetching.

Recipes accept CSS selectors or JSON pointers. They cannot contain executable code. Caller-provided Pydantic models and custom parser adapters are trusted Python code and can execute within the caller's process. Do not load untrusted schema modules.

Input size, candidate count, and JSON-LD block count have configurable limits. These limits are not a CPU-time or process-memory sandbox. Host untrusted workloads in an appropriately isolated process with external resource limits.

Evidence stores locators, not source excerpts. Query strings, fragments, and URL credentials are excluded or rejected. Validation diagnostics omit input values and custom validator messages; completion logs omit URLs and records. Extracted records and URL paths may still contain personal or sensitive data: the caller is responsible for storage, access, retention, and redaction.

The `extract_html` service does not persist data. CLI output is data and should be handled accordingly.

## Required before network acquisition

SEC-001 through SEC-003 require protocol, destination, DNS, and redirect enforcement, denial of private/loopback/metadata destinations by default, bounded requests, and explicit source policy. No network adapter is advertised as complete until those boundaries have regression tests. Do not add a convenience fetch path that bypasses them.

## Dependency maintenance

CI audits the locked runtime dependency graph. Dependabot checks Python dependencies and pinned GitHub Actions. A clean vulnerability scan is evidence about known advisories, not a guarantee of safety. Dependency-license and naming reviews are required before the first public package release.
