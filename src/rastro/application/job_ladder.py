"""Run one job source through the acquisition ladder and remember where it worked."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from rastro.application import run_report_facts as facts
from rastro.application.acquisition_ladder import StepOutcome, climb, gate
from rastro.application.active_recipes import source_key
from rastro.application.ladder_levels import Rung, floor_level, ladder_rungs, start_plan
from rastro.application.solver_plugins import any_solver_installed, solver_port
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.job_results import JobRunResult
from rastro.domain.jobs import Job, UrlJobSource
from rastro.domain.memory import RecipeKey, RecipeStorageError, StoredRecipe
from rastro.domain.models import ConfigurationError, Outcome
from rastro.domain.results import ExtractionResult, Issue
from rastro.domain.source_notes import AcquisitionUse, LadderLevel, SourceNotes
from rastro.ports.acquisition import AcquisitionPort

__all__ = ["JobLadder", "StartLevels", "step_outcome"]

type RunSource = Callable[[Job, AcquisitionPort | None], JobRunResult[Any]]


class StartLevels(Protocol):
    """The part of recipe memory that keeps the level each recipe last worked at."""

    def get(self, key: RecipeKey, *, version: int | None = None) -> StoredRecipe | None: ...

    def save_start_level(
        self, key: RecipeKey, version: int, level: LadderLevel, *, runs: int
    ) -> None: ...


def step_outcome(result: ExtractionResult[Any]) -> StepOutcome:
    """A refusal climbs (except robots.txt, which a stronger level must not get around)."""
    notes = result.notes
    robots = any(issue.code == "robots_denied" for issue in result.issues) or (
        notes is not None and notes.blocked is not None and notes.blocked.kind == "robots"
    )
    refused = result.status == Outcome.ACCESS_RESTRICTION or (notes and notes.blocked)
    if refused and not robots:
        return "blocked"
    return "ok" if result.status in (*facts.OK, Outcome.PARTIAL_SUCCESS) else "failed"


def _source_policy(policy: AcquisitionPolicy, item: UrlJobSource) -> AcquisitionPolicy:
    if "render" in item.model_fields_set:
        return policy.model_copy(update={"render": item.render})
    return policy


def _rendered(job: Job, policy: AcquisitionPolicy) -> Job:
    """``job`` fetching every URL source with ``policy``."""
    sources = tuple(
        s.model_copy(update={"render": policy.render}) if isinstance(s, UrlJobSource) else s
        for s in job.sources
    )
    return job.model_copy(update={"acquisition": policy, "sources": sources})


class JobLadder:
    """One load's ladder: ports are built once per level and shared by its sources.

    ``base`` is the load's own port (the floor of every source); ``build`` makes the port
    for a job at another level; ``scope`` re-enters the operator's trust for the gate and
    for building ports; ``memory`` is the job's recipe memory, if it has one.
    """

    def __init__(
        self,
        job: Job,
        base: AcquisitionPort | None,
        *,
        build: Callable[[Job], AcquisitionPort],
        scope: Callable[[], AbstractContextManager[object]],
        rotate: bool,
        memory: StartLevels | None,
    ) -> None:
        self.job, self.base, self.build, self.scope = job, base, build, scope
        self.rotate, self.memory = rotate, memory
        self._ports: dict[str, AcquisitionPort] = {}
        self._solvers = any_solver_installed()

    def run(self, sub_job: Job, run: RunSource) -> JobRunResult[Any]:
        """Run ``sub_job`` (one source), climbing while the site blocks it."""
        item = sub_job.sources[0]
        if self.base is None or sub_job.acquisition is None or not isinstance(item, UrlJobSource):
            return run(sub_job, self.base)
        policy = _source_policy(sub_job.acquisition, item)
        floor = floor_level(policy)
        key = source_key(sub_job, item)
        stored = self._stored(key)
        level = stored.metadata.start_level if stored else None
        runs = stored.metadata.level_runs if stored else 0
        start, probe = start_plan(floor, level, runs)
        rungs = ladder_rungs(
            policy, html=item.representation == "html", rotate=self.rotate, solvers=self._solvers
        )
        tried: list[Rung] = []

        def attempt(rung: Rung) -> tuple[JobRunResult[Any], StepOutcome]:
            if rung.policy == policy and rung.level != "solver_plugin":
                report = run(sub_job, self.base)
            else:
                port = self._port(rung)
                report = run(_rendered(sub_job, rung.policy), port)
            tried.append(rung)  # only levels that ran: the report's render is the last one
            return report, step_outcome(report.results[0])

        def granted(rung: Rung) -> bool:
            with self.scope():
                return gate(rung)

        report, steps, working = climb(rungs, start, attempt, granted)
        render = tried[-1].policy.render if report is not None else policy.render
        if report is None:  # nothing could run: the job's own level reports the real outcome
            report = run(sub_job, self.base)
        if working is not None and (level is not None or working.level != floor):
            same = working.level == level and not probe
            self._remember(key, working.level, runs + 1 if same else 0)
        use = AcquisitionUse(render=render, ladder=steps, start_level=start)
        return _annotated(report, use, exhausted=working is None)

    def _stored(self, key: RecipeKey) -> StoredRecipe | None:
        if self.memory is None:
            return None
        try:
            return self.memory.get(key)
        except RecipeStorageError:
            return None  # an unreadable memory starts at the floor; the run reports it

    def _remember(self, key: RecipeKey, level: LadderLevel, runs: int) -> None:
        stored = self._stored(key)
        if self.memory is None or stored is None:
            return  # nothing stored for this recipe yet: the next success learns it
        try:
            self.memory.save_start_level(key, stored.recipe.version, level, runs=runs)
        except RecipeStorageError:
            return  # a lost start level only costs a climb on the next run

    def _port(self, rung: Rung) -> AcquisitionPort:
        cache = f"{rung.level}:{rung.policy.model_dump_json()}"
        if cache not in self._ports:
            with self.scope():
                if rung.level == "solver_plugin":
                    port = solver_port(rung.policy)
                    if port is None:
                        raise ConfigurationError("No solver plugin is installed")
                else:
                    port = self.build(_rendered(self.job, rung.policy))
            self._ports[cache] = port
        return self._ports[cache]


def _annotated(
    report: JobRunResult[Any], use: AcquisitionUse, *, exhausted: bool
) -> JobRunResult[Any]:
    """The source's notes carry the ladder; a source still blocked at the top is exhausted."""
    result = report.results[0]
    notes = result.notes or SourceNotes()
    known = notes.acquisition or facts.acquisition(result)
    if known is not None:
        use = known.model_copy(
            update={"render": use.render, "ladder": use.ladder, "start_level": use.start_level}
        )
    else:
        use = use.model_copy(update={"egress": result.metrics.egress})
    issues = result.issues
    if exhausted and step_outcome(result) == "blocked":
        issues = (*issues, Issue(code="ladder_exhausted"))
    result = result.model_copy(
        update={"notes": notes.model_copy(update={"acquisition": use}), "issues": issues}
    )
    return report.model_copy(update={"results": (result, *report.results[1:])})
