# v0.1 validation evidence

This page records independent review, QA, and hosted CI evidence for two exact
pull-request heads on 2026-10-30. It is not merge or release approval. Both pull
requests remain open, neither head is in `dev`, and v0.1 remains incomplete.

## Evidence summary

| Change | Requirements | Independent review | Independent QA | Hosted CI | Integration |
| --- | --- | --- | --- | --- | --- |
| [PR 12](https://github.com/rave-vipx/rastro/pull/12), integration workflow | Documentation and test acceptance evidence for the v0.1 requirements listed in the PR; no requirement status changed | Passed with no blocking findings | PASS; 0 failed, 0 blocked | [8/8 jobs passed](https://github.com/rave-vipx/rastro/actions/runs/41000922373) | Open; clean and mergeable |
| [PR 13](https://github.com/rave-vipx/rastro/pull/13), regional context | GEO-001–003, GEO-005, REC-001 compatibility | Approved; two P3 notes remain | PASS; no functional or security regression found | [8/8 jobs passed](https://github.com/rave-vipx/rastro/actions/runs/41002519056) | Open; clean and mergeable |

The commits and CI runs linked below are immutable evidence. Pull-request state
is current as of the date above and can change.

## PR 12: `dev` integration workflow

Validated head:
[`edaf079c5a5cef4db190a73ea851f0df43bdf4c6`](https://github.com/rave-vipx/rastro/commit/edaf079c5a5cef4db190a73ea851f0df43bdf4c6).
The change adds `dev` to CI push triggers, documents it as the integration
target, keeps documentation deployment restricted to `main`, and adds a
development work log. It changes no runtime source, tests, dependencies, or
deployment permissions.

Its recorded requirement context is DIFF-001, INT-001–004, REC-001–004,
AI-001–004, GEO-001–003, GEO-005, OBS-001–002, QUAL-001–004, and SEC-001–010.
This is traceability for the workflow and evidence change, not evidence that
those requirements are complete.

Review and QA were separate activities. Review inspected the diff, security
scope, requirement wording, exact SHA, and hosted CI, then approved it with no
blocking findings. QA independently checked the workflow triggers,
documentation deploy guard, contribution guidance, links, navigation, and a
strict MkDocs build. QA reported **PASS**, **FAIL: 0**, **BLOCKED: 0**.

The author reported the complete contribution checks against the head:

- Ruff lint passed; formatting passed for 85 files; mypy passed for 41 source
  files.
- Pytest passed 354 tests with 99.77% coverage.
- Both offline examples passed; the short offline benchmark completed 14 cases.
- Strict MkDocs, wheel/sdist build, Twine checks, locked runtime export, and the
  hashed dependency audit passed; the audit found no known vulnerabilities.

QA did not repeat the 354-test suite because the six-file diff contained no
runtime or test changes. It did run strict MkDocs successfully, find zero broken
relative documentation links, verify all five changed GitHub links returned
HTTP 200, and confirm the generated navigation entry. Hosted CI subsequently
ran the cross-platform test, quality/package, and runtime-audit matrix: all
eight jobs passed for the exact head.

Minor limits: the changed evidence links point to mutable `dev` content; QA did
not simulate push events or a documentation deployment. No live model, browser,
or release operation was tested by this workflow-only change.

## PR 13: explicit regional extraction context

Validated head:
[`7386fe3bfc63bcd74f37569e6f2e47d78c57a0b0`](https://github.com/rave-vipx/rastro/commit/7386fe3bfc63bcd74f37569e6f2e47d78c57a0b0).
The change adds ordered language preferences with valid descending HTTP quality
weights, explicit currency/time-zone/direct-egress context, regional recipe-key
isolation, and compatibility with pre-regional SQLite keys. GEO-001 remains
partial: browser geolocation and secure non-direct egress are not implemented.

The review-fix cycle retained its RED/GREEN evidence. Before the production
edit, the new 20-language header and defensive 21-language boundary tests
failed. After the minimal fix, the related suite passed 175 tests. Four
deliberate mutations in a disposable copy each made the relevant regression
test fail, covering language weights, the 20-language limit, legacy-key reuse,
and currency context during learned-recipe reuse.

Independent review approved the exact head and found no runtime regression.
Its full offline run passed 374 tests with 99.78% coverage; Ruff and mypy passed
for 41 source files. Independent QA then separately recorded:

- 374 tests passed with 99.78% coverage against the 90% gate.
- 7 focused regression cases passed; 133 tests in the changed areas passed with
  `ResourceWarning` treated as an error.
- 22/22 independent boundary checks passed.
- Ruff lint passed; formatting passed for the five focused files; mypy passed
  for 41 source files.

The focused reproduction uses the repository source explicitly and stays
offline:

```bash
PYTHONPATH="$PWD/src" python -m pytest -q --no-cov \
  tests/integration/test_acquisition.py::test_ordered_language_preferences_override_locale_header \
  tests/integration/test_acquisition.py::test_invalid_language_header_is_rejected_before_dns \
  tests/integration/test_extraction.py::test_custom_pydantic_init_is_not_bypassed_to_supply_context \
  tests/integration/test_intelligence.py::test_model_recipe_uses_currency_context_and_is_reused_after_restart \
  tests/integration/test_recipe_memory.py::test_pre_regional_sqlite_row_is_reused_after_restart
```

Hosted CI independently completed all eight cross-platform test,
quality/package, and runtime-audit jobs for the exact head.

Two P3 limitations remain. Documentation around a Pydantic model with a custom
`__init__` could state more directly that it does not receive Rastro's regional
context. The acquisition boundary rechecks language syntax and count but not
the contract's 64-character per-tag maximum when a caller bypasses normal
construction with `model_copy`; this can produce a long header but not header
injection. Currency validation is syntactic, time zone remains caller metadata,
and browser, proxy, live-model, and live-network behavior were outside this QA.

## Pending integration and release scope

Both PR heads are currently clean and mergeable into `dev`. The branch has no
protection or ruleset requiring a native GitHub approval, so the absence of one
is not a failed gate. The repository automatically deletes a pull request's
head branch after merge; approval to accept that deletion is still pending.
No merge, tag, package publication, or release has occurred.

This evidence does not verify Ollama end to end and does not claim a DIFF-001
advantage. Live Ollama evidence is tracked separately. The incomplete INT-002
and GEO-001 scope, measurable differentiation, distribution review, and final
release approval remain open for v0.1.
