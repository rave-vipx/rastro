# ADR 0006: explicit regional context with direct-only egress

- **Status:** accepted for the v0.1 regional-context slice; GEO-001 remains partial.
- **Requirements:** GEO-001–003, GEO-005 and REC-001.

## Context

Locale and timezone already identify extraction and recipe reuse, but one locale
cannot express ordered language preferences or an explicit currency. Validators
cannot safely interpret regional values unless the trusted caller's context reaches
Pydantic. An egress selector without a corresponding pinned transport would imply
proxy/geolocation behavior that Rastro does not provide.

Adding fields to serialized recipe keys also risks making existing SQLite rows
unreachable even when callers use all regional defaults.

## Decision

Add ordered, syntactically validated `Source.languages`, optional uppercase
three-letter `currency`, and explicit `egress="direct"`. Explicit languages form
Accept-Language in caller order; an empty tuple retains the existing locale fallback.
The direct-only literal and the acquisition boundary both reject unsupported egress
before DNS. No proxy, rotation, inferred location, or browser configuration is added.

Pass locale, languages, timezone, currency and egress as `ValidationInfo.context`
through the shared extraction function. This covers explicit, persisted, discovered,
model-proposed and HTTP-acquired recipes without changing parser/acquisition ports.
Currency interpretation remains trusted schema behavior; Rastro does not guess or
convert values, and validator defaults/transformations gain no evidence.

Extend `RecipeKey` with the ordered regional values. Its canonical JSON omits the
new fields only when languages are empty, currency is absent and egress is direct,
preserving the exact pre-change key used by existing SQLite rows. Any non-default
regional context gets a distinct key and never falls back to legacy memory.

## Consequences

- Existing callers and stored default-context recipes remain compatible.
- Language order and currency differences cannot share recipes.
- `direct` describes actual network origin, not browser geolocation. GEO-001 stays
  partial until a browser adapter makes browser geolocation applicable or a secure
  pinned non-direct egress adapter is introduced.
- There is no table migration, new dependency, proxy credential surface, new port,
  JSON API, lifecycle state, or automatic restriction bypass.

## Verification

Offline tests cover contract validation, ordered headers, pre-DNS egress rejection,
Pydantic context in explicit/acquired/learning paths, redirect preservation,
regional memory misses, language order and legacy canonical SQLite reuse.
