# v0.1 release evidence register

M5 is **in progress**. This register records evidence and unresolved gates; it is
not release approval. Requirements: DIFF-001, INT-001–004, REC-001–004,
AI-001–004, GEO-001–003, OBS-001/002, and specification sections 17–18.

## Acceptance status

| Gate | Evidence | Remaining work |
| --- | --- | --- |
| Clean installation and core without AI/browser | CI builds and installs the wheel outside the checkout; checks absence of HTTP/model extras and runs demo, doctor, memory and deterministic discovery | Repeat against the exact approved release artifacts |
| Explicit invalid/missing outcomes and provenance | Offline contracts and integration tests; comparison corpus; live example below | Semantic correctness is not guaranteed by type validation |
| Durable recipes | Offline restart/concurrency tests and two independent live example processes | Advanced lifecycle and repair belong to v0.2 |
| Useful timing and inference accounting | SDK metrics, live report, benchmark raw samples | Browser timing is not applicable to the current HTTP-only adapter |
| Configured Ollama works end to end | Offline adapter/proposal/budget tests; [live Ollama evidence](live-ollama-evidence.md): `qwen3:4b` learned, activated and reused a recipe across processes, and live/controlled failures stayed explicit | One model, one machine and synthetic inputs; a harder page failed 0/5 (safely); broader model quality is unmeasured |
| Competent comparison | Independent Scrapling + Pydantic baseline, 14 cases, 50 samples per executor/case | No measured accuracy/speed advantage; developer effort and maintenance benefit unmeasured; DIFF-001 remains open |
| Full v0.1 requirements | [Coverage table](roadmap.md) | Separate permitted structured APIs (INT-002), broader currency interpretation and explicit egress configuration (GEO-001) remain incomplete |
| Licenses, naming and distribution | Installed dependency inventory and public registry checks below | Ownership, naming/trademark review, bundled native notices and final distribution review remain open |
| Supported documentation tooling | Current strict MkDocs build | Plan the transition before Material's announced end of maintenance |
| Immutable release and publishing | Development changelog, locked dependencies and CI packaging checks | Approved version/tag/artifact hashes, registry ownership, protected Trusted Publishing and provenance |

## Live public example

[Books to Scrape](https://books.toscrape.com/) identifies itself as a scraping demo.
Its prices are test data, not commercial information. The example allows only that
host, keeps normal DNS/redirect/robots checks, and explicitly validates GBP syntax.
It does not run in default tests or CI.

```bash
uv sync --locked --extra http
uv run python examples/catalogue.py --database /tmp/rastro-catalogue-check.db
uv run python examples/catalogue.py --database /tmp/rastro-catalogue-check.db
```

Use a fresh database path for the first run. The example returns nonzero on a
non-successful result and prints the full structured outcome. Site availability
and layout can change; a failure must not be converted to empty success.

Recorded on **2026-10-25 UTC**, in separate Python 3.12.15 processes:

| Observation | First process | Second process |
| --- | --- | --- |
| HTTP / outcome | 200 / success | 200 / success |
| Recipe version / reused | 1 / false | 1 / true |
| Accepted records / evidenced fields | 1 / 2 | 1 / 2 |
| Requests / retries | 2 / 0 | 2 / 0 |
| Inference calls | 0 | 0 |
| Total seconds | 2.6270 | 1.9878 |

The result was `A Light in the Attic`, GBP `51.77`, with URL, timestamp, CSS locator
and recipe version for both fields. The requests include robots retrieval; each
process checks policy again. These two timings are observations, not a speedup
experiment. [Raw execution results](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/results/live-catalogue.json)
contain public demo values and operational metadata, with no saved HTML, cookies,
credentials or private content. A synthetic equivalent tests persistence and rejects
missing currency, other currency symbols, malformed decimals and negative prices.

## Dependency license inventory

Reproduce the installed-runtime metadata inventory after `uv sync --locked`:

```bash
uv run python -m benchmarks.licenses --output runtime-licenses.json
```

The [recorded inventory](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/results/runtime-licenses.json)
contains 22 transitive/direct runtime dependencies across core, HTTP and Ollama,
with locked versions, declared license metadata, license-file hashes and the lock
hash. It was collected from macOS arm64 installations; other platform wheels and
bundled native libraries still need review. Development/docs dependencies are not
part of this runtime inventory. Rastro's own license is Apache 2.0.

| Dependency | Locked version | Declared license |
| --- | --- | --- |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| cssselect | 1.6.0 | BSD-3-Clause |
| curl-cffi | 0.16.3 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| lxml | 6.1.3 | BSD-3-Clause |
| orjson | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| protego | 0.6.2 | BSD-3-Clause |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| pydantic-core | 2.50.1 | MIT |
| scrapling | 0.5 | BSD-3-Clause (LICENSE) |
| structlog | 26.1.0 | MIT OR Apache-2.0 |
| tld | 0.13.2 | MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| w3lib | 2.5.0 | BSD-3-Clause |

Scrapling's installed LICENSE contains the three-clause BSD terms; its metadata
contains free-form license text. The table normalizes that label for readability.
`lxml` includes separate `LICENSES.txt` notices; `curl-cffi` includes native code.
Top-level metadata alone cannot clear those bundled components. Preserve applicable
notices and review the exact artifacts selected for distribution, including the
MPL and alternative-license declarations. No legal compatibility or trademark
clearance is asserted by this inventory. The wheel contains Rastro's source, not
vendored dependencies; installers resolve dependencies separately.

## Naming and documentation lifecycle

On 2026-10-25 UTC, the public
[PyPI JSON endpoint for rastro-engine](https://pypi.org/pypi/rastro-engine/json)
returned HTTP 404. This does not reserve a namespace, prove that PyPI will accept
it, or establish ownership. The existing [rastro PyPI project](https://pypi.org/project/rastro/)
is an unrelated astrodynamics library. Review potential package/import/brand
confusion before publishing; this repository makes no affiliation claim.

[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) announces the
end of maintenance on **2027-05-05**, with Zensical as its successor. Maintainers
should evaluate migration before that date and before any release whose support
period crosses it. No docs tool migration is bundled into this evidence slice.
The existing strict build remains a release check.

## Next release decision

Close the unfinished technical and evidence gates first. Then verify namespace
ownership, review exact artifacts/notices, approve the version and release commit,
and configure protected publishing according to the [release checklist](releasing.md).
No tag, registry upload, Trusted Publisher, or publication secret is created here.
