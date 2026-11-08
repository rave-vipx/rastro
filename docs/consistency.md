# Semantic consistency

**Requirements:** QUAL-005 (historical consistency checks), QUAL-006 (separate indicators).

A recipe can return valid values that mean the wrong thing: a ticket sale date in the
event-date path passes every datetime validator. Consistency rules catch this. They are
declarative, generic and optional; a job without rules is unchanged.

## Rules

Declared as `[[consistency]]` entries (the example is `examples/uy-dance-agenda/sample/job.toml`).

| `kind` | Fails when | Parameters |
| --- | --- | --- |
| `after_observation` | datetimes of `field` lie before the observation time | `tolerance_minutes` (0), `max_violation_ratio` (0.0) |
| `distinct_ratio` | distinct values / records is below `min_ratio` | `min_ratio` (0.5), `min_records` (3) |
| `historical_shift` | the median of `field` moved more than `max_shift` versus dataset history | `max_shift`, `unit` = `days` or `ratio` (days), `min_history` (3) |

Unknown kinds and keys are rejected. Rules name fields and thresholds only; dance-specific
choices live in the example, never in the engine.

## Report

`evaluate_consistency(rules, records, observed_at, declared_fields=, history=, rejected=)` returns a
`ConsistencyReport` with three independent indicators:

- `completeness`: share of declared fields that carry a value.
- `structural_validity`: accepted / (accepted + `rejected`).
- `semantic_confidence`: the lowest rule confidence (1.0 when every rule passes).

`None` means "not assessed", never 1.0. A rule is `not_assessable` when it has no comparable
values, too few records or too little history; naive or unparsable datetimes are skipped, not guessed.
`verdict` is `inconsistent` if any rule is violated, `consistent` if some rule passed and none
failed, and `not_assessed` otherwise. Recipe CI must treat only `consistent` as a pass.

Confidence of a violated rule: `after_observation` is `1 - violation_ratio`; `distinct_ratio` is
`ratio / min_ratio`; `historical_shift` is `max_shift / shift`. All values round to four digits;
the evaluation uses no clock, I/O or randomness, so equal input gives byte-equal output.
Explanations contain field names and numbers, never source values.

## History

`history_from_store(store, dataset, fields)` reads stored values per field. Call it **before**
applying the candidate snapshot, or the history will contain the values under test.
