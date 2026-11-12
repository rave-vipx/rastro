"""Recipe lifecycle CLI: repair, re-evaluated promotion, rollback and comparison."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rastro.application.recipe_ci import evaluate_candidate
from rastro.application.recipe_repair import repair_candidates
from rastro.application.repair_candidates import RepairCandidateGenerator
from rastro.cli_hub import add_commands as add_hub_commands
from rastro.cli_hub import hub_command
from rastro.cli_prune import add_commands as add_prune_commands
from rastro.cli_prune import prune_command
from rastro.cli_repair_context import RepairContext, safe_path
from rastro.domain.lifecycle import RecipeState
from rastro.domain.models import ConfigurationError, Limits
from rastro.domain.recipe_compare import CandidateEvaluation, RecipeComparison, RepairPolicy
from rastro.domain.repair import RepairCandidate


def add_commands(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subcommands.add_parser("recipe", help="Evaluate, promote or roll back recipes")
    commands = parser.add_subparsers(dest="recipe_command", required=True)
    add_hub_commands(commands)
    add_prune_commands(commands)
    for name in ("repair", "promote", "rollback", "compare"):
        command = commands.add_parser(name)
        command.add_argument("file", type=Path)
        command.add_argument(
            "--source",
            type=int,
            default=None if name == "repair" else 1,
            help="One-based source index; repair defaults to all sources",
        )
        command.add_argument("--allow-absolute-paths", action="store_true")
        if name in ("promote", "rollback"):
            command.add_argument("--version", type=int, required=True)
            command.add_argument("--reason", default="Operator requested lifecycle action")
        if name == "compare":
            command.add_argument("--from-version", type=int, required=True)
            command.add_argument("--to-version", type=int, required=True)
        if name == "repair":
            command.add_argument("--report", help="New report path inside the job directory")


def recipe_command(args: argparse.Namespace) -> int:
    if args.recipe_command in {"export", "import"}:
        return hub_command(args)
    if args.recipe_command == "prune":
        return prune_command(args)
    context = RepairContext.load(args.file, args.allow_absolute_paths)
    if args.recipe_command == "compare":
        _, key, _ = context.source(args.source)
        old, new = (
            context.repository.get(key, version=v) for v in (args.from_version, args.to_version)
        )
        if old is None or new is None:
            raise ConfigurationError("Comparison recipe version does not exist")
        print(RecipeComparison.between(old.recipe, new.recipe).model_dump_json(indent=2))
        return 0
    if args.recipe_command == "rollback":
        _, key, _ = context.source(args.source)
        stored = context.repository.rollback(
            key, args.version, reason=args.reason, at=datetime.now(UTC)
        )
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "state": stored.metadata.state,
                    "version": stored.recipe.version,
                }
            )
        )
        return 0
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(_evaluate_command(context, args))


async def _evaluate_command(context: RepairContext, args: argparse.Namespace) -> int:
    run_id = str(uuid4())
    # Reserve output before lifecycle writes; exclusive creation refuses existing files/symlinks.
    report_path = (
        safe_path(args.report or f"repair-report-{run_id}.json", context.base, context.allow)
        if args.recipe_command == "repair"
        else None
    )
    descriptor = (
        os.open(
            report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        if report_path
        else None
    )
    try:
        sources = []
        indexes = (
            [args.source] if args.source is not None else range(1, len(context.job.sources) + 1)
        )
        for index in indexes:
            sources.append(await _evaluate_source(context, args, index))
        payload = {"schema_version": 1, "run_id": run_id, "sources": sources}
        encoded = json.dumps(payload, indent=2)
        if descriptor is not None:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                descriptor = None
                output.write(encoded + "\n")
        print(encoded)
        decisions = [c["evaluation"]["decision"] for s in sources for c in s["comparisons"]]
        source_decisions = [
            [c["evaluation"]["decision"] for c in source["comparisons"]] for source in sources
        ]
        return (
            0
            if all("promote" in items for items in source_decisions)
            else (3 if "quarantine" in decisions else 1)
        )
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if report_path is not None:
            report_path.unlink(missing_ok=True)
        raise


async def _evaluate_source(
    context: RepairContext, args: argparse.Namespace, index: int
) -> dict[str, Any]:
    from rastro.adapters.discovery import ScraplingDiscovery
    from rastro.adapters.scrapling import ScraplingParser
    from rastro.adapters.scrapling_relocation import ScraplingRelocation
    from rastro.adapters.structured import JsonDiscovery, JsonParser

    if args.recipe_command == "promote":
        source, key = context.locate(index)
        stored = context.repository.get(key, version=args.version)
        if stored is None or stored.metadata.state != RecipeState.CANDIDATE:
            raise ConfigurationError("Only a stored candidate may be promoted")
        serving = context.repository.get(key)
        # A fresh store (an imported bundle) has nothing serving: compare with itself.
        previous = serving.validated_recipe if serving else stored.validated_recipe
        adopting = serving is None
    else:
        source, key, previous = context.source(index)
        stored, adopting = None, False
    document = await context.document(index, source)
    snapshot, fixtures = context.fixtures(source, previous)
    limits = context.job.limits or Limits()
    parser = JsonParser() if previous.representation == "json" else ScraplingParser()
    policy = context.job.repair or RepairPolicy()
    history = context.history()  # before any dataset write; repair never applies snapshots
    now = datetime.now(UTC)

    def evaluate(candidate: RepairCandidate) -> CandidateEvaluation:
        return evaluate_candidate(
            candidate,
            document,
            source=source,
            schema=context.schema,
            parser=parser,
            limits=limits,
            observed_at=now,
            fixtures=fixtures,
            rules=context.job.consistency,
            history=history,
            policy=policy,
            allow_fallbacks=adopting,
        )

    if stored is not None:  # promote
        # Explicit approval enables promotion, but cannot bypass the CI gates.
        update: dict[str, object] = {"auto_promote": True}
        if adopting:  # No history yet; the import already replayed the bundle's records.
            update["min_historical_fixtures"] = 0
        policy = policy.model_copy(update=update)
        candidate = RepairCandidate(
            recipe=stored.recipe, origin="discovery", rationale="Operator review"
        )
        evaluation = evaluate(candidate)
        if evaluation.decision == "promote":
            context.repository.transition(
                key, args.version, RecipeState.ACTIVE, reason=args.reason, at=now
            )
        comparisons: tuple[RecipeComparison, ...] = (
            RecipeComparison.between(previous, stored.recipe, evaluation),
        )
        return {
            "source_index": index,
            "comparisons": [c.model_dump(mode="json") for c in comparisons],
        }
    model = None
    if context.job.model is not None:
        from rastro.application.job_loader import _build_model_provider

        model = _build_model_provider(context.job.model)
    generator = RepairCandidateGenerator(
        discovery=JsonDiscovery() if previous.representation == "json" else ScraplingDiscovery(),
        relocation=ScraplingRelocation(),
        model=model,
        policy=context.job.inference_policy,
        aliases=context.job.discovery_aliases or None,
    )
    candidates = await generator.generate(
        document,
        source=source,
        schema=context.schema,
        recipe=previous,
        limits=limits,
        snapshot=snapshot,
        model_enabled=context.job.model is not None,
    )
    comparisons = repair_candidates(
        candidates.candidates,
        previous=previous,
        key=key,
        lifecycle=context.repository,
        evaluate=evaluate,
        at=now,
    )
    return {
        "source_index": index,
        "outcome": candidates.outcome,
        "inference_calls": candidates.inference_calls,
        "issues": [issue.model_dump(mode="json") for issue in candidates.issues],
        "comparisons": [c.model_dump(mode="json") for c in comparisons],
    }
