# Synthetic event agenda

An offline job combines listings from invented venues and a ticket seller into one dataset.
All names and page contents are synthetic, and all source hosts use reserved `.example`
domains. The examples make no network requests.

## Files

- `sample/job.toml`: Teatro Central (`agenda.example`) and Tickets Example
  (`tickets.example`), using venue cards and JSON-LD events respectively.
- `sample/v2/job.toml`: a second snapshot with a rescheduled performance.
- `sample/v03/job.toml`: three listing layouts, representing Teatro Central,
  Sala Norte (`salanorte.example`) and Club Faro (`clubfaro.example`).
- `queries/upcoming.sql`: an example dataset query.
- `sample/tiers/job.toml`: Sala Norte events with ticket tiers (a list of objects per event).
- `read_agenda.py`: runs that job and reads the nested records from Python.
- `repair_demo.py`: a complete offline drift and repair demonstration.

The first two jobs share a schema with required `title`, `performers`, `venue`, `city`,
`starts_at` and `ticket_url` fields, plus optional `price` and `currency`.
The regional context uses `es-UY`, UTC−3 (`Etc/GMT+3`) and `UYU` to exercise localized
values. The third job instead requires `starts_on` and accepts missing start times.

The samples cover repeated performances on different dates, a rescheduled performance,
near-matching titles across sources and start times within the configured 90-minute
matching tolerance. The ticket seller also publishes a sale opening date alongside the
actual event date: extraction must choose `startDate`, never `offers.validFrom`.
Invented venues include Teatro Central and Auditorio Horizonte; Puerto Claro is an
invented locality.

## Run offline

From the repository root:

```sh
uv run rastro job validate examples/event-agenda/sample/job.toml
uv run rastro job run examples/event-agenda/sample/job.toml --output json
uv run rastro job validate examples/event-agenda/sample/v2/job.toml
uv run rastro job run examples/event-agenda/sample/v2/job.toml --output json
```

Copy the sample to a temporary directory before loading it into a dataset, so generated
state stays outside the example tree:

```sh
scratch=$(mktemp -d)
cp -R examples/event-agenda/sample "$scratch/sample"
uv run rastro job load "$scratch/sample/job.toml"
uv run rastro dataset export "$scratch/sample/agenda.sqlite" --format html \
  --sort-field starts_at --upcoming > "$scratch/agenda.html"
```

## Nested data and results from code

Each event in `sample/tiers/` has several ticket tiers. No custom adapter is needed: the
job declares `tiers` as a `list` of `object` fields (`tier`, `price`), the recipe reads
them with one nested mapping (`each` plus `members`), and `rastro.read_records` returns
them with provenance.

```sh
uv run python examples/event-agenda/read_agenda.py
```

Expected output:

```text
success: 1 source, 3 accepted, 0 rejected
Noche de candombe: General 450, Platea 700, Palco 1200
  title provenance: article.event [match 0] >> h2::text
Tango en la rambla: General 500, Platea 800
  title provenance: article.event [match 1] >> h2::text
Milonga abierta: Entrada libre 0
  title provenance: article.event [match 2] >> h2::text
```

## Drift and repair offline

```sh
uv run python examples/event-agenda/repair_demo.py
```

The demonstration uses temporary files and a fixed clock. It quarantines a repair below
its configured record threshold, promotes after that threshold is met, rolls back, and
rejects valid sale dates used as event dates. All six dataset records survive, and no
model, browser or network is needed.
See [repair configuration and limits](../../docs/repair.md).

Expected output:

```text
rastro job load job.json --drift
exit=0 status=success stale=0 drift=none,none fields=
Mock Lab: moved and renamed title nodes (seed=42)
rastro job load job.json --drift
exit=3 status=partial_success stale=1 drift=structural_drift,none fields=*,city,currency,performance_id,performers,price,starts_at,ticket_url,title,venue
rastro recipe repair job.json --source 1
exit=3 decisions=quarantine,reject consistency=consistent,not_assessed
Configured repair.min_records=4 (was 5)
rastro recipe repair job.json --source 1
exit=0 decisions=promote,reject consistency=consistent,not_assessed
rastro job load job.json --drift
exit=0 status=success stale=0 drift=structural_drift,none fields=performance_id,title
rastro recipe compare job.json --from-version 1 --to-version 2
exit=0 schema_version=1 versions=1->2 changed=title
rastro recipe rollback job.json --version 1
exit=0 state=active version=1
rastro job load job.json --drift
exit=0 status=success stale=0 drift=structural_drift,none fields=performance_id,title
Mock Lab: valid sale date placed in event-date paths (seed=42)
rastro recipe repair job.json --source 2
exit=1 decisions=reject,reject consistency=inconsistent,not_assessed
All assertions passed; temporary dataset retained 6 records; external spend=$0
```
