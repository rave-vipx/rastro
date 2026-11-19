"""Capability catalog entries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Capability:
    """One need an app or agent has, and how Rastro already covers it."""

    task: str  # the need, in plain language
    use: str  # how to do it in Rastro
    python: tuple[str, ...] = ()  # public names (rastro.<name>)
    cli: tuple[str, ...] = ()  # commands, each starting with "rastro"
    mcp: tuple[str, ...] = ()  # tool names served by `rastro mcp`
    limits: str = ""
    instead_of: str = ""  # what an agent must not hand-build
    docs: str = ""  # docs/<page>.md#<anchor>


def _names(text: str) -> tuple[str, ...]:
    return tuple(text.split())


EXTRACT_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        task="Extract typed records from one URL, file or HTML string",
        use='`rastro.scrape(target, Model)` with `rastro.css("h2::text")` selectors on a '
        "Pydantic model; `scrape_async` inside an event loop; `memory=` remembers the recipe",
        python=_names("scrape scrape_async css"),
        mcp=_names("scrape_file"),
        limits="A URL target needs the `http` extra, is fetched once and only its host is allowed",
        instead_of="requests/httpx plus BeautifulSoup plus your own validation",
        docs="docs/api.md#scrape",
    ),
    Capability(
        task="Control fetching, limits and region from Python",
        use="`Rastro.web(AcquisitionPolicy(allowed_hosts=...))`, then "
        "`engine.extract(source=Source(url=...))`",
        python=_names("Rastro AcquisitionPolicy Source Limits EgressProfile RegionalContext"),
        limits="Allowlisted hosts, robots.txt, pacing, DNS/SSRF checks and request budgets",
        instead_of="your own HTTP client, retry loop or robots.txt check",
        docs="docs/acquisition.md#extract-a-url",
    ),
    Capability(
        task="Fetch a detail page or a JSON endpoint from your own adapter code",
        use="`rastro.fetch(url, policy=AcquisitionPolicy(allowed_hosts=(...)), "
        'representation="json")` returns `Fetched` (status, final url, body, media_type, '
        "issues); `fetch_async` inside an event loop",
        python=_names("fetch fetch_async Fetched"),
        limits="Same acquirer as jobs: allowlist, robots.txt, egress, budgets, byte limit; "
        "refusals are explicit outcomes, never an empty success; plain HTTP only",
        instead_of="importing private acquirers or transports, or your own HTTP client",
        docs="docs/api.md#adapters-fetch-and-load-your-own-records",
    ),
    Capability(
        task="Store records your own code built, with provenance, in a Rastro dataset",
        use="`rastro.load_records(job_path, records, source_url=..., observed_at=...)`: the job "
        "file supplies dataset, fields and identity; same validation and snapshot rules as "
        "`rastro job load`, then Studio and `rastro dataset export` show them",
        python=_names("load_records"),
        limits="Every record validated before writing; identity required and unique; "
        "provenance records `locator = adapter`",
        instead_of="writing a JSON snapshot and running `job load` on it, or your own database "
        "just to inspect results",
        docs="docs/api.md#adapters-fetch-and-load-your-own-records",
    ),
    Capability(
        task="Scrape a list of URLs repeatedly without writing Python",
        use="A TOML job: `[[sources]] url = ...`, `[acquisition] allowed_hosts`, `[recipe]`, "
        "`[[fields]]`. Every run fetches the URLs again; `rastro new --site URL` drafts one",
        python=_names(
            "load_job parse_job run_job run_job_async run_job_file build_job_schema "
            "job_json_schema job_run_json_schema Job JobSource UrlJobSource FileJobSource "
            "JobField JobFieldType"
        ),
        cli=(
            "rastro new --site URL",
            "rastro job validate",
            "rastro job run",
            "rastro job preview",
            "rastro job schema",
        ),
        mcp=_names("job_validate job_preview job_extract"),
        limits="Only `allowed_hosts`; robots.txt; per-source `max_requests` budget",
        instead_of="a script with its own fetch loop, retries and per-site code",
        docs="docs/jobs.md#complete-toml-example",
    ),
    Capability(
        task="Read HTML, a JSON API, embedded JSON-LD, or JSON hidden in an attribute or script",
        use='`[recipe] strategy = "css"`, `"json"` (JSON pointers), `"jsonld"` (`item_type` '
        'takes one type or several, e.g. `["Event", "MusicEvent"]`) or `"embedded_json"` '
        '(`source = "input[name=GXState]::attr(value)"` or `"script#__NEXT_DATA__::text"`, '
        "then `record_path`); `representation` on the source",
        python=_names("Recipe CssRecipe JsonRecipe JsonLdRecipe EmbeddedJsonRecipe Representation"),
        cli=("rastro validate-recipe",),
        limits="Selectors follow the cost policy; recipes are data, never code",
        instead_of="your own JSON-LD or JSON response parsing",
        docs="docs/api.md#recipe-variants",
    ),
    Capability(
        task="Build selectors from example values or field names",
        use="`rastro.suggest(html, Model, examples=...)` or `rastro suggest`; "
        "with a model, field names alone are enough",
        python=_names("suggest Suggestion SuggestionError FieldConfidence"),
        cli=("rastro suggest", "rastro new --from page.html --example field=value"),
        mcp=_names("suggest_recipe suggest_context recipe_try job_new"),
        limits="The recipe must reproduce every example and extract `records_min` records",
        instead_of="guessing selectors by hand in a loop",
        docs="docs/suggest.md",
    ),
)
