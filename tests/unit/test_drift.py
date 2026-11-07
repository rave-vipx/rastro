"""Deterministic, explainable drift classification (DRIFT-001-006)."""

from datetime import UTC, datetime

from rastro.domain.drift import FieldStructure, StructuralSnapshot, assess_drift
from rastro.domain.models import Outcome


def snapshot(**changes):
    data = dict(
        source_url="https://example.org/events",
        run_id="run",
        observed_at=datetime.now(UTC),
        schema_fingerprint="a" * 64,
        status=Outcome.SUCCESS,
        fields={
            "starts_at": FieldStructure(
                selector_match_count=2,
                subtree_fingerprint="a" * 64,
                field_coverage=1,
                validation_failure_rate=0,
                fallback_used=False,
            )
        },
    )
    data.update(changes)
    return StructuralSnapshot(**data)


def test_drift_score_is_deterministic_and_explained():
    baseline = snapshot()
    current = snapshot(
        fields={
            "starts_at": FieldStructure(
                selector_match_count=0,
                subtree_fingerprint="b" * 64,
                field_coverage=0,
                validation_failure_rate=0,
                fallback_used=False,
            )
        },
        status=Outcome.INSUFFICIENT_EVIDENCE,
    )
    first = assess_drift(baseline, current)
    assert first == assess_drift(baseline, current)
    assert first.score == 0.75
    assert first.risk == "high"
    assert first.verdict == "structural_drift"
    assert {s.field for s in first.signals} == {"starts_at"}
    assert all(s.explanation and s.dataset_effect for s in first.signals)
    assert first.thresholds.high == 0.5


def test_extraction_failure_is_not_reported_as_drift():
    baseline = snapshot()
    current = snapshot(
        status=Outcome.INVALID_DATA,
        fields={
            "starts_at": FieldStructure(
                selector_match_count=2,
                subtree_fingerprint="a" * 64,
                field_coverage=0,
                validation_failure_rate=1,
                fallback_used=False,
            )
        },
    )
    assert assess_drift(baseline, current).verdict == "extraction_failure"
    unavailable = snapshot(status=Outcome.TRANSIENT_FAILURE, fields={})
    assert assess_drift(baseline, unavailable).verdict == "extraction_failure"
    assert assess_drift(baseline, unavailable).score == 0


def test_first_observation_and_unknown_measurements_do_not_invent_drift():
    assert assess_drift(None, snapshot()).verdict == "none"
    unknown = snapshot(
        fields={"starts_at": FieldStructure(field_coverage=1, validation_failure_rate=0)}
    )
    assert assess_drift(snapshot(), unknown).score == 0


def test_fallback_warning_on_success_and_recovery_do_not_reverse_the_signal():
    baseline = snapshot()
    fallback = snapshot(
        fields={
            "starts_at": baseline.fields["starts_at"].model_copy(update={"fallback_used": True})
        }
    )
    assessment = assess_drift(baseline, fallback)
    assert assessment.score == 0.15
    assert assessment.verdict == "structural_drift"
    assert "Validated" in assessment.signals[0].dataset_effect
    assert assess_drift(fallback, baseline).score == 0


def test_context_and_failed_baselines_are_not_comparable():
    baseline = snapshot()
    changed = snapshot(
        fields={
            "starts_at": FieldStructure(
                selector_match_count=0, field_coverage=0, validation_failure_rate=0
            )
        }
    )
    for old in [
        baseline.model_copy(update={"locale": "es"}),
        baseline.model_copy(update={"status": Outcome.INVALID_DATA}),
    ]:
        assert assess_drift(old, changed).signals == ()


def test_score_uses_worst_field_and_stable_field_order():
    old = snapshot()
    new = snapshot(
        fields={"starts_at": old.fields["starts_at"].model_copy(update={"selector_match_count": 1})}
    )
    assert assess_drift(old, new).score == 0.175
    fields = {"z": old.fields["starts_at"], "a": old.fields["starts_at"]}
    other = {name: new.fields["starts_at"] for name in reversed(fields)}
    result = assess_drift(snapshot(fields=fields), snapshot(fields=other))
    assert result.score == 0.175
    assert [s.field for s in result.signals] == ["a", "z"]
