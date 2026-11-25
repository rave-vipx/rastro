"""Repair and drift reach the run report, the failure log and Studio's alerts the same way."""

from datetime import UTC, datetime

from pydantic import BaseModel

from rastro.application.run_report import report_for_run
from rastro.application.self_repair import repair_note
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import ExtractionResult, Issue, Metrics, Outcome
from rastro.domain.results import RepairProposal, SelfRepair
from rastro.studio.overview_alerts import report_alerts


class Item(BaseModel):
    name: str


def result(
    repair: SelfRepair | None = None, issues: tuple[Issue, ...] = ()
) -> ExtractionResult[Item]:
    now = datetime.now(UTC)
    return ExtractionResult[Item](
        job_id="j",
        status=Outcome.PARTIAL_SUCCESS,
        source_url="https://h.example/list?token=x",
        locale="en-US",
        timezone="UTC",
        schema_fingerprint="0" * 64,
        started_at=now,
        completed_at=now,
        records=(),
        issues=issues,
        metrics=Metrics(
            input_bytes=1,
            candidates=1,
            accepted=0,
            rejected=0,
            extraction_seconds=0,
            validation_seconds=0,
            total_seconds=0,
        ),
        repair=repair,
    )


def proposal(decision: str, *reasons: str, version: int = 2) -> RepairProposal:
    return RepairProposal(
        version=version, decision=decision, reasons=reasons, changed_fields=("name",)
    )  # type: ignore[arg-type]


def quarantined() -> SelfRepair:
    return SelfRepair(
        mode="auto",
        previous_version=1,
        candidates=(proposal("quarantine", "competing_candidates"),),
    )


def wrap(item: ExtractionResult[Item]) -> JobRunResult[Item]:
    return JobRunResult.from_results(schema_fingerprint="0" * 64, results=[item])


def test_quarantine_in_report() -> None:
    report = report_for_run("job.toml", wrap(result(quarantined())))
    note = report.sources[0].repair
    assert note is not None
    assert (note.decision, note.candidate_version) == ("quarantined", 2)


def test_promotion_refused_reason_code() -> None:
    note = repair_note(quarantined())
    assert note.promotion_refused == "competing_candidates"
    odd = SelfRepair(
        mode="auto",
        previous_version=1,
        candidates=(proposal("quarantine", "all_gates_passed", "Not A Code!"),),
    )
    assert repair_note(odd).promotion_refused is None
    rejected = SelfRepair(
        mode="auto", previous_version=1, candidates=(proposal("reject", "regression"),)
    )
    assert repair_note(rejected).decision == "rejected"


def test_alerts_view_reads_run_report() -> None:
    report = report_for_run("job.toml", wrap(result(quarantined())))
    found = report_alerts(report)
    kinds = {(a["kind"], a["code"]) for a in found}
    assert ("quarantine", "competing_candidates") in kinds
    assert all(a["source"] == "https://h.example/list" for a in found)
    assert all("token" not in str(a) for a in found)


def test_repairs_without_model_counted() -> None:
    promoted = SelfRepair(
        mode="auto",
        previous_version=1,
        promoted=True,
        promoted_version=2,
        candidates=(proposal("promote", "all_gates_passed"),),
    )
    report = report_for_run("job.toml", wrap(result(promoted)))
    assert report.totals.repairs_without_model == 1
    assert report.sources[0].repair is not None
    assert report.sources[0].repair.decision == "promoted"


def test_fields_nulled_reaches_the_report() -> None:
    issues = (Issue(code="field_nulled", record_index=0, field="price"),)
    report = report_for_run("job.toml", wrap(result(issues=issues)))
    assert report.sources[0].fields_nulled == {"price": 1}
