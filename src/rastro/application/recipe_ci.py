"""Offline candidate CI, independent of persistence and activation (REPAIR-002/004/005)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from rastro.application.consistency import History, evaluate_consistency
from rastro.application.extraction import extract_document, validate_recipe
from rastro.domain.consistency import ConsistencyReport, ConsistencyRule
from rastro.domain.models import ConfigurationError, Limits, Outcome, Source, schema_fingerprint
from rastro.domain.recipe_compare import CandidateEvaluation, RegressionCheck, RepairPolicy
from rastro.domain.repair import RepairCandidate
from rastro.mocklab.bundle import FixtureBundle
from rastro.ports.parser import DocumentParser


def evaluate_candidate(
    candidate: RepairCandidate,
    document: str,
    *,
    source: Source,
    schema: type[BaseModel],
    parser: DocumentParser,
    limits: Limits,
    observed_at: datetime,
    fixtures: Sequence[FixtureBundle] = (),
    rules: Sequence[ConsistencyRule] = (),
    history: History | None = None,
    policy: RepairPolicy | None = None,
) -> CandidateEvaluation:
    """Replay historical fixtures and generated invariants; never save candidate data.

    Historical comparisons require identical validated values and proven fields,
    allowing changed locators/version numbers. Current semantics use pre-write history.
    No declared consistency rules means the semantic gate is inapplicable, not passed.
    """
    policy = policy or RepairPolicy()
    recipe = validate_recipe(schema, candidate.recipe, candidate.recipe.representation)
    if not recipe.require_evidence or recipe.fallbacks:
        raise ConfigurationError("Recipe CI requires evidence and a single candidate")
    if len(fixtures) > 32:
        raise ConfigurationError("Recipe CI accepts at most 32 historical fixtures")
    current = extract_document(
        document, source=source, schema=schema, recipe=recipe, parser=parser, limits=limits
    )
    rows = [r.data.model_dump(mode="json") for r in current.records]
    indicators = evaluate_consistency(
        rules,
        rows,
        observed_at,
        declared_fields=tuple(schema.model_fields),
        history=history,
        rejected=current.metrics.rejected,
    )
    historical = tuple(
        _replay(candidate, fixture, schema, parser, limits, i) for i, fixture in enumerate(fixtures)
    )
    generated = (
        RegressionCheck(name="nonempty_validated_records", passed=bool(current.records)),
        RegressionCheck(
            name="required_field_provenance",
            passed=bool(current.records)
            and all(
                all(
                    name in record.evidence
                    for name, field in schema.model_fields.items()
                    if field.is_required()
                )
                for record in current.records
            ),
        ),
        RegressionCheck(name="no_rejected_current_records", passed=current.metrics.rejected == 0),
    )
    decision, reasons = _decide(
        candidate, current.status, len(rows), indicators, historical, generated, bool(rules), policy
    )
    return CandidateEvaluation(
        decision=decision,
        reasons=reasons,
        current_status=current.status,
        current_records=len(rows),
        historical_fixtures=len(historical),
        historical_passed=sum(check.passed for check in historical),
        indicators=indicators,
        regressions=(*historical, *generated),
    )


def _replay(
    candidate: RepairCandidate,
    fixture: FixtureBundle,
    schema: type[BaseModel],
    parser: DocumentParser,
    limits: Limits,
    index: int,
) -> RegressionCheck:
    manifest = fixture.manifest
    if manifest.schema_fingerprint != schema_fingerprint(schema):
        raise ConfigurationError("Historical fixture belongs to another schema")
    if manifest.representation != candidate.recipe.representation:
        raise ConfigurationError("Historical fixture has another representation")
    result = extract_document(
        fixture.labeled_body,
        source=manifest.source,
        schema=schema,
        recipe=candidate.recipe,
        parser=parser,
        limits=limits,
    )
    expected = fixture.expected
    actual = [(r.data.model_dump(mode="json"), set(r.evidence)) for r in result.records]
    wanted = [(r.data, set(r.evidence)) for r in expected.records]
    return RegressionCheck(
        name=f"historical_fixture_{index}",
        passed=(
            result.status == expected.status == Outcome.SUCCESS
            and actual == wanted
            and result.metrics.rejected == 0
        ),
    )


def _decide(
    candidate: RepairCandidate,
    status: Outcome,
    count: int,
    indicators: ConsistencyReport,
    historical: tuple[RegressionCheck, ...],
    generated: tuple[RegressionCheck, ...],
    has_rules: bool,
    policy: RepairPolicy,
) -> tuple[Literal["promote", "quarantine", "reject"], tuple[str, ...]]:
    if candidate.ambiguous:
        return "quarantine", ("ambiguous_evidence",)
    if (
        status != Outcome.SUCCESS
        or not all(c.passed for c in (*historical, *generated))
        or (has_rules and indicators.verdict == "inconsistent")
    ):
        return "reject", ("validation_or_regression_failed",)
    reasons = []
    if len(historical) < policy.min_historical_fixtures:
        reasons.append("insufficient_history")
    if count < policy.min_records:
        reasons.append("insufficient_current_records")
    for name in ("completeness", "structural_validity"):
        value = getattr(indicators, name)
        if value is None or value < getattr(policy, "min_" + name):
            reasons.append(name + "_below_threshold")
    if has_rules and (
        indicators.verdict != "consistent"
        or indicators.semantic_confidence is None
        or indicators.semantic_confidence < policy.min_semantic_confidence
    ):
        reasons.append("semantic_threshold_not_met")
    if not policy.auto_promote:
        reasons.append("automatic_promotion_disabled")
    return ("quarantine", tuple(reasons)) if reasons else ("promote", ("all_gates_passed",))
