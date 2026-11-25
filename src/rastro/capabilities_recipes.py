"""Capability catalog entries."""

from __future__ import annotations

from rastro.capabilities_extract import Capability, _names

RECIPES_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        task="Detect when a site's structure changes",
        use="`rastro job load --drift` measures drift per source; acknowledge with `ack-drift`",
        python=_names("acknowledge_job_drift"),
        cli=("rastro job load --drift", "rastro job ack-drift"),
        mcp=_names("drift_status"),
        limits="Measured on the first page of a source",
        instead_of="comparing page snapshots yourself",
        docs="docs/drift.md#open-drift",
    ),
    Capability(
        task="Remember, version and inspect recipes",
        use="`recipe_memory` in a job or `memory=` in `scrape`; versions have states and history",
        python=_names("SQLiteRecipeRepository RecipeRepository RecipeLifecycle"),
        cli=(
            "rastro recipe show",
            "rastro recipe compare",
            "rastro recipe rollback",
            "rastro recipe prune",
        ),
        mcp=_names("recipe_list recipe_history"),
        instead_of="storing selectors in your own config or database",
        docs="docs/memory.md",
    ),
    Capability(
        task="Repair a broken recipe and promote it safely",
        use="`rastro recipe repair` proposes gated candidates; `promote` needs a reason; "
        "`apply` writes the serving recipe into the job",
        python=_names("repair_job_source promote_job_recipe apply_job_recipe SelfRepair"),
        cli=("rastro recipe repair", "rastro recipe promote", "rastro recipe apply"),
        mcp=_names("recipe_repair recipe_promote recipe_apply"),
        limits="Nothing serves until every gate passes; `auto_promote` is off by default",
        instead_of="editing selectors in place when extraction breaks",
        docs="docs/repair.md",
    ),
    Capability(
        task="Let your own agent propose repairs as the model",
        use="`repair_context`, then `recipe_propose`, judge `semantic_review`, "
        "then `recipe_promote`",
        mcp=_names("repair_context recipe_propose semantic_review"),
        limits="The agent proposes recipes, never records; `recipe_propose` never promotes",
        instead_of="asking a model to write records or patch the dataset",
        docs="docs/mcp.md#agent-in-the-loop",
    ),
    Capability(
        task="Explore a live page while writing a recipe",
        use="MCP `browser_snapshot` (hardened text and outline), `browser_extract` (try a "
        "recipe), `browser_network` and `browser_screenshot`, each after optional page steps; "
        "`rastro shell JOB` opens a Python shell on a source's parsed page",
        cli=("rastro shell",),
        mcp=_names("browser_snapshot browser_extract browser_network browser_screenshot"),
        limits="One fresh guarded page per call on the job's allowed hosts; steps, screenshots "
        "and network need operator grants for MCP; no JavaScript evaluation",
        instead_of="driving a separate browser tool outside the job's network policy",
        docs="docs/mcp.md#browser-tools",
    ),
    Capability(
        task="Use any AI model (optional)",
        use='`rastro.model("ollama:...")`, an OpenAI-compatible endpoint, a command, or a '
        "`ModelPort` object; jobs take `[model]`",
        python=_names(
            "model ModelPort ModelRequest ModelError OllamaProvider OpenAICompatibleProvider "
            "CommandProvider JobModelProvider InferencePolicy"
        ),
        limits="Models propose selectors or judgements, never records; call and cost budgets",
        instead_of="calling an LLM to extract values from HTML",
        docs="docs/ai.md",
    ),
    Capability(
        task="Share and install recipes through a registry",
        use="`rastro recipe export`/`publish` to a Git directory; `search` and `install` "
        "from a directory or an HTTPS URL",
        cli=(
            "rastro recipe export",
            "rastro recipe import",
            "rastro recipe publish",
            "rastro recipe search",
            "rastro recipe install",
        ),
        limits="Installs stay candidates; bundles cannot carry code or grants",
        instead_of="copying selectors between projects by hand",
        docs="docs/hub.md",
    ),
    Capability(
        task="Start from a recipe someone already shared for a site",
        use="`rastro hub search URL` offers recipes for the URL's domain from the public hub "
        "or any hub URL; `rastro hub install` stores one as a candidate; `rastro hub publish` "
        "writes your recipe, a synthetic sample and field descriptions into a hub checkout",
        python=_names("hub_candidates HubCandidate"),
        cli=("rastro hub search", "rastro hub install", "rastro hub publish"),
        limits="Offers are candidates until your local gates pass; publish writes locally, "
        "sharing is a pull request that only touches `hub/`",
        instead_of="writing a recipe for a site someone already scraped",
        docs="docs/hub.md",
    ),
    Capability(
        task="Test recipes offline against synthetic page changes",
        use="Mock Lab: `build_fixture`, `verify_fixture`, `load_fixture`",
        python=_names(
            "build_fixture verify_fixture load_fixture FixtureBundle SyntheticLabel "
            "MockLabError SanitizationError"
        ),
        instead_of="committing captured third-party pages as test fixtures",
        docs="docs/mock-lab.md",
    ),
)
