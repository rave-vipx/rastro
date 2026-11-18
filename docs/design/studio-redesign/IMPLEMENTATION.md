# Studio redesign: implementation handoff

Status: design reference, not code. Requirement IDs: N1, N2, N4, N5, N7, N10, N12 (`docs/product-needs.md`).
Inputs: `docs/design/studio-brief.md`, `docs/design/studio-sample-data.json`, and the artboards in `artboards/`.
Design canvas (owner only): https://claude.ai/artifact/4MnwCwGTBwKPJz6qZx1ELa

## 1. What the artboards are

Each `artboards/*.dc.html` file is one screen, written in a design-tool format. It is a pixel and copy reference, **not code to ship**. You can still open one in a browser to read it, but it will not run without the design tool's runtime.

| Artboard | Screen (brief section) | Notes |
| --- | --- | --- |
| `Main.dc.html` | 1 · Home with projects | Project cards, "start something new" entry points |
| `Estados.dc.html` | 1 · Empty folder, unrecognized database, loading | Three states stacked |
| `Resultados.dc.html` | 2 · Results + provenance panel | Most important screen |
| `Fuentes.dc.html` | 3 · Sources (coverage) | Includes non-blocking save warning |
| `Alertas.dc.html` | 4 · Alerts | Grouped by run, one plain sentence + one action |
| `Reparar.dc.html` | 5 · Fix a site | Before/after, reason, auto-apply switch (off) |
| `Nuevo.dc.html` | 6 · New scraper wizard (4 steps) | Step 2 is a block-style field builder |
| `Recetas.dc.html` | Recipes, collections, registry | Not in the brief; see section 6 |
| `Ajustes.dc.html` | 7 · Advanced project settings | Folded groups |
| `Tokens.dc.html` | Tokens + named components | Light and dark side by side |
| `HomeEN.dc.html`, `ResultsEN.dc.html` | English check | Same layout, English copy |
| `Movil.dc.html` | Results at 360–390 px | Table becomes a card list + bottom sheet |

### Reading the format

Keep: layout, spacing, sizes, radii, colors, copy, states, `aria-*` attributes, the semantic elements (`<table>`, `<button>`, `<label>`, `<details>`).

Ignore and re-implement in vanilla JS:

| Design format | Vanilla equivalent |
| --- | --- |
| `<x-dc>`, `<helmet>`, `support.js`, `data-props` | Nothing. Studio's own `index.html` and `studio.css` |
| `{{value}}` | `el.textContent = value` (never `innerHTML` for data) |
| `<sc-for list=… as=…>` | A render function that builds rows with `document.createElement` |
| `<sc-if value=…>` | `hidden` attribute or conditional render |
| `onClick="{{fn}}"` | `addEventListener('click', …)` |
| `class Component extends DCLogic` / `setState` | A small state object + `render()` per view |
| Inline `style="…"` | Classes in `studio.css` using the tokens below |
| Theme switch via `data-theme` on `.rs` | `data-theme` on `<html>` (auto / light / dark) |

## 2. Tokens

Fonts are system stacks, so nothing is downloaded (offline requirement).

```css
:root {
  --font-sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, Roboto, "Helvetica Neue", Arial, sans-serif;
  --font-mono: ui-monospace, "SF Mono", SFMono-Regular, "Cascadia Mono", Menlo, Consolas, monospace;
  --bg:#f5f5f7; --surface:#fff; --surface-2:#f2f2f4; --surface-3:#e8e8ed;
  --border:#e3e3e8; --border-strong:#c9c9d0;
  --text:#1d1d1f; --text-2:#55555b; --text-3:#6e6e73;
  --accent:#0066d6; --accent-hover:#0055b3; --accent-soft:#e8f1fc; --on-accent:#fff;
  --ok:#1f7a36; --ok-soft:#e6f4ea; --warn:#a04a00; --warn-soft:#fdf0e1; --fail:#c4161c; --fail-soft:#fdeaea;
  --bar: rgba(255,255,255,.78);
  --shadow-1: 0 1px 2px rgba(0,0,0,.04), 0 1px 1px rgba(0,0,0,.03);
  --shadow-2: 0 12px 32px rgba(0,0,0,.10), 0 2px 8px rgba(0,0,0,.06);
  --s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px; --s-5:24px; --s-6:32px; --s-7:48px;
  --r-sm:6px; --r-md:9px; --r-lg:14px; --r-pill:999px;
  color-scheme: light;
}
/* Dark: apply under @media (prefers-color-scheme: dark) for :root:not([data-theme=light]) and under :root[data-theme=dark] */
--bg:#111113; --surface:#1c1c1e; --surface-2:#242427; --surface-3:#2c2c2e;
--border:#2f2f33; --border-strong:#48484c;
--text:#f5f5f7; --text-2:#b4b4ba; --text-3:#94949a;
--accent:#3d9bff; --accent-hover:#62afff; --accent-soft:#16283f; --on-accent:#04101f;
--ok:#4cd471; --ok-soft:#12291a; --warn:#ffad33; --warn-soft:#33240c; --fail:#ff6b61; --fail-soft:#3a1714;
--bar: rgba(28,28,30,.78); --shadow-1: 0 1px 2px rgba(0,0,0,.4); --shadow-2: 0 16px 40px rgba(0,0,0,.5);
```

Type scale: display 30/600, title 20/600, heading 15/600, body 14/400, label 12/600, stat 22/600 tabular figures, mono 12.5.

Before merging, check AA contrast with a tool. The values were chosen for AA but were not measured in a browser.

## 3. Components

Named in `Tokens.dc.html`: `ProjectCard`, `StatusChip`, `SourceRow`, `ResultsTable`, `ProvenancePanel`, `Alert`, `BeforeAfter`, `EmptyState`, `EmptyCell`, `TrustMarker`, `Button`.

Rules every component keeps:

- A status always has an icon shape and a word, not only a color: circle = ok, triangle = warning, octagon = failure.
- One primary button per view. Controls are at least 36 px tall, and 44 px on mobile.
- Visible focus: `outline: 3px solid var(--accent)` on `:focus-visible`.

## 4. Data contracts (use `studio-sample-data.json`, not the mock's rows)

The artboards use placeholder events (Noche Fluo, noche.example…) written before the sample file was read. **Implement against `docs/design/studio-sample-data.json`.** The shapes below come from that file.

- **Price cell** `precio.state`:
  - `published`: show `text`. When it lists several tiers (`Sin costo / $ 160 / $ 199`), the table shows a summary and the provenance panel shows each tier with its availability.
  - `free`: show the bold word "Gratis".
  - `not_published`: faint italic "No publicado" with a minus-circle icon.
  - `source_failed`: `--fail` "La fuente falló" with an octagon icon.
- **Event status** `estado`: `en_vivo` → accent chip "En vivo"; anything upcoming shows no chip.
- **Source status**: `ok` | `warning` | `capped` (show "se leyeron N fichas; límite N" from `read`/`cap`) | `failed`. Always show `status_text` as the label. The brief also lists *robots.txt denied* and *structure changed*. Map those from whatever the engine reports.
- **Alerts** `kind`: `failure` | `drift` | `cap` | `warning`. The brief also lists quarantine and a refused promote. Each alert shows `text` and a single `action`.
- **Imported snapshot**: when a record comes from an import, the panel shows "Importado el {imported_at} · observado originalmente el {originally_observed_at}".
- **Repair**: `repair_candidate.comparison[]` (`field`, `before`, `after`, `changed`) drives `BeforeAfter`. `checks` drives the summary row. `auto_apply` starts `false`.
- **Provenance per field**: the panel needs, per field, the source URL, `observado_en` and the extraction or recipe version. Confirm that `/api/records` returns these. If it does not, that is a backend gap.

## 5. Screens → existing Studio API

Routes found in `src/rastro/studio/*.py`. Verify each before relying on it.

| Screen | Routes |
| --- | --- |
| Home | `/api/jobs`, `/api/connect` · **gap:** no project list with health/records/problem counts |
| Results + provenance | `/api/records`, `/api/export` (JSON, HTML) |
| Sources | `/api/health` · **gap:** per-source last fetch / read / rejected / cap may need an endpoint |
| Alerts | **gap:** no alert list route found. N5 requires the alert list without opening a log |
| Fix a site | `/api/repair`, `/api/promote` (with reason), `/api/apply`, `/api/auto-promote` |
| New scraper | `/api/connect`, `/api/preview`, `/api/extract`, `/api/suggest`, `/api/ai/suggest` ("Pedir a la IA"), `/api/pages`, `/api/pagination`, `/api/create` |
| Recipes / registry | `/api/registry/search`, `/api/registry/install`, `/api/registry/publish` |
| Advanced settings | `/api/bounds`, `/api/pagination`, `/api/session` (grants) |

## 6. Mock ideas that need a decision before building

The mock goes beyond the brief in places. Do not build these until the owner confirms them against `docs/product-needs.md`:

1. **"Search by keyword" as a start option** (`Main`, `Nuevo` step 1). Nothing in N1–N12 covers it, and it needs a search provider. Either drop it or treat it as a future need.
2. **"Hub on GitHub" and "Open pull request"** (`Recetas`). N10 says no accounts and no Rastro-operated server: search and install work from a local registry or an HTTPS URL, and publish writes into a local registry. Relabel this as "Registry (URL)" and make publish local. A GitHub repository can still serve as the HTTPS URL.
3. **Collections** of recipes. This is not a defined concept yet.
4. **Commands in Ajustes** (`rastro export`, `rastro serve`, `rastro recipes …`). These are illustrative. Replace them with the real CLI commands, or drop any that do not exist.
5. **Block-style field builder** (`Nuevo` step 2). It is a UI over the same job fields that `/api/extract` and `/api/suggest` produce, and it adds no new engine concept. The "Código" toggle shows the job as data and never runs anything.
6. **Stealth** appears as a mode choice in Ajustes. Per N12, it stays a Studio-process grant (`--allow-stealth`), off by default. A recipe or bundle can never set it.

## 7. Guardrails (do not weaken)

- Third-party values render with `textContent`. Fields flagged as looking like instructions get the `TrustMarker` and an explanation in the panel.
- Images and links that come from data never auto-load. Show a placeholder with a "Cargar" button.
- Auto-promote and stealth start off.
- Invalid or missing data never shows as an empty success: each empty cell carries its reason.
- No CDN and no web fonts. Keyboard navigation everywhere. Status is never conveyed by color alone.
- i18n: one `es`/`en` dictionary. Spanish copy is Rioplatense voseo ("Pegá la URL").

## 8. Suggested slices

1. Tokens + theme switch + i18n dictionary + app shell (header, project tabs). No behavior change.
2. Results table: price-cell states, filters, pagination, export (N5 partly).
3. Provenance panel, imported snapshot, TrustMarker, blocked images.
4. Sources + Alerts (N5), including the backend endpoints from section 5.
5. Fix a site (N7, N12).
6. Wizard (N1, N2, N4), then Home + empty states.
7. Advanced settings, registry (N10), mobile pass, English pass.

Each slice: tests, docs (`docs/studio.md`), and requirement IDs in the PR.
