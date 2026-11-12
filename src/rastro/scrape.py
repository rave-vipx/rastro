"""One-call scraping into validated Pydantic models (INT-001, INT-002, REC-001, REPAIR-001-005).

``scrape`` builds a recipe from ``rastro.css`` markers (or takes ``recipe=``),
loads the target, extracts through the standard engine and, with ``memory=``,
remembers the recipe and repairs it offline when the page structure changes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from rastro.application.engine import Rastro
from rastro.application.field_aliases import discovery_aliases
from rastro.application.success_samples import collect_successes
from rastro.domain.memory import RecipeContext, RecipeKey
from rastro.domain.model_selectors import recipe_from_model
from rastro.domain.models import (
    ConfigurationError,
    ExtractionResult,
    Limits,
    Outcome,
    Recipe,
    Representation,
)
from rastro.domain.repair import recipe_digest
from rastro.scrape_memory import ScrapeMemory
from rastro.scrape_target import Page, Prefetched, load_page

type RepairMode = Literal["off", "propose", "auto"]
_REPAIR_MODES = ("off", "propose", "auto")
_STRUCTURAL = {Outcome.MISSING_DATA, Outcome.INVALID_DATA, Outcome.INSUFFICIENT_EVIDENCE}
_NO_SELECTORS = (
    "No selectors and no structured data matched: declare rastro.css(...) selectors, "
    "pass fields=, or use rastro.suggest"
)


def scrape[T: BaseModel](
    target: str | Path,
    schema: type[T],
    *,
    records: str | None = None,
    fields: Mapping[str, str] | None = None,
    recipe: Recipe | None = None,
    url: str | None = None,
    memory: str | Path | None = None,
    repair: RepairMode = "propose",
    locale: str = "en-US",
    limits: Limits | None = None,
) -> ExtractionResult[T]:
    """Extract ``schema`` records from HTML text, a file or a URL. See ``scrape_async``."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ConfigurationError(
            "rastro.scrape cannot run inside a running event loop; "
            "use await rastro.scrape_async(...)"
        )
    # The HTTP transport needs add_reader, so use the portable selector loop.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(
            scrape_async(
                target,
                schema,
                records=records,
                fields=fields,
                recipe=recipe,
                url=url,
                memory=memory,
                repair=repair,
                locale=locale,
                limits=limits,
            )
        )


async def scrape_async[T: BaseModel](
    target: str | Path,
    schema: type[T],
    *,
    records: str | None = None,
    fields: Mapping[str, str] | None = None,
    recipe: Recipe | None = None,
    url: str | None = None,
    memory: str | Path | None = None,
    repair: RepairMode = "propose",
    locale: str = "en-US",
    limits: Limits | None = None,
) -> ExtractionResult[T]:
    """Extract ``schema`` records; ``result.items`` holds the validated instances.

    ``url`` sets the page URL used for relative links and provenance (default
    ``https://localhost/`` for text and files). With ``memory``, a remembered
    recipe that stops matching is repaired offline: ``"propose"`` stores a gated
    candidate, ``"auto"`` also promotes it when every gate passes, ``"off"`` does neither.
    """
    if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        raise ConfigurationError("schema must be a Pydantic model class")
    if repair not in _REPAIR_MODES:
        raise ConfigurationError("repair must be 'off', 'propose' or 'auto'")
    if recipe is not None and (records is not None or fields):
        raise ConfigurationError("recipe= replaces model selectors; omit records= and fields=")
    limits = limits or Limits()
    seed = recipe or recipe_from_model(schema, records=records, fields=fields)
    representation: Representation = "html" if seed is None else seed.representation
    page = await load_page(
        target, url=url, locale=locale, limits=limits, representation=representation
    )
    if page.document is None:  # acquisition failed: the engine reports the explicit outcome
        return await _extract(_engine(page, limits, discover=seed is None), page, schema, seed)
    if memory is None:
        seed = seed or await asyncio.to_thread(_discover, page.document, schema, limits)
        return await _extract(_engine(page, limits), page, schema, seed)
    store = ScrapeMemory(memory, limits)
    return await _remembered(store, page, schema, seed, repair, limits)


async def _remembered[T: BaseModel](
    store: ScrapeMemory,
    page: Page,
    schema: type[T],
    seed: Recipe | None,
    repair: RepairMode,
    limits: Limits,
) -> ExtractionResult[T]:
    assert page.document is not None
    context = _context(seed)
    key = RecipeKey.for_source(page.source, schema, context, representation=page.representation)
    previous = await asyncio.to_thread(store.serving, key)
    engine = _engine(page, limits, store)
    first: Recipe | None = None  # None: the engine serves the remembered recipe
    if previous is None:
        first = seed or await asyncio.to_thread(_discover, page.document, schema, limits)
    result = await _extract_and_retain(store, engine, page, schema, first, context)
    if previous is None or repair == "off" or result.records or result.status not in _STRUCTURAL:
        return result
    report = await store.repair(
        "auto" if repair == "auto" else "propose",
        key=key,
        source=page.final_source,
        document=page.document,
        schema=schema,
        previous=previous,
    )
    if report.promoted:
        result = await _extract_and_retain(store, engine, page, schema, None, context)
    return result.model_copy(update={"repair": report})


async def _extract_and_retain[T: BaseModel](
    store: ScrapeMemory,
    engine: Rastro,
    page: Page,
    schema: type[T],
    recipe: Recipe | None,
    context: RecipeContext,
) -> ExtractionResult[T]:
    with collect_successes() as samples:
        result = await _extract(engine, page, schema, recipe, context)
    sample = samples.get(result.job_id)
    if result.status != Outcome.SUCCESS or sample is None:
        return result
    issues = await asyncio.to_thread(store.retain, schema, sample)
    return result.model_copy(update={"issues": (*result.issues, *issues)}) if issues else result


async def _extract[T: BaseModel](
    engine: Rastro,
    page: Page,
    schema: type[T],
    recipe: Recipe | None,
    context: RecipeContext | None = None,
) -> ExtractionResult[T]:
    if page.fetched is not None:
        return await engine.extract(
            source=page.source,
            schema=schema,
            recipe=recipe,
            context=context,
            representation=page.representation,
        )
    assert page.document is not None
    json = page.representation == "json"
    extract = engine.extract_json_async if json else engine.extract_html_async
    return await extract(
        page.document, source=page.source, schema=schema, recipe=recipe, context=context
    )


def _engine(
    page: Page, limits: Limits, store: ScrapeMemory | None = None, *, discover: bool = False
) -> Rastro:
    fetched = page.fetched
    return Rastro(
        limits=limits,
        repository=None if store is None else store.repository,
        acquisition=None if fetched is None else Prefetched(fetched),
        policy=page.policy,
        discover_recipes=discover,
    )


def _context(seed: Recipe | None) -> RecipeContext:
    """Memory is scoped to the declared selectors: editing them starts a fresh history."""
    template = "scrape-discovered" if seed is None else "scrape-" + recipe_digest(seed)[:40]
    return RecipeContext(template=template)


def _discover(document: str, schema: type[BaseModel], limits: Limits) -> Recipe:
    """Exact-name JSON-LD and microdata discovery; never guesses synonyms."""
    from rastro.adapters.discovery import ScraplingDiscovery

    found = ScraplingDiscovery().discover(
        document,
        tuple(schema.model_fields),
        limits,
        max_candidates=4,
        aliases=discovery_aliases(schema.model_fields),
    )
    if not found:
        raise ConfigurationError(_NO_SELECTORS)
    first, *rest = found
    return Recipe.model_validate(
        {**first.model_dump(), "fallbacks": [item.model_dump() for item in rest]}
    )
