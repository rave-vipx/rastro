# Pilot Report: Uruguay Dance Events Agenda (`uy-dance-agenda`)

- **Requirement IDs**: `COST-001`, `JOB-001`, `JOB-002`, `SCR-001`, `SCR-003`, `CORE-005`
- **Live query timestamp**: `2026-11-06T19:56:31Z` – `2026-11-06T19:56:38Z` (`2026-11-06T16:56:31-03:00` – `2026-11-06T16:56:38-03:00`, `America/Montevideo`)
- **Job file**: `examples/uy-dance-agenda/job.toml`
- **Offline sample**: `examples/uy-dance-agenda/sample/job.toml` (and `examples/uy-dance-agenda/sample/v2/job.toml`)
- **Schema fingerprint**: `12a221b71135d609770ebf7485e3229c32d0b2f3a7ef610ac8a0f1b32161c00c`
- **External AI / proxy / browser spend**: `$0.00` (`inference_calls = 0`, `egress = "direct"`)

---

## 1. Candidate Source Survey (`robots.txt` & Access Audit)

Twelve Uruguayan cultural venues and ticket sellers were surveyed on **2026-11-06** using Rastro's `WebAcquirer` (`respect_robots_txt = true`, `min_interval_seconds = 2.0`, `egress = "direct"`). No restriction, `robots.txt` rule, or challenge was bypassed (`SCR-003`).

### Selected Sources

| Role | Source URL | `robots.txt` status & policy | Structured data & HTML shape | Selection rationale |
| --- | --- | --- | --- | --- |
| **Venue listings page (*cartelera*)** | `https://sodre.gub.uy/espectaculos/` | HTTP 200 (`User-agent: *` / `Disallow:` empty — all public paths allowed) | 21 `.item .cyg--info.espectaculo` HTML cards (including 4 dance/ballet/tango/folclore productions); Yoast SEO JSON-LD contains only `WebPage`/`WebSite` metadata (no `Event` items) | Official national auditoriums cartelera (Auditorio Nacional Adela Reta & Auditorio Nelly Goitiño); publicly accessible without login or challenge, and links directly to Tickantel event pages. |
| **Ticket seller event page** | `https://tickantel.com.uy/ng/espectaculo/40021938/muestra-final-2026-area-tango-y-area-candombe` | HTTP 200 (`User-Agent: *`, `Allow: /`, `Disallow: /admin`, `Disallow: /cms`, `Disallow: /api` — public `/ng/espectaculo/*` pages allowed) | `<script type="application/ld+json">` array with 3 `@type = "Event"` items (Nov 12, 13, and 14, 2026 at 20:00 `-03:00`) | Public event page on Uruguay's primary state-backed ticketing platform (Antel) for a SODRE dance production (*Muestra final 2026: Área Tango y Área Candombe*), with Schema.org `Event` JSON-LD. |

### Discarded Candidate Sources

| Candidate | Role | Outcome / `issue_code` | Reason for discarding |
| --- | --- | --- | --- |
| `https://redtickets.uy/` | Ticket seller | `access_restriction` (`robots_disallowed`) / `operation_timeout` | `https://redtickets.uy/robots.txt` explicitly declares `User-agent: *` / `Disallow: /`. Dual-stack Cloudflare DNS also returns an unreachable `AAAA` address first. |
| `https://tickantel.com.uy/ng` | Ticket seller catalog root | HTTP 200, 0 `Event` items in HTML | Angular SPA shell (`<app-root>`) that embeds only `WebSite` and `Organization` JSON-LD in initial HTML and loads event listings via XHR under `/api` (disallowed by `Disallow: /api` in `robots.txt`). |
| `https://www.passline.com/pais/uruguay` | Ticket seller | `transient_failure` (`operation_timeout`) | Dual-stack Cloudflare host returns `AAAA` (`2606:4700:...`) first in DNS; `pin_destination` pins only the first resolved address (`validated[0]`) without Happy Eyeballs IPv4 fallback. |
| `https://entraste.com/` | Ticket seller | `transient_failure` (`operation_timeout`) | Same dual-stack Cloudflare IPv6 first-address pinning timeout as Passline. |
| `https://salacamacua.uy/cartelera/` | Venue | `permanent_failure` (`http_4xx`, HTTP 406) | Server WAF returns `406 Not Acceptable` to non-browser `User-Agent` (`Rastro/0.1`) on `/robots.txt` and `/cartelera/`. |
| `https://bluzzbar.uy/` | Venue | `permanent_failure` (`tls_verification_failed`) | Fails TLS certificate verification during `robots.txt` acquisition. |
| `https://teatroeltinglado.com.uy/cartelera/` | Venue | `access_restriction` (`https_required`) | `https://teatroeltinglado.com.uy/robots.txt` redirects (HTTP 301) to plaintext `http://www.teatroeltinglado.com.uy/robots.txt`, which Rastro blocks under `allow_http = false`. |
| `https://www.teatrosolis.org.uy/programacion` | Venue | HTTP 200 (1,488 bytes), 0 records | Initial HTML is a 1,488-byte `MVD CMS` client-side JavaScript redirect shell with no server-rendered listings or JSON-LD. |
| `https://www.teatrostella.com/cartelera` | Venue | HTTP 200 (28,601 bytes), 0 records | Next.js RSC shell renders `Cargando cartelera…` with `"espectaculos": []` in initial HTML and fetches events client-side via JavaScript. |
| `https://bns.gub.uy/es/calendario`, `https://salazitarrosa.montevideo.gub.uy/programacion`, `https://www.saladelmuseo.com.uy/shows.html`, `https://magnoliosala.uy/` | Venues | HTTP 200 (`robots.txt` allowed), 0 `Event` JSON-LD items | Server-rendered HTML cards omit `Event` JSON-LD, omit year/time in Spanish date strings (`06 de noviembre`, `VIE 6 NOV 20:00 H`, `Jueves 12 Nov`), and use relative `href` paths; retained as obstacle evidence while `sodre.gub.uy/espectaculos/` was selected as the primary venue source. |

---

## 2. Live Execution Results

Command executed on **2026-11-06T19:56:30Z** (`2026-11-06T16:56:30-03:00`):

```bash
uv run rastro job validate examples/uy-dance-agenda/job.toml
uv run rastro job run examples/uy-dance-agenda/job.toml --output json
```

### Job Summary

| Metric | Value |
| --- | --- |
| Overall job `status` | `partial_success` (CLI exit code `3`) |
| Sources executed | `2` (`succeeded = 1`, `partial = 0`, `failed = 1`) |
| Total `records_accepted` | `3` |
| Total `records_rejected` | `45` (across primary and fallback recipe attempts) |
| Total `fields_validated` | `24` (8 fields × 3 accepted records) |
| `inference_calls` | `0` |

### Outcome per Source

| Source URL | HTTP status | Source outcome (`status`) | Recipe attempts | Candidates evaluated | Accepted | Rejected | Issue codes observed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `https://sodre.gub.uy/espectaculos/` | `200` (197,456 decoded bytes; 2 HTTP requests including `robots.txt`) | `insufficient_evidence` | Attempt `0` (`css`, `insufficient_evidence`) → Attempt `1` (`css` fallback, `insufficient_evidence`) | `42` (21 cards × 2 attempts) | `0` | `42` (21 per attempt) | Attempt 1: 17× `validation_error` (`starts_at`), 2× `ambiguous_match` (`starts_at`), 2× `missing_evidence` (`venue`/`city` on card 0; `ticket_url` on card 6) |
| `https://tickantel.com.uy/ng/espectaculo/40021938/muestra-final-2026-area-tango-y-area-candombe` | `200` (79,586 decoded bytes; 2 HTTP requests including `robots.txt`) | `success` | Attempt `0` (`jsonld`, `insufficient_evidence`) → Attempt `1` (`jsonld` fallback, `success`) | `6` (3 `Event` items × 2 attempts) | `3` | `3` (from Attempt 0) | Attempt 1: `[]` (all 3 dance performance records accepted) |

### Field Coverage Across Live Sources

| Field | Schema type | Venue (`sodre.gub.uy/espectaculos/`, 21 cards) | Ticket seller (`tickantel.com.uy` event `40021938`, 3 `Event` items) |
| --- | --- | --- | --- |
| `title` | `string` (required) | **21 / 21** extracted via `h2.cyg--display-3::text` | **3 / 3** validated via `/name` |
| `performers` | `string` (required) | **21 / 21** extracted via `p::text` | **0 / 3** at `/performer/name` (omitted by Tickantel); **3 / 3** validated via fallback `/description` |
| `venue` | `string` (required) | **20 / 21** extracted via `address img::attr(alt)` (1 institutional card omits the venue image) | **3 / 3** validated via `/location/name` |
| `city` | `string` (required) | **0 / 21** dedicated city nodes in card DOM (venue city is implicit); fallback reuses `address img::attr(alt)` (**20 / 21**) | **0 / 3** at `/location/address/addressLocality` (omitted by Tickantel); **3 / 3** validated via fallback `/location/address/addressCountry` (`"UY"`) |
| `starts_at` | `datetime` (required) | **0 / 21** validated: `<time class="cyg--dia">` omits `datetime="..."`; visible text uses Spanish prose/ranges (17× `validation_error`) or 2 `<time>` nodes per card (2× `ambiguous_match`) | **3 / 3** validated via `/startDate` (`2026-11-12T20:00:00-03:00`, `2026-11-13T20:00:00-03:00`, `2026-11-14T20:00:00-03:00`) |
| `ticket_url` | `url` (required) | **20 / 21** extracted via `a.cyg--btn::attr(href)` (pointing to `https://tickantel.com.uy/ng/espectaculo/...`; 1 card has no ticket button) | **3 / 3** validated via `/offers/url` |
| `price` | `decimal` (optional) | **0 / 21** (SODRE listing cards omit prices; prices only appear on Tickantel) | **3 / 3** validated via `/offers/price` (`"0"` on `40021938`; `"300"` on `40021886/raza-oriental`; omitted in favor of `AggregateOffer.lowPrice`/`highPrice` on `40020133/bns-el-cascanueces`) |
| `currency` | `string` (optional) | **0 / 21** (omitted on SODRE listing cards) | **3 / 3** validated via `/offers/priceCurrency` (`"UYU"`) |

---

## 3. Ranked Obstacles for v0.3 (`DRIFT` & Acquisition Roadmap)

Obstacles discovered during the live pilot are ranked below by their impact on end-to-end record coverage across Uruguayan event sources and classified by cause.

| Rank | Cause classification | Obstacle | Live evidence | v0.3 capability candidate |
| --- | --- | --- | --- | --- |
| **1** | `missing structured data` | **Localized Spanish date text, date ranges, and yearless dates instead of ISO 8601 `datetime`** | On `sodre.gub.uy/espectaculos/`, all 21 cards omit `<time datetime="...">` and render Spanish strings or ranges (`"12 al 14 NOV 2026 - 20 H"`, `"22 NOV 2026 - 19 H"`), causing 17 `validation_error` failures on `starts_at` (`type = "datetime"`). `bns.gub.uy`, `salazitarrosa.montevideo.gub.uy`, `saladelmuseo.com.uy`, and `magnoliosala.uy` similarly render `"06 de noviembre"`, `"VIE 6 NOV 20:00 H"`, and split day/month spans without a year. | Locale-aware declarative date/time parser (`region.locale = "es-UY"`, `region.timezone = "America/Montevideo"`) and date-range expansion into per-performance candidate records. |
| **2** | `ambiguous dates (sale date vs event date)` | **Multiple valid timestamps per page/object (`offers.validFrom` and `WebPage.datePublished` vs. performance `startDate`)** | Every Tickantel `Event` JSON-LD object includes both `/startDate` (`"2026-11-12T20:00:00-03:00"`) and `/offers/validFrom` (`"2026-11-04T07:56:29-03:00"`, ticket sale opening time). On `sodre.gub.uy` and `salazitarrosa.montevideo.gub.uy`, the only ISO 8601 timestamps in the document are Yoast SEO `WebPage.datePublished` / `dateModified` metadata. | Semantic date-role disambiguation in schema-first discovery (`DIS-002`) that rejects `offers.validFrom`, `datePublished`, and `dateModified` when targeting an event's start timestamp, plus multi-schedule card splitting when a single `.espectaculo` card contains multiple `<time class="cyg--dia">` nodes (2 `ambiguous_match` failures on SODRE). |
| **3** | `missing structured data` | **Partial / polymorphic Schema.org `Event` JSON-LD and missing card fields across listing vs. detail pages** | Tickantel `Event` JSON-LD omits `performer.name` and `location.address.addressLocality` (requiring fallback pointers `/description` and `/location/address/addressCountry`), and switches between `Offer` (`/offers/price` on `40021938` and `40021886`) and `AggregateOffer` (`/offers/lowPrice` + `/offers/highPrice` on `40020133`). SODRE cards omit `city`, `price`, and `currency`, and `saladelmuseo.com.uy` uses relative `href="/shows/item/..."` links that fail `UrlField` (`AnyHttpUrl`) validation without base-URL resolution. | Multi-pointer field fallback lists within a single recipe (`["/offers/price", "/offers/lowPrice"]`), declarative constant/region defaults for implicit venue city (`region.city` / field default), and relative URL resolution against `source_url` before `url` validation. |
| **4** | `JavaScript-rendered` | **Client-rendered SPA catalogs and JS redirect shells return 0 events over plain HTTP** | `tickantel.com.uy/ng` renders an empty `<app-root>` Angular shell (only `WebSite`/`Organization` JSON-LD in initial HTML); `teatrosolis.org.uy/programacion` returns a 1,488-byte `MVD CMS` JS redirect shell; `teatrostella.com/cartelera` returns Next.js RSC markup with `"espectaculos": []` (`Cargando cartelera…`). | Optional browser rendering adapter behind the `Acquirer` port (`ACQ-002`, gated by per-job policy and cost budgets). |
| **5** | `pagination` | **Catalog discovery requires two-hop listing-to-detail traversal or paginated catalog feeds** | Tickantel has no static HTML catalog of dance events (`/ng` paginates via `/api` XHR), while `sodre.gub.uy/espectaculos/` lists the 21 cards and their outbound `https://tickantel.com.uy/ng/espectaculo/...` URLs (`a.cyg--btn::attr(href)`). Extracting complete records across a season requires following `ticket_url` links from the venue listing page to each ticket seller detail page. | Bounded two-stage link-following / pagination in declarative jobs (`max_pages`, `follow_field = "ticket_url"` restricted by `acquisition.allowed_hosts` and `max_requests`). |
| **6** | `challenge` | **WAF user-agent blocks (HTTP 406) and dual-stack Cloudflare IPv6 destination-pinning timeouts** | `salacamacua.uy` rejects `Rastro/0.1` with HTTP 406 (`permanent_failure`). `passline.com`, `entraste.com`, and `redtickets.uy` resolve both Cloudflare `AAAA` (`2606:4700:...`) and `A` (`104.26.x.x` / `172.67.x.x`) records; `pin_destination` pins `validated[0]` (`AAAA`) and times out (`operation_timeout`) on IPv4-only routes without trying `validated[1]`. | Happy Eyeballs (RFC 8305) address fallback across all validated `PinnedTarget` addresses returned by `pin_destination`, plus configurable transparent `User-Agent` contact metadata (`SCR-003`). |
| **7** | `rate limit` | **`robots.txt` full/API disallow rules and shared-host pacing** | `redtickets.uy/robots.txt` disallows `/` (`access_restriction: robots_disallowed`), and `tickantel.com.uy/robots.txt` disallows `/api`, requiring polite sequential fetching (`min_interval_seconds = 2.0`) of public HTML event pages. | Per-host `Crawl-delay` / `Retry-After` persistence across runs and conditional GET (`ETag` / `If-Modified-Since`) caching so unchanged event pages do not re-transfer payloads (`SYNC-001`). |
| **8** | `auth` | **HTTPS-to-HTTP redirect downgrades on `robots.txt` and broken origin TLS certificates** | `https://teatroeltinglado.com.uy/robots.txt` redirects (301) to `http://www.teatroeltinglado.com.uy/robots.txt` (`access_restriction: https_required`), and `bluzzbar.uy` fails TLS verification (`tls_verification_failed`). Ticketing checkout steps beyond event listings require user authentication. | Keep strict HTTPS/TLS and no-auth defaults (`SCR-003`), and surface clearer diagnostic guidance in `rastro job run` when a `robots.txt` redirect downgrades from `https://` to `http://`. |

---

## 4. Reproducible Offline Sample (`examples/uy-dance-agenda/sample/`)

To test the pilot job deterministically without network access or live site drift, `examples/uy-dance-agenda/sample/` provides synthetic HTML pages that preserve the exact CSS selectors of `sodre.gub.uy` and the `@type = "Event"` JSON-LD structure of `tickantel.com.uy` using invented dance events:

- **`examples/uy-dance-agenda/sample/job.toml`**: Validates and runs offline against `sample/sodre-cartelera.html` (4 accepted records) and `sample/tickantel-evento.html` (3 accepted records), producing `7` accepted records and `0` rejected records (`status = "success"`, exit code `0`).
- **`examples/uy-dance-agenda/sample/v2/job.toml`**: Second offline snapshot where `Suite del Río de la Plata` at `Auditorio Nacional Adela Reta` is rescheduled to `2026-11-28T21:00:00-03:00` across both sources.
- **Scenarios covered for dataset storage and diffing (Slices A, C, and D)**:
  1. **Performance repeated on two dates**: `Candombe de Invierno` at `Auditorio Nelly Goitiño` on `2026-11-20T20:30:00-03:00` and `2026-11-21T20:30:00-03:00`.
  2. **Rescheduled performance (`sample/v2/`)**: `Suite del Río de la Plata` changes start time from `2026-11-28T20:00:00-03:00` / `2026-11-28T20:30:00-03:00` in `sample/` to `2026-11-28T21:00:00-03:00` in `sample/v2/`.
  3. **Same event listed by both sources with slightly different titles (near-match)**: `Gala Anual 26: Tango y Milonga` (venue) vs. `Gala Anual 2026: Área Tango y Milonga` (ticket seller) at `Auditorio Nelly Goitiño` on `2026-11-26T20:00:00-03:00`, plus `Suite del Río de la Plata` listed 30 minutes apart (`20:00` vs. `20:30`) in v1.
  4. **Ticket sale date vs. event date**: `sample/tickantel-evento.html` embeds both `offers.validFrom = "2026-11-01T10:00:00-03:00"` (and visible `.venta-desde` sale date markup) and `startDate`, verifying that `starts_at` binds exclusively to the performance date.
