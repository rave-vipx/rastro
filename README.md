<p align="center">
  <img src="https://raw.githubusercontent.com/rave-vipx/rastro/main/docs/assets/rastro-banner.svg" alt="Rastro — The open source web data engine. Web data, with evidence." width="100%">
</p>

<p align="center">
  <a href="https://github.com/rave-vipx/rastro/actions/workflows/ci.yml"><img src="https://github.com/rave-vipx/rastro/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12%2B-3776ab" alt="Python 3.12+"></a>
  <a href="https://github.com/rave-vipx/rastro/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-orange" alt="Apache 2.0 license"></a>
  <img src="https://img.shields.io/badge/status-beta-536d60" alt="Beta">
</p>

<p align="center">
  <a href="https://rave-vipx.github.io/rastro/">Documentation</a> ·
  <a href="https://rave-vipx.github.io/rastro/stability/">Stability policy</a> ·
  <a href="https://github.com/rave-vipx/rastro/blob/main/CONTRIBUTING.md">Contributing</a> ·
  <a href="https://github.com/rave-vipx/rastro/issues">Issues</a>
</p>

**Rastro is an open-source web data engine. Paste a URL, name the fields, and it keeps a
dataset up to date with a verified recipe.**

- **Scrape once, reuse the recipe.** A run scrapes the URL. The next run fetches that URL
  again and reuses the recipe. It does not call a model unless the site changed.
- **Studio, on your machine.** A local loopback page: paste the URL, name the fields,
  preview, create the job. Uploading HTML is not required.
- **Gated repair.** When the site changes, Rastro says so and proposes a repair.
  A repair becomes active only after the existing gates. Auto-promote is off until you
  turn it on.
- **Opt-in reach.** JavaScript, next-page pagination, bounded scroll and a bounded
  same-host spider are opt-in. Stealth is a separate operator grant and is never implied
  by browser or scroll.

## Five minutes with Studio

```sh
pip install "rastro-engine[studio,http]"
mkdir my-scrapers && rastro studio --root my-scrapers
```

1. Open the loopback URL printed in the terminal and choose **New scraper**.
2. Paste a page URL you have permission to scrape.
3. Name the fields you want (for example `title` and `price`) and copy one example value
   for each from the page; with a configured model, names alone are enough. Preview the
   records.
4. Give the job a dataset file and a record key, press **Create job**, then **Load into
   dataset**. Load again later to update the same records.

<!-- TODO(owner): add a Studio screenshot here (absolute https:// URL; PyPI renders this file). -->

Two switches, both off by default: **auto-promote** writes only `[repair] auto_promote` on
the job, and the **stealth** switch grants the running process, never the job. See the
[Studio guide](https://rave-vipx.github.io/rastro/studio/).

## Install

```sh
pip install rastro-engine                    # core: parse supplied HTML, no network
pip install "rastro-engine[http]"            # fetch permitted URLs
pip install "rastro-engine[studio]"          # local web UI
pip install "rastro-engine[browser]"         # render JavaScript pages (opt-in)
pip install "rastro-engine[postgres]"        # PostgreSQL datasets
```

The package is `rastro-engine`; the import is `rastro`. Python 3.12 or newer. The core needs
no account, model, browser or external service.

## Python quickstart

```python
from typing import Annotated, ClassVar
from pydantic import BaseModel, HttpUrl
import rastro


class Offer(BaseModel):
    __records__: ClassVar[str] = "li.job"
    title: Annotated[str, rastro.css("h3::text")]
    url: Annotated[HttpUrl, rastro.css("a::attr(href)")]


html = '<ul><li class="job"><h3>Senior Dev</h3><a href="https://acme.example/jobs/1">Apply</a></li></ul>'
result = rastro.scrape(html, Offer, url="https://acme.example/careers")
for offer in result.items:  # validated Offer instances
    print(offer.title, offer.url)
print(result.status, result.issues)
```

The target may be an HTML string, a `pathlib.Path`, or an `https://` URL (needs the `http` extra).
Every record carries field-level evidence: source URL, observation time, selector, method and
recipe version. Missing, invalid and ambiguous data are explicit outcomes, never a silent
empty list. Use `rastro.scrape_async` inside an event loop. See the
[quickstart](https://rave-vipx.github.io/rastro/quickstart/) and the [API reference](https://rave-vipx.github.io/rastro/api/).

## Why not a Scrapling + Pydantic script?

- **Recipe memory:** the recipe that worked is stored and reused on the next run.
- **Explicit outcomes:** missing, invalid and ambiguous data are reported, never an empty list.
- **Field provenance:** every value keeps its source URL, selector, time and recipe version.
- **Dataset identity:** loads update the same records and mark stale sources.
- **Gated repair:** a repair that would save bad data cannot pass the gates silently.

On the synthetic benchmark Rastro did not show better raw accuracy than a competent
Scrapling + Pydantic baseline, and it is slower because it validates and keeps evidence.
See the [comparison](https://rave-vipx.github.io/rastro/comparison/).

## How it compares

| | Rastro | Scrapling + Pydantic by hand | Scrapy | BeautifulSoup |
| --- | --- | --- | --- | --- |
| Crawling / discovered links | Bounded, opt-in | You write it | Yes, its core strength | No |
| Next-link pagination | Bounded, opt-in | You write it | You write it | No |
| Browser scroll | Bounded, opt-in; no implied stealth | You write it | You wire a browser | No |
| Scheduling | Job schedule / watch | You write it | External scheduler | No |
| Typed, validated records | Yes, from your model | You wire it | Via items/pipelines | No |
| Field provenance (URL, selector, time, recipe version) | Built in | You write it | No | No |
| Explicit failure outcomes | Built in | You write it | Partly (errors, stats) | No |
| Recipe memory and verified self-repair | Built in, offline | Scrapling's adaptive relocation, ungated | No | No |
| SQLite dataset with identity and stale tracking | Built in | You write it | Via pipelines | No |
| Speed on a single page | Slower (validation and evidence) | Faster | Fast | Varies by parser |

## Share a recipe

The published object is the recipe; a live job still scrapes its own URL. Install verifies
digests, selector policy and offline replay, and the recipe stays a candidate until you
promote it. Shared recipes cannot run code, and there is no Rastro-operated registry or
account server: a registry is a folder or a static HTTPS site you host. See
[the registry examples](https://github.com/rave-vipx/rastro/tree/main/examples/registry) and [sharing](https://rave-vipx.github.io/rastro/hub/).

```sh
rastro recipe search listing --registry examples/registry
rastro recipe search listing --registry-url https://recipes.example.org/registry
```

### Try the synthetic registry

From a checkout, this offline walkthrough copies the synthetic registry to a scratch folder:

```sh
scratch=$(mktemp -d)
cp -R examples/registry "$scratch/registry"
rastro recipe search synthetic --registry "$scratch/registry"
rastro recipe install synthetic-listing@1.0.0 --registry "$scratch/registry" \
  --store "$scratch/registry/listing-recipes.sqlite"
rastro recipe promote "$scratch/registry/listing.json" --source 1 --version 1 \
  --reason "Reviewed synthetic listing replay"
rastro job load "$scratch/registry/listing.json"
rastro job load "$scratch/registry/listing.json"  # same two records, no duplicates
rastro dataset export "$scratch/registry/listing-data.sqlite" --format jsonl
```

The included file-source job is an offline demo; for a real site use your own URL job with
the same recipe store. Promote re-checks your current source.

## Notify on failure

Copy this from the directory containing your job (POSIX shell, Python installed):

```sh
cat > notify.py <<'PYHOOK'
import json
import sys
from pathlib import Path

summary = json.load(sys.stdin)
Path("last-rastro-alert.json").write_text(json.dumps(summary, indent=2) + "\n")
PYHOOK
rastro job load shop.toml --drift --on-failure "python notify.py"
```

The hook runs for nonzero run outcomes (including partial success and needs attention),
receives a bounded JSON summary on stdin, and writes no records or page bodies.
`--on-failure` and `RASTRO_ON_FAILURE` are operator-only; a job or bundle cannot set
an executable hook. Commands run without a shell and have a timeout. See [jobs](https://rave-vipx.github.io/rastro/jobs/).

## More

- [Self-repair](https://rave-vipx.github.io/rastro/repair/): offline recipe repair with `repair="propose"` (default), `"auto"` or `"off"`.
- [Bring your own AI](https://rave-vipx.github.io/rastro/ai/): any local or hosted model proposes; Rastro verifies. Page content leaves the machine only with `allow_remote`.
- [Suggest selectors from examples](https://rave-vipx.github.io/rastro/suggest/): `rastro suggest` and `rastro new --from`.
- [Jobs](https://rave-vipx.github.io/rastro/jobs/), [datasets](https://rave-vipx.github.io/rastro/dataset/) and [drift](https://rave-vipx.github.io/rastro/drift/): `rastro job validate | load | watch | stats`, `rastro recipe repair | promote | apply`.
- [Rastro for AI agents](https://rave-vipx.github.io/rastro/ai-agents/): MCP server (`rastro mcp`), JSON CLI, exit codes and [`llms.txt`](https://github.com/rave-vipx/rastro/blob/main/llms.txt).

## Responsible use

URL acquisition is opt-in, limited to hosts you allow, honours robots rules, paces requests and
stops on 401/403/451. Stealth is an explicit operator opt-in; challenge solving additionally needs a job
request and the grant. There is no proxy rotation or external CAPTCHA service, and
Rastro promises no invisibility or unrestricted access. See [HTTP acquisition](https://rave-vipx.github.io/rastro/acquisition/) and [SECURITY.md](https://github.com/rave-vipx/rastro/blob/main/SECURITY.md).

## Stability

From 0.6.0 Rastro follows Semantic Versioning for the names in `rastro.__all__`, the CLI and
its JSON output, the job format and the recipe bundle format. Deprecated names keep working
with a warning for one minor release. See the [stability policy](https://rave-vipx.github.io/rastro/stability/) and the
[changelog](https://github.com/rave-vipx/rastro/blob/main/CHANGELOG.md).

## Contribute

Start with [CONTRIBUTING.md](https://github.com/rave-vipx/rastro/blob/main/CONTRIBUTING.md). Bug reports with a small **synthetic** HTML
fixture are especially useful. Tests run offline.

## License

[Apache License 2.0](https://github.com/rave-vipx/rastro/blob/main/LICENSE). No affiliation with other similarly named projects is implied.
