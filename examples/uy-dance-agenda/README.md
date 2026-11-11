# Uruguay Dance Agenda Pilot (`uy-dance-agenda`)

This directory contains the first public pilot job for Rastro (`COST-001`): feeding an agenda of dance events in Uruguay from two real public sources—a venue listings page (*cartelera*) and a ticket seller event page—together with a reproducible offline sample that uses synthetic HTML fixtures.

Full pilot findings, live run counts, field coverage, and ranked v0.3 obstacles are documented in [`docs/pilot/uy-dance-agenda.md`](../../docs/pilot/uy-dance-agenda.md).

## Files

- [`job.toml`](job.toml) — Declarative job with a `[dataset]` block (identity `title`, `venue`, `starts_on`) targeting four live Uruguayan sources. The v0.3 job reads SODRE, Sala Zitarrosa and Magnolio Sala listing pages plus the Tickantel event page; its schema (`starts_on` required, `starts_at` optional, both `localized`) differs from the two sample jobs. Synthetic copies of the three listing pages live in [`sample/v03/`](sample/v03/) and back `tests/integration/test_uy_dance_v03_sources.py`. Results: [`docs/pilot/uy-dance-agenda-v03.md`](../../docs/pilot/uy-dance-agenda-v03.md). The two source notes below describe the original v0.2 job:
  1. **Venue listings page (`sodre.gub.uy`)**: `https://sodre.gub.uy/espectaculos/` (CSS recipe over `.item .cyg--info.espectaculo` cards with a visible-text fallback). On the live site, `<time class="cyg--dia">` omits the HTML5 `datetime` attribute and renders localized Spanish prose/ranges (`"12 al 14 NOV 2026 - 20 H"`), so the live run yields `0` accepted records (`status = "insufficient_evidence"`) from the venue source.
  2. **Ticket seller event page (`tickantel.com.uy`)**: `https://tickantel.com.uy/ng/espectaculo/40021938/muestra-final-2026-area-tango-y-area-candombe` (JSON-LD `Event` recipe with a fallback for Tickantel's partial `PostalAddress`/`description` shape). Produces all `3` accepted records in the live run (`status = "success"`).
- [`sample/job.toml`](sample/job.toml) — Reproducible offline version of the same job pointing at synthetic local HTML files:
  - [`sample/sodre-cartelera.html`](sample/sodre-cartelera.html) — Synthetic venue listings page mimicking SODRE's `.item .cyg--info.espectaculo` DOM structure with invented dance events, adding ISO 8601 `datetime="..."` attributes and `<span class="cyg--ciudad">` nodes that the live site lacks so the venue recipe passes offline (`4` accepted records).
  - [`sample/tickantel-evento.html`](sample/tickantel-evento.html) — Synthetic ticket seller page mimicking Tickantel's `<script type="application/ld+json">` `@type = "Event"` array structure with invented dance events (`3` accepted records).
- [`sample/v2/`](sample/v2/) — Second offline snapshot ([`sample/v2/job.toml`](sample/v2/job.toml), [`sample/v2/sodre-cartelera.html`](sample/v2/sodre-cartelera.html), [`sample/v2/tickantel-evento.html`](sample/v2/tickantel-evento.html)) where one performance (`Suite del Río de la Plata`) is rescheduled from `20:00`/`20:30` to `21:00`.

## Target Schema Notes

All three jobs (`job.toml`, `sample/job.toml`, and `sample/v2/job.toml`) share the identical target schema (`schema_fingerprint = "12a221b71135d609770ebf7485e3229c32d0b2f3a7ef610ac8a0f1b32161c00c"`) and regional context (`locale = "es-UY"`, `languages = ["es-UY", "es"]`, `timezone = "America/Montevideo"`, `currency = "UYU"`):

| Field | Type | Required | Constraints | Domain semantics |
| --- | --- | --- | --- | --- |
| `title` | `string` | yes | `min_length = 1` | Production or event title as published by the source. |
| `performers` | `string` | yes | `min_length = 1` | Performing company, school, ensemble, or featured artists. |
| `venue` | `string` | yes | `min_length = 1` | Auditorium, theater, or dance venue hosting the performance. |
| `city` | `string` | yes | `min_length = 1` | City or locality code for the venue (e.g., `"Montevideo"` or `"UY"` when `addressLocality` is omitted in `PostalAddress`). |
| `starts_at` | `datetime` | yes | ISO 8601 | Performance start date and time. Must represent the show's `startDate`, never the ticket sale opening timestamp (`offers.validFrom`). |
| `ticket_url` | `url` | yes | HTTP(S) URL | Canonical ticket purchase or event detail URL. |
| `price` | `decimal` | no | `ge = 0` | Scalar ticket price when published on the listing or `Offer`. |
| `currency` | `string` | no | `min_length = 3`, `max_length = 3` | ISO 4217 3-letter currency code (e.g., `"UYU"`). |

### Dataset Identity & Sample Edge Cases

Each job includes a commented `[dataset]` block prepared for Slice D (`name = "uy_dance_agenda"`, `identity = ["title", "venue", "starts_at"]`, `match_tolerance_minutes = 90`). The offline sample fixtures in `sample/` and `sample/v2/` cover the four domain cases needed by dataset storage and diffing (Slices A, C, and D):

1. **Performance repeated on two dates (`sample/`)**: `Candombe de Invierno` at `Auditorio Nelly Goitiño` has two distinct functions (`2026-11-20T20:30:00-03:00` and `2026-11-21T20:30:00-03:00`). Because `starts_at` is part of the record identity, both dates remain distinct records.
2. **Rescheduled performance (`sample/v2/`)**: `Suite del Río de la Plata` at `Auditorio Nacional Adela Reta` changes its start time from `2026-11-28T20:00:00-03:00` (venue v1) / `2026-11-28T20:30:00-03:00` (ticket seller v1) to `2026-11-28T21:00:00-03:00` in `sample/v2/`.
3. **Cross-source near-match (`sample/`)**:
   - Title variation: the venue lists `Gala Anual 26: Tango y Milonga` while the ticket seller lists `Gala Anual 2026: Área Tango y Milonga` for the same venue (`Auditorio Nelly Goitiño`) and start time (`2026-11-26T20:00:00-03:00`), mirroring the live SODRE vs. Tickantel title discrepancy.
   - Start-time tolerance variation: in `sample/` (v1), `Suite del Río de la Plata` at `Auditorio Nacional Adela Reta` is listed at `20:00` by the venue and `20:30` by the ticket seller (30 minutes apart, within `match_tolerance_minutes = 90`).
4. **Ticket sale date vs. event date (`sample/tickantel-evento.html`)**: Each event embeds both `offers.validFrom = "2026-11-01T10:00:00-03:00"` (and visible `.venta-desde` sale date markup) and `startDate` (`2026-11-20`, `2026-11-26`, `2026-11-28`), ensuring `starts_at` binds exclusively to the performance start time.

## Running Offline (No Network, Zero Spend)

Validate and run the offline sample from the repository root:

```bash
uv run rastro job validate examples/uy-dance-agenda/sample/job.toml
uv run rastro job run examples/uy-dance-agenda/sample/job.toml --output json
```

Run the rescheduled second snapshot (`sample/v2/`):

```bash
uv run rastro job validate examples/uy-dance-agenda/sample/v2/job.toml
uv run rastro job run examples/uy-dance-agenda/sample/v2/job.toml --output json
```

## Running Against Live Sources

Validate and run the live pilot job (requires internet access; uses deterministic recipes with `inference_calls = 0` and `$0.00` external spend):

```bash
uv run rastro job validate examples/uy-dance-agenda/job.toml
uv run rastro job run examples/uy-dance-agenda/job.toml --output json
```

Export the offline sample as an HTML agenda (SCHED-007, DATA-004):
```bash
uv run rastro job load examples/uy-dance-agenda/sample/job.toml
uv run rastro dataset export examples/uy-dance-agenda/sample/agenda.sqlite --format html --sort-field starts_at --upcoming > agenda.html
```

The public page <https://rave-vipx.github.io/rastro/agenda/> is that JSON export rendered by [`render_agenda.py`](render_agenda.py) (`python render_agenda.py export.json > index.html`): Spanish, phone-first, grouped by day, with a "Solo baile" filter.

Load the live job into a dataset and export the upcoming agenda (run it from a scratch
directory; `agenda.sqlite` is written next to the job file):

```bash
uv run rastro job load job.toml
uv run rastro dataset export agenda.sqlite --format html --sort-field starts_on --upcoming > agenda.html
```
