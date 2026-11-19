# Rastro glossary

Rastro turns a website into validated records with a recipe written once, often by AI, and
reused without AI until the site changes. These are the words its docs, code and reports use.

## Jobs and runs

**Operator**:
The person who installs Rastro and runs jobs on their own machine; the only party whose input
is trusted by default.
_Avoid_: user (when trust matters), admin

**Operator job**:
A job that entered through the command line or the Python API, and so runs with the operator's
trust.
_Avoid_: local job, trusted job file

**Run report**:
The versioned, content-free summary of one job run or load: per-source outcomes, reasons,
counts, timings and capabilities used, with no records, page bodies, queries or secrets.
_Avoid_: run log, job summary

**Source notes**:
Names and counts that describe what happened to one source: how it was fetched, discovered,
crawled or repaired, and why it stopped.
_Avoid_: source metadata, debug info

## Trust and capabilities

**Input origin**:
Where a job or recipe came from: the operator, an agent tool, a shared bundle, a registry
recipe or model output. It is set by the entry point, never by the content.
_Avoid_: source (that word means a URL to scrape), trust level

**Capability**:
A named acquisition feature that changes what a job can reach, such as stealth rendering,
proxy rotation or network capture.
_Avoid_: permission, feature flag

**Grant**:
The operator's explicit permission for one origin to use one capability, or for any job to use
a grant-only capability.
_Avoid_: allow-list entry, override

## Fields and recipes

**Nested field**:
A schema field whose value is a list or an object made of scalar members.
_Avoid_: complex field, sub-schema

**Member**:
One scalar value inside an object field.
_Avoid_: sub-field, property

**Field mapping**:
The recipe entry that says where a field's value is read: one expression, a list of fallback
expressions, or an `each` scope with `members` or a single `value` for nested fields.
_Avoid_: selector (when it is more than one), extractor

**Null placeholder**:
Page text that means "no value", such as `N/A`, which a field treats as missing.
_Avoid_: empty marker, sentinel

**Repaired JSON**:
Page data that parsed as JSON only after a narrow, reported repair, such as escaping raw
control characters inside strings.
_Avoid_: lenient JSON, tolerant JSON

**Canonical page URL**:
The page address kept as provenance: scheme, host, port, path and the query without tracking
or secret-like parameters, sorted, with no fragment.
_Avoid_: clean URL, normalized URL

## Acquisition

**Acquisition ladder**:
The ordered levels Rastro may try to fetch a page, from plain HTTP up to an operator solver
plugin, stopping at the first that works or is not granted.
_Avoid_: fallback chain, escalation

**Ladder level**:
One rung of the acquisition ladder: HTTP, browser, stealth, challenge solving, proxy rotation
or solver plugin.
_Avoid_: tier, mode

**Page step**:
One bounded action a recipe runs in a rendered page before extraction: click, type, select,
scroll, wait or follow.
_Avoid_: page action script, macro

**Browser controls**:
The job settings that shape how a browser loads and observes a page, such as what to wait for,
which resources to block and what to capture.
_Avoid_: browser options, Playwright settings

**Captured response**:
A network response seen by a rendered page and kept, without its query, for extraction.
_Avoid_: intercepted request, XHR

**Crawl checkpoint**:
The saved frontier and progress of an unbounded crawl, so a later run resumes instead of
starting again.
_Avoid_: crawl state file, pause file

## Engines

**Engine**:
A library Rastro uses behind its strategy interfaces to fetch, parse or crawl.
_Avoid_: backend, driver

**Engine feature**:
One capability an engine offers, such as finding similar elements or blocking ads.
_Avoid_: engine option

**Engine map**:
The list of every engine feature with where Rastro exposes it, or why it does not.
_Avoid_: feature matrix, coverage table

**Adaptive candidate**:
A group of repeated page elements that look like records, found by similarity rather than a
fixed path, with a proposed locator per field.
_Avoid_: guess, suggestion

**Condensed page**:
A short Markdown rendering of a page, with hidden content removed, given to a model instead of
the full HTML.
_Avoid_: page summary, cleaned HTML

## Sharing

**Hub**:
The default public registry of shared recipes: a Git repository whose recipes are verified by
continuous integration.
_Avoid_: marketplace, store
