"""Schema validation and evidence shared by supplied HTML and asynchronous acquisition."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

import structlog
from pydantic import BaseModel, ValidationError

from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.models import (
    ConfigurationError,
    Evidence,
    ExtractionRecord,
    ExtractionResult,
    Issue,
    Limits,
    Metrics,
    Outcome,
    Recipe,
    ResourceLimitError,
    Source,
    schema_fingerprint,
)
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.parser import DocumentParser

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)


class Rastro:
    """Extract supplied HTML, or explicitly configure permission-aware HTTP acquisition."""

    def __init__(
        self,
        *,
        parser: DocumentParser | None = None,
        limits: Limits | None = None,
        acquisition: AcquisitionPort | None = None,
        policy: AcquisitionPolicy | None = None,
    ) -> None:
        if parser is None:
            from rastro.adapters.scrapling import ScraplingParser

            parser = ScraplingParser()
        self._parser = parser
        self._limits = limits or Limits()
        self._acquisition = acquisition
        self._policy = policy
        if acquisition is not None and policy is None:
            raise ConfigurationError("An acquisition port requires a batch policy")

    def extract_html[T: BaseModel](
        self,
        html: str,
        *,
        source: Source,
        schema: type[T],
        recipe: Recipe,
    ) -> ExtractionResult[T]:
        """Validate each candidate, preserving valid records and reporting every rejection.

        Recipe keys use model field names. Pydantic's coercion and custom validators
        follow the supplied model configuration. Source values are never logged.
        """
        started = perf_counter()
        started_at = datetime.now(UTC)
        job_id = str(uuid4())
        recipe = self._validate_recipe(schema, recipe)
        size = len(html.encode("utf-8"))
        if size > self._limits.max_html_bytes:
            raise ResourceLimitError("HTML input exceeds max_html_bytes")
        extraction_start = perf_counter()
        parsed = self._parser.parse(html, recipe, self._limits)
        if len(parsed.candidates) > self._limits.max_records:
            raise ResourceLimitError("Parser returned more than max_records")
        extraction_seconds = perf_counter() - extraction_start
        validation_start = perf_counter()
        records: list[ExtractionRecord[T]] = []
        issues = list(parsed.issues)
        rejected = 0
        for index, candidate in enumerate(parsed.candidates):
            if candidate.issues:
                issues.extend(candidate.issues)
                rejected += 1
                continue
            if not candidate.values:
                issues.append(Issue(code="missing_fields", record_index=index))
                rejected += 1
                continue
            if any(name not in candidate.locators for name in candidate.values):
                issues.append(Issue(code="missing_evidence", record_index=index))
                rejected += 1
                continue
            try:
                data = schema.model_validate(candidate.values)
            except ValidationError as error:
                for detail in error.errors(
                    include_url=False, include_context=False, include_input=False
                ):
                    # Validator messages can contain secrets; only expose a stable category.
                    field = str(detail["loc"][0]) if detail["loc"] else None
                    issues.append(Issue(code="validation_error", record_index=index, field=field))
                rejected += 1
                continue
            evidence = {
                name: Evidence(
                    source_url=source.evidence_url,
                    observed_at=started_at,
                    method=recipe.strategy,
                    locator=locator,
                    recipe_version=recipe.version,
                )
                for name, locator in candidate.locators.items()
                if name in candidate.values
            }
            records.append(ExtractionRecord(data=data, evidence=evidence))
        validation_seconds = perf_counter() - validation_start
        if records:
            status = Outcome.PARTIAL_SUCCESS if issues else Outcome.SUCCESS
        elif any(issue.code in {"ambiguous_match", "missing_evidence"} for issue in issues):
            status = Outcome.INSUFFICIENT_EVIDENCE
        elif any(issue.code in {"validation_error", "invalid_jsonld"} for issue in issues):
            status = Outcome.INVALID_DATA
        else:
            status = Outcome.MISSING_DATA
        result = ExtractionResult[T](
            job_id=job_id,
            status=status,
            source_url=source.evidence_url,
            locale=source.locale,
            timezone=source.timezone,
            schema_fingerprint=schema_fingerprint(schema),
            started_at=started_at,
            completed_at=datetime.now(UTC),
            records=tuple(records),
            issues=tuple(issues),
            metrics=Metrics(
                input_bytes=size,
                candidates=len(parsed.candidates),
                accepted=len(records),
                fields_validated=sum(len(record.evidence) for record in records),
                rejected=rejected,
                extraction_seconds=extraction_seconds,
                validation_seconds=validation_seconds,
                total_seconds=perf_counter() - started,
            ),
        )
        _LOG.info(
            "extraction_completed",
            job_id=job_id,
            status=status.value,
            accepted=len(records),
            rejected=rejected,
            total_seconds=result.metrics.total_seconds,
        )
        return result

    @staticmethod
    def _validate_recipe(schema: type[BaseModel], recipe: Recipe) -> Recipe:
        # Revalidate mutable mappings at the trust boundary, even on a frozen model.
        recipe = Recipe.model_validate(recipe.model_dump())
        fields = schema.model_fields
        if not fields or any(name not in fields for name in recipe.fields):
            raise ConfigurationError("Recipe mappings must reference declared schema fields")
        if any(field.alias or field.validation_alias for field in fields.values()):
            raise ConfigurationError("Schema aliases are not supported in this milestone")
        return recipe

    @classmethod
    def web(cls, policy: AcquisitionPolicy, *, limits: Limits | None = None) -> Rastro:
        """Create an HTTP client. Install rastro-engine[http]; use one event loop per client."""
        try:
            from rastro.adapters.dns import SystemResolver
            from rastro.adapters.http import CurlTransport
            from rastro.application.acquisition import WebAcquirer
        except ModuleNotFoundError as error:
            if error.name not in {"curl_cffi", "protego"}:
                raise
            raise ConfigurationError("Install rastro-engine[http] for HTTP acquisition") from None
        return cls(
            acquisition=WebAcquirer(policy, transport=CurlTransport(), resolver=SystemResolver()),
            policy=policy,
            limits=limits,
        )

    async def extract[T: BaseModel](
        self,
        *,
        source: Source,
        schema: type[T],
        recipe: Recipe,
    ) -> ExtractionResult[T]:
        """Acquire one permitted source, then validate it through the offline engine."""
        if self._acquisition is None:
            raise ConfigurationError("Use Rastro.web(policy) or supply an acquisition port")
        recipe = self._validate_recipe(schema, recipe)
        started, started_at = perf_counter(), datetime.now(UTC)
        fetched = await self._acquisition.acquire(source, max_bytes=self._limits.max_html_bytes)
        final_source = Source(url=fetched.url, locale=source.locale, timezone=source.timezone)
        issue = fetched.issue_code
        result: ExtractionResult[T] | None = None
        if fetched.html is not None and fetched.status == Outcome.SUCCESS:
            try:
                result = await asyncio.to_thread(
                    self.extract_html,
                    fetched.html,
                    source=final_source,
                    schema=schema,
                    recipe=recipe,
                )
            except ResourceLimitError:
                issue = "extraction_limit"
        if result is None:
            result = ExtractionResult[T](
                job_id=str(uuid4()),
                status=(
                    Outcome.PERMANENT_FAILURE
                    if fetched.status == Outcome.SUCCESS
                    else fetched.status
                ),
                source_url=final_source.evidence_url,
                locale=source.locale,
                timezone=source.timezone,
                schema_fingerprint=schema_fingerprint(schema),
                started_at=started_at,
                completed_at=datetime.now(UTC),
                records=(),
                issues=(Issue(code=issue or "invalid_acquisition_result"),),
                metrics=Metrics(
                    input_bytes=0,
                    candidates=0,
                    accepted=0,
                    rejected=0,
                    extraction_seconds=0,
                    validation_seconds=0,
                    total_seconds=0,
                ),
            )
        metrics = result.metrics.model_copy(
            update={
                "acquisition_method": "http",
                "acquisition_seconds": fetched.acquisition_seconds,
                "network_seconds": fetched.network_seconds,
                "request_count": fetched.requests,
                "retry_count": fetched.retries,
                "redirect_count": fetched.redirects,
                "bytes_received": fetched.bytes_received,
                "total_seconds": perf_counter() - started,
            }
        )
        result = result.model_copy(
            update={
                "started_at": started_at,
                "completed_at": datetime.now(UTC),
                "metrics": metrics,
                "requested_url": source.evidence_url,
                "http_status": fetched.http_status,
                "retry_after_seconds": fetched.retry_after_seconds,
            }
        )
        _LOG.info(
            "acquisition_completed",
            job_id=result.job_id,
            status=result.status.value,
            request_count=fetched.requests,
            retry_count=fetched.retries,
            acquisition_seconds=fetched.acquisition_seconds,
        )
        return result

    async def extract_many[T: BaseModel](
        self,
        sources: Sequence[Source],
        *,
        schema: type[T],
        recipe: Recipe,
    ) -> tuple[ExtractionResult[T], ...]:
        """Preserve input order with a bounded worker pool; cancellation cancels all jobs."""
        if self._acquisition is None or self._policy is None:
            raise ConfigurationError("Batch acquisition requires Rastro.web(policy)")
        recipe = self._validate_recipe(schema, recipe)
        if len(sources) > self._policy.max_sources:
            raise ResourceLimitError("Batch exceeds max_sources")
        pending = iter(enumerate(tuple(sources)))
        results: list[ExtractionResult[T] | None] = [None] * len(sources)

        async def worker() -> None:
            for index, source in pending:
                results[index] = await self.extract(source=source, schema=schema, recipe=recipe)

        async with asyncio.TaskGroup() as group:
            for _ in range(min(len(sources), self._policy.max_concurrency)):
                group.create_task(worker())
        return tuple(result for result in results if result is not None)
