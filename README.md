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

**Typed, self-healing web scraping: declare a Pydantic model, get validated records with provenance; when the site changes Rastro detects it and proposes or applies a verified repair.**

> **Status: Beta — 0.6.0.** The public API follows the [stability policy](https://rave-vipx.github.io/rastro/stability/).

## Install

```sh
pip install rastro-engine                    # core: parse supplied HTML, no network
pip install "rastro-engine[http]"            # fetch permitted URLs
pip install "rastro-engine[mcp,studio]"      # agent server and local web UI
```

The package is `rastro-engine`; the import is `rastro`. Python 3.12 or newer. The core needs
no account, model, browser or external service.

## Quickstart

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
- **Exit codes:** `0` success, `1` failed, `2` configuration or usage error, `3` partial success.
- Source values are labelled `"data_trust": "untrusted_source_data"`.

See [Rastro for AI agents](https://rave-vipx.github.io/rastro/ai-agents/) and [`llms.txt`](https://github.com/rave-vipx/rastro/blob/main/llms.txt).

## Local GUI

`pip install "rastro-engine[studio]"` and `rastro studio --root path/to/jobs` open a
loopback-only page to validate a job, preview records with provenance, correct a selector and
copy the commands that connect your app. See [Studio](https://rave-vipx.github.io/rastro/studio/).

## How it compares

| | Rastro | Scrapling + Pydantic by hand | Scrapy | BeautifulSoup |
| --- | --- | --- | --- | --- |
| Crawling, link following, scheduling | No: you name the pages | You write it | Yes, its core strength | No |
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
stops on 401/403/451. There is no CAPTCHA solving, identity rotation or bypass of access
restrictions. See [HTTP acquisition](https://rave-vipx.github.io/rastro/acquisition/) and [SECURITY.md](https://github.com/rave-vipx/rastro/blob/main/SECURITY.md).

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
