# ADR 0009: recipe strategy union and definition/metadata split

- **Status:** accepted for pre-v0.2 hardening.
- **Requirements:** REC-001–004, with REC-005 and REC-006 as v0.2 targets this
  change prepares for but does not implement; INT-002–004, QUAL-001–004,
  specification section 13 ("Recipe" data model) and the section 10 dependency rule.

## Context

Through 0.1.0, `Recipe` was one model with every strategy's selector as an optional
field (`item_type`, `record_selector`, `record_path`). A validator rejected illegal
combinations at runtime. That worked, but:

- Types did not say which selector a recipe has. Parsers asserted
  `record_selector is not None` or branched on `strategy` strings, and the JSON parser
  rechecked `record_path`.
- Serializations carried every selector, mostly `null`.
- The model proposal schema was a second, hand-written copy of the CSS and JSON-LD
  rules (`_CssProposal`, `_JsonLdProposal`), which had already drifted once (the
  0.1.0 fix that made `record_selector` required for CSS proposals).
- `StoredRecipe` mixed the definition with operational history (`selected_index`,
  timestamps, run counts). REC-005 adds candidate/active/degraded/retired states, and
  REC-006 needs failed repairs kept apart from a working version. Both are
  operational facts. They should not change the definition model or its
  immutability checks.

## Decision

**Strategy union.** `Recipe` is the shared base of `CssRecipe` (`record_selector`,
`::text`/`::attr()` mappings), `JsonLdRecipe` (optional `item_type`, JSON pointers)
and `JsonRecipe` (`record_path`, JSON pointers). They share `fields`, `version`,
`require_evidence` and `fallbacks`. Each variant forbids extra keys, so another
strategy's selector is rejected by type rather than by a combination check.
Fallbacks are typed per representation (`CssRecipe | JsonLdRecipe` for HTML,
`JsonRecipe` for JSON), and a shared validator keeps them flat and on one version.
Each variant also declares its `representation`.

`Recipe` remains the public name and loading entry point. A small `StrategyUnion`
base in the domain does three things. `Recipe(...)` selects the variant class from
`strategy` before Pydantic initializes it. The base's validator is rebuilt as a
Pydantic tagged union on `strategy`, so `Recipe.model_validate(...)` and
`model_validate_json(...)` return the variant. A field annotated `Recipe` in any
model (`ExtractionResult.learned_recipe`, `StoredRecipe.recipe`, or a user's own
model) validates as the union. Recipe files, the CLI `validate-recipe` command and
`examples/product.recipe.json` keep working unchanged.

**Proposals derive from the variants.** `RecipeProposal` uses the same union base.
Its `CssProposal` and `JsonLdProposal` variants are generated from `CssRecipe` and
`JsonLdRecipe` without `version`, `require_evidence` and `fallbacks`. Their
`strategy` is required. `proposal_schema()` stays the single public schema builder.
Its output equals the 0.1.0 schema except for the two variant titles
(`CssProposal`/`JsonLdProposal` instead of `_CssProposal`/`_JsonLdProposal`).
Titles do not constrain decoding, so model adapters behave as before. A test pins
this.

**Definition and metadata.** `StoredRecipe` is now `key`, `recipe` (the definition)
and `metadata: RecipeMetadata` (`selected_index`, `created_at`, `last_success_at`,
`successful_runs`). `version` stays in the definition because it names the
immutable definition and appears in recipe files. Lifecycle state and health will be
added to `RecipeMetadata` and its storage columns in v0.2. Definitions, their
equality-based conflict check and their serialized payload will not change.

**Legacy payloads and migration.** Payloads written before the union contain
`null` for unused selectors. M3 payloads also omit `require_evidence` and
`record_path`. The union base drops a known selector key whose value is `null` when
the selected variant does not declare it, so `null` means "absent". A non-null
foreign selector is still rejected. SQLite already stored the definition (`payload`)
apart from the metadata columns, so the table layout is unchanged. No migration runs
and `user_version` stays 1. Old rows are not rewritten. A repeated save compares
*validated* definitions, so a legacy payload and the equivalent new variant do not
conflict. Tests insert the literal M3 and 0.1.0 payload strings, including HTML
fallbacks and JSON recipes.

**Internal seams removed in the same change.** The engine passes `representation` to
every `AcquisitionPort`, and `WebAcquirer` passes `proxy` (None for direct egress) to
every `HttpTransport`. Both port signatures now require these keywords.
`AcquisitionResult.html` is now `body`, since it carries JSON as well. Prepared
extraction (parser selection by representation, learner or memory path, metric
stamping, acquisition failure results) moved from `Rastro` into
`application.service.ExtractionService`. `Rastro` is now a thin facade. Internal
ports have no external implementers before 1.0, so they get no compatibility shims.

## Alternatives considered

- **Keep the single model with a combination validator.** Smallest change, but it
  keeps optional selectors in every type, parser assertions and a parallel proposal
  schema. Every new strategy or lifecycle field would grow the same model.
- **A plain `Annotated[CssRecipe | JsonLdRecipe | JsonRecipe, discriminator]` alias
  named `Recipe`.** This is idiomatic Pydantic, but an alias has no
  `model_validate`, `model_validate_json` or constructor and no `isinstance` base.
  It would break recipe loading, the CLI, the benchmarks and user code without
  making anything safer.
- **A separate `parse_recipe()` function.** Explicit, but it gives up the public
  `Recipe.model_validate*` contract and still leaves fields typed `Recipe`
  validating as the bare base.
- **Generated proposal schema from `Recipe.model_json_schema()`.** That output uses
  `oneOf`, `$defs` and a discriminator mapping. Grammar-constrained decoders support
  these unevenly, and dropping fields from it after generation is fragile. Deriving
  proposal *models* from variant fields keeps the existing flat `anyOf` shape.
- **Normalize legacy payloads with a SQLite migration.** Rewriting rows would mean a
  version bump and a write path for every database on first open, only to remove
  `null` keys that validation already accepts. Reading legacy rows is cheaper and
  reversible. A migration will come when v0.2 adds lifecycle columns.

## Consequences

- Public changes, all recorded in the changelog: the variant classes and
  `RecipeMetadata` are exported; `StoredRecipe` metadata moved under `.metadata`;
  serialized recipes omit other strategies' selectors; `AcquisitionResult.html` is
  `body`; and the transport and acquisition ports require `proxy` and
  `representation`.
- Static type checkers see only the shared fields on `Recipe(...)`. Typed code should
  construct `CssRecipe`, `JsonLdRecipe` or `JsonRecipe`. The runtime still accepts
  `Recipe(strategy=..., ...)`.
- Validation errors for recipes now name the variant, for example
  `css.item_type: Extra inputs are not permitted`, or a `union_tag_invalid` error
  for an unknown strategy. Values are still never logged.
- Adding a strategy means adding a variant, registering it with `seal_union`, and
  adding a parser branch. Proposal support is opt-in per variant.
