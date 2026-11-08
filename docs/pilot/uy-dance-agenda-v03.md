# Uruguay dance agenda, v0.3 goal A: four live sources into one dataset

Requirements: SYNC-002/006, DATA-004/005, SCR-003, SCHED-007. Job:
`examples/uy-dance-agenda/job.toml`, run on
**2026-11-08, 18:55 local (21:55Z)** from an empty scratch directory.

```bash
cp examples/uy-dance-agenda/job.toml "$SCRATCH/job.toml" && cd "$SCRATCH"
uv run rastro job load job.toml                       # run 1: exit 3 (partial_success)
uv run rastro job load job.toml                       # run 2: exit 3, 0 records created
uv run rastro dataset export agenda.sqlite --format html --sort-field starts_on --upcoming \
  --timezone America/Montevideo
```

Exit 3 is expected: one SODRE card is rejected (below), so that source is `partial_success`.
Robots were checked by the acquisition layer; `min_interval_seconds = 2.0` was kept. No
challenge, WAF or refusal was bypassed.

## Result

| Source | Accepted | Rejected | Issue codes | Upcoming (`starts_on` ≥ 2026-11-08) |
| --- | ---: | ---: | --- | ---: |
| `sodre.gub.uy/espectaculos/` | 19 | 1 | `missing_evidence` (venue) ×1 | 17 |
| `salazitarrosa.montevideo.gub.uy/programacion` | 21 | 0 | `relocation_retention_skipped` (info) | 21 |
| `magnoliosala.uy/` | 11 | 0 | `relocation_retention_skipped` (info) | 11 |
| `tickantel.com.uy/ng/espectaculo/40021938/...` | 3 | 0 | none | 3 |
| **Total** | **54** | **1** | | **52** |

- **Second run:** created 0, unchanged 19 / 21 / 11 / 3 (54 records), same status and issues.
- **Upcoming = 52** rows with `starts_on` on or after today. Two of them (SODRE, 8 NOV, 18 H) had
  already started at run time, so 50 start later than the run. One production is listed twice
  (SODRE "Muestra final 26: Área Tango" on 12 NOV and Tickantel "Muestra final 2026: Área Tango y
  Área Candombe" on 12/13/14 NOV); the titles differ, so they are not merged (0 `uncertain`):
  51 distinct performances. All three goal thresholds hold on a live run (≥3 sources, ≥30
  upcoming events), with the caveats in the spot check.
- **Dance:** rule = case-insensitive match of `tango`, `folclore`, `folklore`, `ballet`, `danza`,
  `dance`, `milonga`, `candombe`, `salsa`, `flamenco`, `samba`, `murga`, `cumbia`, `zamba` or
  `malambo` in the title or the category (`category` is SODRE's last label or Sala Zitarrosa's tag
  text; Magnolio has none). **8 rows** match, 5 productions: Muestra final (4 rows), Raza oriental,
  Alejandro Balbis "Milonga por vos" (a concert, a false positive by title), El Cascanueces,
  "Encuentro, espectáculo de danza española". Probable misses by the rule: "Nelson Pino" (the
  subtitle "45 años en el tango" is not in `title`), "Piazzolla por los barrios", "Pago y arrabal".
  The rule is a keyword filter, not a classifier.

## Schema and engine changes (and why)

- Fields: `starts_on` (`date`, required, `localized`) and `starts_at` (`datetime`, optional,
  `localized`); identity `title`, `venue`, `starts_on`; `venue` required. `performers` and `city`
  became optional and `category` was added, because Zitarrosa and Magnolio publish no city and
  often no performers. Sala Zitarrosa and Magnolio state no time in a node a recipe can read, so
  they load with `starts_on` only.
- `document:` CSS prefix ([jobs.md](../jobs.md#css-field-scope-document-and-blank-text)): Zitarrosa
  and Magnolio print the venue once, outside the records. No constants.
- Whitespace-only text nodes no longer count as matches. Without it Magnolio's date node
  (`MIE 11 NOV <span>19:00</span>`) returned two matches and every card was `ambiguous_match`.
- Localized parsing: a list of times (`26 NOV 2026 - 19 H y 21.30 H`, SODRE's Gabriel Rolón) uses
  the first time; a localized `date` field accepts an ISO datetime (Tickantel's `startDate`) and
  keeps its local day. Both were rejected before.
- Venue for Magnolio is the desktop header's site name, not `og:title`: fixture sanitization
  rewrites attribute values that are not selector identifiers, so `meta[property="og:title"]`
  broke fixture replay on load (`Fixture replay does not reproduce the expected extraction`).

## Spot check (10 rows, against pages fetched 2026-11-08)

Checked against the live pages' own text and links (raw HTML read independently of the recipes,
fetched the same day, not re-fetched when this was written). Tickantel rows were not part of the
sample. "ok" means title, date, venue and link match the page.

| # | Source | Title | Date | Venue | Link | Result |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | SODRE | Raza oriental | 22 NOV 2026, 19:00 | Auditorio Nelly Goitiño | tickantel …/40021886/raza-oriental | ok |
| 2 | SODRE | El Cascanueces | 10 DIC 2026, 20:00 | Auditorio Nacional Adela Reta | tickantel …/40020133/… | ok (card also lists 13 y 20 DIC 17 H; first date only) |
| 3 | SODRE | Gabriel Rolón | 26 NOV 2026, 19:00 | Auditorio Nacional Adela Reta | tickantel …/40021474/… | ok (page also says 21.30 H; first time only) |
| 4 | SODRE | Nelson Pino | 15 NOV 2026, 20:00 | Auditorio Nelly Goitiño | tickantel …/40020890/… | ok |
| 5 | Sala Zitarrosa | Lucas Cary presenta «La oportunidad» | 11 NOV | Sala Zitarrosa | …/espectaculo/lucas-cary/ | ok |
| 6 | Sala Zitarrosa | Alejandro Balbis. Milonga por vos | 10 DIC | Sala Zitarrosa | …/espectaculo/alejandro-balbis/ | ok |
| 7 | Sala Zitarrosa | Encuentro, espectáculo de danza española | 21 DIC | Sala Zitarrosa | …/encuentro-espectaculo-de-danza-espanola/ | ok |
| 8 | Magnolio | Paz Carrara | JUE 12 NOV | Magnolio Sala | …/evento/pazcarraratresxtres | ok (21:00 H not loaded) |
| 9 | Magnolio | LED - Luces, extravío y delirio | VIE 27 NOV | Magnolio Sala | …/evento/led-luces,extravioydelirio | ok (also 28 and 29 NOV; first date only) |
| 10 | Magnolio | Benja Torres | SAB 12 DIC | Magnolio Sala | …/evento/benjatorres | ok (also DOM 13 DIC; first date only) |

Score: 10/10 ok, 0 wrong. Four of the ten are incomplete by design (only the first date or time
of a multi-date card is stored), which the "ok" does not hide. The year of Zitarrosa and Magnolio
dates is inferred (the pages print none): the observation year, rolling forward after 30 days.

## Remaining obstacles, by cause

- **Robots refusal (skipped, SCR-003):** Passline and Entraste answer `robots.txt` with 403, a
  refusal. Not fetched.
- **Date split across nodes:** Sala del Museo (`saladelmuseo.com.uy/shows.html`: weekday, day and
  month in three `<span>`s) and the BNS calendar (`bns.gub.uy/es/calendario`: month in a heading
  outside the row, day in a node with `<br/>`). A recipe reads one node per field and cannot join
  them; each page was fetched once and not loaded. Needs a concatenating field or a
  context-aware date, not a constant.
- **Time in another node:** Magnolio's hour sits in `span.hora`, so `starts_at` stays empty there.
  Same cause blocks Zitarrosa's per-card time (it is not on the listing at all).
- **Multi-date cards:** only the first date identifies a card. SODRE "Beatles y Bach" (9 OCT, then
  12 NOV) and "Encantéa" (6 al 8 NOV, run on 8 NOV) are excluded from "upcoming" although later
  dates remain; "El Cascanueces", "LED" and "Benja Torres" lose their other dates.
- **Missing venue:** SODRE's "Sodre celebra" card has an empty address. It is rejected
  (`missing_evidence`), and the source is flagged `partial_snapshot` stale (19 keys) until it is
  fixed or the venue becomes optional.
- **Pagination:** Sala del Museo loads more items by infinite scroll; not handled.
- **Duplicates across sources:** the same production under different titles is not merged.
- **Repair retention:** recipes with `document:` fields skip relocation snapshots
  (`relocation_retention_skipped`), so Zitarrosa and Magnolio have no relocation repair yet.
- **Consistency rules** on `starts_at` only cover SODRE and Tickantel rows (the others have no time).
