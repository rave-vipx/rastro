"""Held-out DIFF-001 evaluation: python -m benchmarks.heldout --output report.json.

Measures source-specific configuration, outcomes under structural mutations of a
previously working integration, model calls per successful record with SQLite memory
(deterministic scripted fake model), operational visibility, and latency. The corpus
in ``heldout.json`` was authored after, and separately from, the 14-case regression
corpus; no engine code was changed for it.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import inspect
import json
import logging
import platform
import re
import statistics
import sys
import tempfile
import textwrap
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from benchmarks import baseline
from benchmarks.contracts import Mapping
from benchmarks.run import _git
from benchmarks.scoring import percentile95, ratio
from rastro import (
    ExtractionResult,
    InferencePolicy,
    ModelRequest,
    Rastro,
    Recipe,
    Source,
    SQLiteRecipeRepository,
)

ROOT = Path(__file__).resolve().parent
STRUCTURED = {"jsonld", "microdata"}
EXECUTORS = ("baseline", "baseline_adapted", "rastro_memory", "rastro_rediscover")
TOKEN = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]")


class Product(BaseModel):
    """Field names follow schema.org vocabulary, which exact-name discovery requires."""

    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    priceCurrency: Literal["USD", "EUR", "UYU"]  # schema.org property name


class LegacyProduct(BaseModel):
    """The regression corpus naming (``currency``) to measure discovery name sensitivity."""

    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: Literal["USD", "EUR", "UYU"]


class Mutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: str
    replace: tuple[tuple[str, str], ...] = Field(min_length=1)
    expected: tuple[Product, ...]
    requires_warning: bool = False

    def apply(self, html: str) -> str:
        for old, new in self.replace:
            if old not in html:
                raise ValueError(f"Mutation {self.name} does not apply")
            html = html.replace(old, new)
        return html


class HeldoutSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: Literal["jsonld", "microdata", "css"]
    url: str
    html: str
    baseline_mappings: tuple[Mapping, ...] = Field(min_length=1)
    rastro_recipe: Mapping | None = None
    expected: tuple[Product, ...] = Field(min_length=1)
    mutations: tuple[Mutation, ...]

    @property
    def source(self) -> Source:
        return Source(url=self.url)


@dataclass(frozen=True)
class Run:
    status: str
    records: tuple[Product, ...]
    inference_calls: int = 0


def load_sources() -> list[HeldoutSource]:
    return TypeAdapter(list[HeldoutSource]).validate_json((ROOT / "heldout.json").read_bytes())


def _values(record: BaseModel) -> tuple[tuple[str, str], ...]:
    dumped = record.model_dump()
    return tuple(
        sorted(
            (key, format(value.normalize(), "f") if isinstance(value, Decimal) else str(value))
            for key, value in dumped.items()
        )
    )


def classify(
    run: Run, expected: Iterable[Product], *, requires_warning: bool = False
) -> dict[str, Any]:
    """Silent error = announced ``success`` with a wrong record set or a required warning."""
    got, want = Counter(map(_values, run.records)), Counter(map(_values, expected))
    exact = got == want
    success = run.status == "success"
    return {
        "status": run.status,
        "exact_records": exact,
        "correct_records": sum((got & want).values()),
        "expected_records": sum(want.values()),
        "extra_records": sum((got - want).values()),
        "outcome": (
            "silent_error"
            if success and (not exact or requires_warning)
            else "correct_success"
            if success
            else "explicit_non_success"
        ),
    }


# --- Executors -------------------------------------------------------------------------


def recipe_for(mappings: Iterable[Mapping]) -> Recipe:
    recipes = [Recipe(**mapping.model_dump(), require_evidence=True) for mapping in mappings]
    return recipes[0].model_copy(update={"fallbacks": tuple(recipes[1:])})


def explicit_mappings(source: HeldoutSource) -> tuple[Mapping, ...]:
    return (source.rastro_recipe,) if source.rastro_recipe else source.baseline_mappings


def run_baseline(source: HeldoutSource, html: str) -> Run:
    return Run(*baseline.run(html, source.baseline_mappings, Product))


def run_baseline_adapted(source: HeldoutSource, html: str) -> Run:
    mappings = (
        baseline.structured_mappings(html, tuple(Product.model_fields))
        if source.kind in STRUCTURED
        else ()
    )
    return Run(*baseline.run(html, mappings or source.baseline_mappings, Product))


def _run(result: ExtractionResult[Any]) -> Run:
    return Run(
        result.status.value,
        tuple(record.data for record in result.records),
        result.metrics.inference_calls,
    )


async def rastro_extract(
    source: HeldoutSource,
    html: str,
    *,
    database: Path | None = None,
    explicit: bool = False,
    schema: type[BaseModel] = Product,
) -> ExtractionResult[Any]:
    """The whole Rastro integration: discovery when possible, otherwise the user's recipe."""
    engine = Rastro(
        discover_recipes=True,
        repository=SQLiteRecipeRepository(database) if database else None,
    )
    recipe = recipe_for(explicit_mappings(source)) if explicit else None
    return await engine.extract_html_async(html, source=source.source, schema=schema, recipe=recipe)


def needs_recipe(source: HeldoutSource) -> bool:
    return source.kind not in STRUCTURED


def logical_lines(*objects: Callable[..., Any]) -> int:
    """Count source lines that are not blank, comments, or docstrings."""
    total = 0
    for obj in objects:
        text = textwrap.dedent(inspect.getsource(obj))
        tree = ast.parse(text)
        skip: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and ast.get_docstring(node):
                first = node.body[0]
                skip.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
        total += sum(
            1
            for number, line in enumerate(text.splitlines(), 1)
            if line.strip() and not line.strip().startswith("#") and number not in skip
        )
    return total


def config_size(mappings: Iterable[Mapping]) -> dict[str, int]:
    """Entries are selectors/pointers/types a user writes; tokens split identifiers/punctuation."""
    values: list[str] = []
    for mapping in mappings:
        values.extend(filter(None, (mapping.record_selector, mapping.item_type)))
        values.extend(mapping.fields.values())
    return {
        "entries": len(values),
        "tokens": sum(len(TOKEN.findall(value)) for value in values),
        "characters": sum(len(value) for value in values),
    }


# --- Dimensions ------------------------------------------------------------------------


def integration_effort(sources: list[HeldoutSource], loop: asyncio.AbstractEventLoop) -> Any:
    rows: list[dict[str, Any]] = []
    for source in sources:
        manual = config_size(source.baseline_mappings)
        adapted = run_baseline_adapted(source, source.html)
        adapted_auto = source.kind in STRUCTURED and classify(adapted, source.expected)[
            "outcome"
        ] == ("correct_success")
        discovered = loop.run_until_complete(rastro_extract(source, source.html))
        rastro_auto = classify(_run(discovered), source.expected)["outcome"] == "correct_success"
        legacy = loop.run_until_complete(rastro_extract(source, source.html, schema=LegacyProduct))
        zero = {"entries": 0, "tokens": 0, "characters": 0}
        rows.append(
            {
                "source": source.name,
                "kind": source.kind,
                "baseline": manual,
                "baseline_adapted": zero if adapted_auto else manual,
                "baseline_adapted_auto_mapped": adapted_auto,
                "rastro": zero if rastro_auto else config_size(explicit_mappings(source)),
                "rastro_discovered": rastro_auto,
                "rastro_learned_recipe": discovered.learned_recipe.model_dump(mode="json")
                if discovered.learned_recipe
                else None,
                "rastro_discovery_with_currency_field_name": legacy.status.value,
            }
        )
    totals = {
        name: {
            key: sum(row[name][key] for row in rows) for key in ("entries", "tokens", "characters")
        }
        for name in ("baseline", "baseline_adapted", "rastro")
    }
    return {
        "per_source": rows,
        "totals": totals,
        "one_time_code_logical_lines": {
            "baseline_reference_executor": logical_lines(
                baseline._objects,
                baseline._pointer,
                baseline._types,
                baseline._json_items,
                baseline._json_rows,
                baseline.run,
            ),
            "baseline_structured_adapter": logical_lines(baseline.structured_mappings),
            "rastro_integration": logical_lines(rastro_extract),
        },
    }


def mutation_outcomes(sources: list[HeldoutSource], loop: asyncio.AbstractEventLoop) -> Any:
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="rastro-heldout-") as directory:
        for source in sources:
            database = Path(directory) / f"{source.name}.db"
            # Establish the previously working integrations on the original page.
            initial = loop.run_until_complete(
                rastro_extract(
                    source, source.html, database=database, explicit=needs_recipe(source)
                )
            )
            base = {
                "baseline": run_baseline(source, source.html),
                "baseline_adapted": run_baseline_adapted(source, source.html),
                "rastro_memory": _run(initial),
                "rastro_rediscover": _run(
                    loop.run_until_complete(
                        rastro_extract(source, source.html, explicit=needs_recipe(source))
                    )
                ),
            }
            for name, run in base.items():
                if classify(run, source.expected)["outcome"] != "correct_success":
                    raise ValueError(f"{name} does not work on {source.name} before mutation")
            for mutation in source.mutations:
                html = mutation.apply(source.html)
                memory = loop.run_until_complete(rastro_extract(source, html, database=database))
                runs = {
                    "baseline": run_baseline(source, html),
                    "baseline_adapted": run_baseline_adapted(source, html),
                    "rastro_memory": _run(memory),
                    "rastro_rediscover": _run(
                        loop.run_until_complete(
                            rastro_extract(source, html, explicit=needs_recipe(source))
                        )
                    ),
                }
                rows.append(
                    {
                        "source": source.name,
                        "mutation": mutation.name,
                        "kind": mutation.kind,
                        "requires_warning": mutation.requires_warning,
                        "rastro_memory_reused": memory.recipe_reused,
                        "rastro_memory_issues": sorted({issue.code for issue in memory.issues}),
                        **{
                            name: classify(
                                run, mutation.expected, requires_warning=mutation.requires_warning
                            )
                            for name, run in runs.items()
                        },
                    }
                )
    summary = {}
    for name in EXECUTORS:
        outcomes = Counter(row[name]["outcome"] for row in rows)
        correct = sum(row[name]["correct_records"] for row in rows)
        expected = sum(row[name]["expected_records"] for row in rows)
        harmful = sum(row[name]["outcome"] != "correct_success" for row in rows)
        summary[name] = {
            "mutations": len(rows),
            "correct_success": outcomes["correct_success"],
            "explicit_non_success": outcomes["explicit_non_success"],
            "silent_error": outcomes["silent_error"],
            "silent_error_rate": ratio(outcomes["silent_error"], len(rows)),
            "detection_rate_when_not_correct": ratio(outcomes["explicit_non_success"], harmful),
            "correct_records": correct,
            "expected_records": expected,
            "complete_record_recall": ratio(correct, expected),
            "extra_records": sum(row[name]["extra_records"] for row in rows),
        }
    return {"summary": summary, "mutations": rows}


class ScriptedModel:
    """Deterministic fake model: replays fixed proposals. Measures call accounting only."""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls = 0

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        output = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return output


def proposal(source: HeldoutSource) -> str:
    mapping = explicit_mappings(source)[0]
    return json.dumps(mapping.model_dump(exclude_none=True))


def model_reuse(sources: list[HeldoutSource], loop: asyncio.AbstractEventLoop, runs: int) -> Any:
    scripts: dict[str, Callable[[HeldoutSource], list[str]]] = {
        "scripted_valid_first": lambda source: [proposal(source)],
        "scripted_one_rejection": lambda source: ["not a recipe", proposal(source)],
    }
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="rastro-heldout-model-") as directory:
        for source in sources:
            for script, outputs in scripts.items():
                for memory in (True, False):
                    database = Path(directory) / f"{source.name}-{script}.db" if memory else None
                    calls = records = successes = 0
                    for _ in range(runs):
                        # A new engine per run models a process restart sharing one database.
                        engine = Rastro(
                            model=ScriptedModel(outputs(source)),
                            repository=SQLiteRecipeRepository(database) if database else None,
                        )
                        result = loop.run_until_complete(
                            engine.extract_html_async(
                                source.html, source=source.source, schema=Product
                            )
                        )
                        ok = classify(_run(result), source.expected)["outcome"]
                        calls += result.metrics.inference_calls
                        if ok == "correct_success":
                            successes += 1
                            records += len(result.records)
                    rows.append(
                        {
                            "source": source.name,
                            "kind": source.kind,
                            "model": script,
                            "sqlite_memory": memory,
                            "runs": runs,
                            "correct_runs": successes,
                            "inference_calls": calls,
                            "successful_records": records,
                            "calls_per_successful_record": ratio(calls, records),
                        }
                    )
    summary = {}
    for script in scripts:
        for memory in (True, False):
            group = [r for r in rows if r["model"] == script and r["sqlite_memory"] is memory]
            calls = sum(r["inference_calls"] for r in group)
            records = sum(r["successful_records"] for r in group)
            summary[f"{script}/{'memory' if memory else 'no_memory'}"] = {
                "inference_calls": calls,
                "successful_records": records,
                "calls_per_successful_record": ratio(calls, records),
            }
    return {
        "evidence_kind": "deterministic_scripted_fake_model",
        "note": "Replays the user's own selectors; it is not model quality evidence.",
        "baseline_inference_calls": 0,
        "summary": summary,
        "per_source": rows,
    }


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def visibility(sources: list[HeldoutSource], loop: asyncio.AbstractEventLoop) -> Any:
    """Checklist of what each executor returns; observed from real results, not a score."""
    source = sources[0]
    cards = next(item for item in sources if item.name == "hardware_cards")

    def issues(mutation_name: str) -> list[Any]:
        mutation = next(item for item in cards.mutations if item.name == mutation_name)
        html = mutation.apply(cards.html)
        return list(loop.run_until_complete(rastro_extract(cards, html, explicit=True)).issues)

    ambiguous, missing = issues("old_price_added"), issues("price_removed_one")
    logger = logging.getLogger("rastro")
    capture, level = _Capture(), logger.level
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    try:
        result = loop.run_until_complete(rastro_extract(source, source.html))
    finally:
        logger.removeHandler(capture)
        logger.setLevel(level)
    evidence = next(iter(result.records[0].evidence.values()))
    status, _ = baseline.run(source.html, source.baseline_mappings, Product)
    baseline_fields = {"status": status}
    events = [json.loads(message) for message in capture.messages]
    checks = {
        "explicit_outcome_status": ("status", bool(result.status)),
        "issue_category_and_record_index": (
            None,
            all(issue.code and issue.record_index is not None for issue in ambiguous + missing),
        ),
        "issue_names_field_for_ambiguous_value": (None, any(i.field for i in ambiguous)),
        # Observed defect: missing_evidence omits the known missing required field name.
        "issue_names_field_for_missing_required_value": (None, any(i.field for i in missing)),
        "per_field_locator": (None, bool(evidence.locator)),
        "per_field_extraction_method": (None, bool(evidence.method)),
        "per_field_source_url_and_observed_at": (
            None,
            bool(evidence.source_url and evidence.observed_at),
        ),
        "recipe_version": (None, result.recipe_version is not None),
        "recipe_reuse_flag_and_attempt_origins": (None, bool(result.recipe_attempts)),
        "learned_recipe_inspectable": (None, result.learned_recipe is not None),
        "job_id_and_timestamps": (None, bool(result.job_id and result.completed_at)),
        "schema_fingerprint": (None, bool(result.schema_fingerprint)),
        "candidate_accepted_rejected_counts": (None, result.metrics.candidates >= 1),
        "stage_timings": (None, result.metrics.total_seconds > 0),
        "inference_calls_and_seconds": (
            None,
            {"inference_calls", "inference_seconds"} <= type(result.metrics).model_fields.keys(),
        ),
        "structured_completion_log_event": (
            None,
            any("job_id" in event for event in events),
        ),
    }
    return {
        "source": source.name,
        "issue_source": "hardware_cards/old_price_added and price_removed_one",
        "missing_value_issues": [issue.model_dump() for issue in missing],
        "rastro_log_events": sorted({event.get("event", "") for event in events}),
        "checklist": [
            {
                "item": item,
                "baseline": field in baseline_fields if field else False,
                "rastro": bool(observed),
            }
            for item, (field, observed) in checks.items()
        ],
        "note": "Baseline items are what the reference returns (status, records); adding "
        "the rest is unmeasured engineering effort, not impossible.",
    }


def timed_runners(
    source: HeldoutSource,
    recipe: Recipe,
    repository: SQLiteRecipeRepository,
    loop: asyncio.AbstractEventLoop,
) -> dict[str, Callable[[], Run]]:
    def explicit() -> Run:
        return _run(
            Rastro().extract_html(source.html, source=source.source, schema=Product, recipe=recipe)
        )

    def rediscover() -> Run:
        coroutine = rastro_extract(source, source.html, explicit=needs_recipe(source))
        return _run(loop.run_until_complete(coroutine))

    def memory() -> Run:
        engine = Rastro(repository=repository)
        return _run(engine.extract_html(source.html, source=source.source, schema=Product))

    return {
        "baseline": lambda: run_baseline(source, source.html),
        "baseline_adapted": lambda: run_baseline_adapted(source, source.html),
        "rastro_explicit": explicit,
        "rastro_rediscover": rediscover,
        "rastro_memory_reuse": memory,
    }


def latency(
    sources: list[HeldoutSource],
    loop: asyncio.AbstractEventLoop,
    repetitions: int,
    warmup: int,
) -> Any:
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="rastro-heldout-latency-") as directory:
        for source in sources:
            database = Path(directory) / f"{source.name}.db"
            loop.run_until_complete(
                rastro_extract(
                    source, source.html, database=database, explicit=needs_recipe(source)
                )
            )
            repository = SQLiteRecipeRepository(database)
            recipe = recipe_for(explicit_mappings(source))
            runners = timed_runners(source, recipe, repository, loop)
            samples: dict[str, list[float]] = {name: [] for name in runners}
            for iteration in range(warmup + repetitions):
                order = list(runners) if iteration % 2 == 0 else list(reversed(runners))
                for name in order:
                    start = perf_counter_ns()
                    run = runners[name]()
                    elapsed = (perf_counter_ns() - start) / 1_000_000
                    if classify(run, source.expected)["outcome"] != "correct_success":
                        raise ValueError(f"{name} failed on {source.name} during timing")
                    if iteration >= warmup:
                        samples[name].append(elapsed)
            rows.append(
                {
                    "source": source.name,
                    "latency_ms": {
                        name: {
                            "median": statistics.median(values),
                            "p95": percentile95(values),
                            "samples": values,
                        }
                        for name, values in samples.items()
                    },
                }
            )
    return rows


def run(
    repetitions: int = 30,
    warmup: int = 3,
    model_runs: int = 10,
    loop_factory: Callable[[], asyncio.AbstractEventLoop] = asyncio.new_event_loop,
) -> dict[str, Any]:
    if not 1 <= repetitions <= 1000 or not 0 <= warmup <= 100 or not 1 <= model_runs <= 100:
        raise ValueError("Use 1-1000 repetitions, 0-100 warmups and 1-100 model runs")
    sources = load_sources()
    logger = logging.getLogger("rastro")
    disabled = logger.disabled
    loop = loop_factory()
    try:
        visible = visibility(sources, loop)
        logger.disabled = True
        effort = integration_effort(sources, loop)
        mutations = mutation_outcomes(sources, loop)
        models = model_reuse(sources, loop, model_runs)
        timings = latency(sources, loop, repetitions, warmup)
    finally:
        logger.disabled = disabled
        loop.close()
    return {
        "format_version": 1,
        "scope": "held-out synthetic offline corpus; supplied HTML; no network or live model",
        "created_at": datetime.now(UTC).isoformat(),
        "repetitions": repetitions,
        "warmup": warmup,
        "model_runs": model_runs,
        "environment": {
            "python": sys.version.split()[0],
            "os": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "packages": {
                name: version(name) for name in ("rastro-engine", "scrapling", "pydantic", "lxml")
            },
        },
        "git_revision": _git("rev-parse", "HEAD"),
        "git_dirty": None if (status := _git("status", "--porcelain")) is None else bool(status),
        "source_hashes": {
            path.relative_to(ROOT.parent).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(
                [
                    *ROOT.glob("*.py"),
                    ROOT / "heldout.json",
                    ROOT.parent / "uv.lock",
                    *(ROOT.parent / "src" / "rastro").rglob("*.py"),
                ]
            )
        },
        "corpus": {
            "sources": len(sources),
            "mutations": sum(len(source.mutations) for source in sources),
            "kinds": dict(Counter(source.kind for source in sources)),
        },
        "integration_effort": effort,
        "mutations": mutations,
        "model_reuse": models,
        "visibility": visible,
        "latency": timings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--model-runs", type=int, default=10)
    args = parser.parse_args()
    report = run(args.repetitions, args.warmup, args.model_runs)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    summary = {
        "effort_totals": report["integration_effort"]["totals"],
        "one_time_code": report["integration_effort"]["one_time_code_logical_lines"],
        "mutations": report["mutations"]["summary"],
        "model_reuse": report["model_reuse"]["summary"],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
