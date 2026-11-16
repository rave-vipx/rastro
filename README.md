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

**Paste a URL, name the fields, and keep a database up to date.** Rastro saves a
verified recipe, fetches the URL again on later runs, and reports when a site changes.

> **Beta — 0.7.0.** This documentation accompanies the N1–N12 product cut. Studio's
> next-page, alert, repair, switch and registry controls are in this branch; URL-first
> creation (N1), spider, scroll and HTTPS registry fetching are integrated from their own
> branches. See the [stability policy](https://rave-vipx.github.io/rastro/stability/).

## Five minutes with Studio

Python 3.12+:

```sh
pip install "rastro-engine[studio,http]"
mkdir my-scrapers
rastro studio --root my-scrapers
```

1. Open the token-bearing loopback URL printed in the terminal. Choose **New scraper**
   and paste a page URL you have permission to scrape.
2. Name the fields you want, such as `title` and `price`, and copy one example value
   for each from the page. Preview the suggested recipe. With a configured model
   (`rastro studio --root my-scrapers --model SPEC`), **Ask AI** can propose selectors
   from field names; remote models additionally require `--allow-remote`.
3. Check the preview records, name the job `shop.toml`, choose a dataset file and a
   record key, and set a recipe memory file for repair. Press **Create job**.
4. Open the job, preview again, then **Load into dataset**. **Dataset** shows the
   stored records and lets you download them. Run **Load into dataset** again to
   update the same records; URL jobs fetch the page again each time.

**Auto-promote defaults off:** Studio's switch writes `[repair] auto_promote` on your
job, and a repair still serves only after the gates and the semantic judge pass.
**Stealth defaults off:** Studio's session switch, `--allow-stealth` or
`RASTRO_ALLOW_STEALTH=1` grants the running process only, never the job, and choosing
browser rendering or scroll does not grant it.

HTTP is the first path. If the page needs JavaScript, install the `browser` extra
and its browser runtime as described in the [browser guide](https://rave-vipx.github.io/rastro/acquisition/#javascript-pages), then
choose browser rendering. Bounded next-link pagination (Studio's **Next page** setting),
spider and scroll are opt-in; spider and scroll come with the acquisition integration.
See [Studio](https://rave-vipx.github.io/rastro/studio/) for the workflow and limits. No account is required.

## Install

```sh
pip install rastro-engine                    # core: parse supplied HTML, no network
pip install "rastro-engine[http]"            # fetch permitted URLs
pip install "rastro-engine[mcp,studio]"      # agent server and local web UI
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

## Self-repair

Give Rastro a recipe memory and it remembers what worked, together with a small sample of the
last successful page:

```python
result = rastro.scrape(html, Offer, url=URL, memory="recipes.db", repair="auto")
if result.repair:
    print(result.repair.promoted)
```

When the site renames a class or moves a block, the remembered recipe fails and Rastro
searches offline for a replacement, then checks it against the saved sample and your schema.
With `repair="propose"` (the default) it stores the candidate for review; with `repair="auto"`
it promotes the candidate only if every gate passes and returns the repaired result.
`repair="off"` reports the failure as is. Repair needs no AI and makes no extra network requests.
See [self-repair](https://rave-vipx.github.io/rastro/repair/).

## Bring your own AI

Rastro works fully offline and deterministically. You can also connect a model: a local one,
any API you have a key for, the command-line tool of a subscription you already have, or the
agent that drives Rastro over MCP. No provider is preferred. Rastro uses the model to propose
recipes when it has no examples, to propose repairs for pages whose structure changed, and to
check that a repair's values still mean the same thing.
**A model never writes your data. It proposes, and Rastro verifies:** every proposal goes
through the same validation and repair gates as a deterministic one, and sending page content
off the machine requires an explicit `allow_remote`.

On our 28-case synthetic benchmark, Rastro repaired 12 broken pages without a model, and 23
with a scripted model that answered correctly, with no wrong data in either case. The small
local models we tried recovered nothing new, and as judges they blocked correct repairs. A model
that is wrong both when it proposes and when it judges gets no wrong repair through: its
candidates must pass deterministic value checks before the judge. See
[bring your own AI](https://rave-vipx.github.io/rastro/ai/) for the numbers.

## Suggest selectors from examples

You know the values you want but not the CSS? Save the page and give one example per field:

```sh
rastro suggest page.html --url https://acme.example/careers \
  --example title="Senior Dev" --example salary=120
rastro new job.toml --from page.html --url https://acme.example/careers \
  --example title="Senior Dev" --example salary=120
```

`rastro suggest` (or `rastro.suggest(html, Offer, examples=[...])`) finds the repeating record
and the shortest selectors that reproduce your examples, and verifies them through the normal
extraction pipeline. `rastro new` writes a complete job file. See
[suggest from examples](https://rave-vipx.github.io/rastro/suggest/).

## Jobs and datasets

A job file (`job.toml`, `format_version = 1`) declares fields, sources and a SQLite dataset as
data, with no Python. Rastro loads records with stable identity, keeps the last valid rows when
a source fails, and marks stale origins. `job load`, `recipe repair` and `recipe apply` need a
`[dataset]` block and `recipe_memory` in the job; `rastro new` writes the `[dataset]` block when
you pass `--dataset` and `--identity`, and `recipe_memory` when you pass
`--recipe-memory recipes.sqlite3`:

```sh
rastro job validate job.toml
rastro job load job.toml --drift          # load approved fields, measure structural drift
rastro job watch job.toml                 # reload on the job's [schedule]
rastro job stats job.toml                 # success rates, timings, freshness per source
rastro dataset export offers.sqlite --format jsonl
rastro recipe repair job.toml --source 1  # propose a gated repair for a broken source
rastro recipe promote job.toml --source 1 --version 2 --reason "checked by hand"
rastro recipe apply job.toml --source 1   # write the serving recipe back into job.toml
```

The same operations are available from Python (`rastro.load_job_file`,
`rastro.repair_job_source`, `rastro.promote_job_recipe`, `rastro.apply_job_recipe`). See
the [complete job example](https://rave-vipx.github.io/rastro/jobs/#complete-toml-example),
[jobs](https://rave-vipx.github.io/rastro/jobs/), [datasets](https://rave-vipx.github.io/rastro/dataset/) and [drift](https://rave-vipx.github.io/rastro/drift/).

## For AI agents

- **MCP:** `pip install "rastro-engine[mcp]"`, then `claude mcp add rastro -- rastro mcp --root .`
  exposes job validate, preview and run, dataset records and recipe listing.
- **JSON CLI:** stdout is JSON; errors are one JSON object on stderr
  (`{"error", "message", "location"}`) and never echo page text or secrets.
- **Exit codes:** `0` success, `1` failed, `2` configuration or usage error, `3` partial success,
  `4` needs attention (open drift, stale source), `5` storage error after the run.
- Source values are labelled `"data_trust": "untrusted_source_data"`.

See [Rastro for AI agents](https://rave-vipx.github.io/rastro/ai-agents/) and [`llms.txt`](https://github.com/rave-vipx/rastro/blob/main/llms.txt).

## Share a recipe

The published object is a declarative recipe. A live job still scrapes its URL;
a bundle's explicit sample is offline replay evidence, not the next scrape target.
The repository includes synthetic listing, news and product packages. From a checkout,
this complete offline walkthrough copies the registry to a scratch folder:

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

Install verifies digests, selector policy and offline replay, and stays a candidate.
Repeat installs reuse the recipe. The included file-source job is an offline demo;
for a real site use your own URL job with matching source, schema and context, no
embedded recipe, and the same recipe store. Promote re-checks your current source.
See [the registry examples](https://github.com/rave-vipx/rastro/tree/main/examples/registry) and [sharing](https://rave-vipx.github.io/rastro/hub/)
for export/publish and the HTTPS `--registry-url` path. There is no
Rastro-operated registry or account server, and shared recipes cannot execute code.

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

## How it compares

| | Rastro | Scrapling + Pydantic by hand | Scrapy | BeautifulSoup |
| --- | --- | --- | --- | --- |
| Crawling / discovered links | Bounded opt-in spider (N3 integration) | You write it | Yes, its core strength | No |
| Next-link pagination | Bounded, opt-in | You write it | You write it | No |
| Browser scroll | Bounded, opt-in (N9 integration); no implied stealth | You write it | You wire a browser | No |
| Scheduling | Job schedule / watch | You write it | External scheduler | No |
| Typed, validated records | Yes, from your model | You wire it | Via items/pipelines | No |
| Field provenance (URL, selector, time, recipe version) | Built in | You write it | No | No |
| Explicit failure outcomes | Built in | You write it | Partly (errors, stats) | No |
| Recipe memory and verified self-repair | Built in, offline | Scrapling's adaptive relocation, ungated | No | No |
| SQLite dataset with identity and stale tracking | Built in | You write it | Via pipelines | No |
| Speed on a single page | Slower (validation and evidence) | Faster | Fast | Varies by parser |

Rastro builds on Scrapling and Pydantic. On our reproducible comparison it extracts the same
records as a competent Scrapling + Pydantic baseline, with more operational visibility and
higher latency; it has not shown better raw accuracy. See the
[comparison](https://rave-vipx.github.io/rastro/comparison/) for the numbers and the side-by-side code.

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
