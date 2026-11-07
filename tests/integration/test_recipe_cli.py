"""REPAIR-002-006 / REC-006: offline lifecycle leaves the dataset intact."""

import asyncio
import json
import shutil
import socket
from pathlib import Path

import pytest
from conftest import _ORIGINAL_SOCKET

from rastro.cli import main

SAMPLE = Path(__file__).parents[2] / "examples/uy-dance-agenda/sample"


@pytest.fixture(autouse=True)
def offline_cli_loop(monkeypatch):
    from datetime import UTC, datetime

    import rastro.cli_recipe as recipe_cli

    class FrozenTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 7, 12, tzinfo=UTC)

    monkeypatch.setattr(recipe_cli, "datetime", FrozenTime)
    original_loop = asyncio.SelectorEventLoop

    def factory():
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(socket, "socket", _ORIGINAL_SOCKET)
            return original_loop()

    monkeypatch.setattr(asyncio, "SelectorEventLoop", factory)


def setup_sample(tmp_path):
    folder = tmp_path / "sample"
    shutil.copytree(SAMPLE, folder)
    job = folder / "job.toml"
    job.write_text(
        job.read_text().replace(
            "format_version = 1",
            'format_version = 1\nrecipe_memory = "recipes.sqlite"\n\n[repair]\nauto_promote = true',
        )
    )
    return folder, job


def mutate(folder):
    from rastro.mocklab.mutations.html_content import HtmlChangeClasses
    from rastro.mocklab.mutations.html_structure import HtmlWrapElement
    from rastro.mocklab.mutations.plan import MutationPlan

    path = folder / "sodre-cartelera.html"
    path.write_text(
        MutationPlan(
            seed=42,
            operators=(
                HtmlWrapElement(selector="h2.cyg--display-3", wrapper_tag="section"),
                HtmlChangeClasses(
                    selector="h2.cyg--display-3", rename={"cyg--display-3": "new-title"}
                ),
            ),
        )
        .apply(path.read_text())
        .document
    )


def test_rollback_restores_previous_version_and_next_load_uses_it(tmp_path, capsys):
    folder, job = setup_sample(tmp_path)
    assert main(["job", "load", str(job), "--drift"]) == 0
    original = (folder / "sodre-cartelera.html").read_text()
    capsys.readouterr()
    mutate(folder)
    assert main(["job", "load", str(job), "--drift"]) == 3
    capsys.readouterr()
    result = main(["recipe", "repair", str(job), "--source", "1"])
    output = capsys.readouterr()
    assert result == 0, output.err + output.out
    repair = json.loads(output.out)
    assert repair["sources"][0]["comparisons"][0]["evaluation"]["decision"] == "promote"
    assert len(list(folder.glob("repair-report-*.json"))) == 1
    assert main(["job", "load", str(job)]) == 0
    loaded = json.loads(capsys.readouterr().out)
    assert loaded["sources"][0]["status"] == "success"
    assert main(["recipe", "rollback", str(job), "--source", "1", "--version", "1"]) == 0
    capsys.readouterr()
    (folder / "sodre-cartelera.html").write_text(original)
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    assert main(["recipe", "compare", str(job), "--from-version", "1", "--to-version", "2"]) == 0
    comparison = json.loads(capsys.readouterr().out)
    assert comparison["candidate_version"] == 2
    assert comparison["changed_fields"] == ["title"]


def test_failed_candidate_never_replaces_active_and_deletes_no_records(tmp_path, capsys):
    folder, job = setup_sample(tmp_path)
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    assert main(["dataset", "export", str(folder / "agenda.sqlite")]) == 0
    before = json.loads(capsys.readouterr().out)["records"]
    # Schema-valid sale dates must fail the semantic CI gate.
    from rastro.adapters.sqlite import SQLiteRecipeRepository
    from rastro.application.intelligence import with_version
    from rastro.cli_repair_context import RepairContext

    context = RepairContext.load(job, False)
    _source, key, recipe = context.source(2)
    wrong = with_version(
        recipe.model_copy(
            update={"fields": {**recipe.fields, "starts_at": "/offers/validFrom"}, "fallbacks": ()}
        ),
        2,
    )
    repo = SQLiteRecipeRepository(folder / "recipes.sqlite")
    from datetime import UTC, datetime

    repo.save_candidate(key, wrong, selected_index=0, at=datetime.now(UTC))
    assert main(["recipe", "promote", str(job), "--source", "2", "--version", "2"]) == 1
    output = json.loads(capsys.readouterr().out)
    evaluation = output["sources"][0]["comparisons"][0]["evaluation"]
    assert evaluation["decision"] == "reject"
    assert evaluation["indicators"]["verdict"] == "inconsistent"
    assert repo.get(key).recipe.version == 1
    assert main(["dataset", "export", str(folder / "agenda.sqlite")]) == 0
    assert json.loads(capsys.readouterr().out)["records"] == before


def test_quarantine_can_only_promote_after_fresh_ci_and_leaves_records_intact(tmp_path, capsys):
    folder, job = setup_sample(tmp_path)
    job.write_text(job.read_text().replace("auto_promote = true", "auto_promote = false"))
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    mutate(folder)
    assert main(["recipe", "repair", str(job), "--source", "1"]) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["sources"][0]["comparisons"][0]["evaluation"]["decision"] == "quarantine"
    assert main(["recipe", "promote", str(job), "--source", "1", "--version", "2"]) == 0
    capsys.readouterr()
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()


def test_recipe_paths_refuse_symlinks_and_report_collisions(tmp_path, capsys):
    folder, job = setup_sample(tmp_path)
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    sentinel = folder / "keep.json"
    sentinel.write_text("preserve")
    link = folder / "link.json"
    link.symlink_to(sentinel)
    assert main(["recipe", "repair", str(job), "--report", "link.json"]) == 2
    assert sentinel.read_text() == "preserve"
    capsys.readouterr()
    assert main(["recipe", "repair", str(job), "--report", "keep.json"]) == 2
    assert sentinel.read_text() == "preserve"
    assert not list(folder.glob("repair-report-*.json"))
    capsys.readouterr()
    assert main(["recipe", "repair", str(job), "--source", "0"]) == 2


def test_ambiguous_candidate_is_quarantined_not_active(tmp_path, capsys):
    """REPAIR-005/REC-006: ambiguous provider evidence cannot activate a version."""
    from datetime import UTC, datetime

    from rastro.adapters.scrapling import ScraplingParser
    from rastro.application.recipe_ci import evaluate_candidate
    from rastro.application.recipe_repair import repair_candidates
    from rastro.cli_repair_context import RepairContext
    from rastro.domain.models import Limits
    from rastro.domain.repair import RepairCandidate, RepairEvidence

    folder, job = setup_sample(tmp_path)
    assert main(["job", "load", str(job)]) == 0
    capsys.readouterr()
    context = RepairContext.load(job, False)
    source, key, recipe = context.source(1)
    _, fixtures = context.fixtures(source, recipe)
    candidate = RepairCandidate(
        recipe=recipe.model_copy(update={"fallbacks": ()}),
        origin="scrapling_relocation",
        rationale="Tied fingerprint matches",
        evidence=(RepairEvidence(field="title", match_count=2),),
    )
    now = datetime(2026, 11, 7, tzinfo=UTC)
    comparisons = repair_candidates(
        (candidate,),
        previous=recipe,
        key=key,
        lifecycle=context.repository,
        at=now,
        evaluate=lambda candidate: evaluate_candidate(
            candidate,
            (folder / "sodre-cartelera.html").read_text(),
            source=source,
            schema=context.schema,
            parser=ScraplingParser(),
            limits=Limits(),
            observed_at=now,
            fixtures=fixtures,
            rules=context.job.consistency,
            history=context.history(),
            policy=context.job.repair,
        ),
    )
    assert comparisons[0].evaluation.decision == "quarantine"
    assert context.repository.get(key).recipe.version == 1
    assert context.repository.get(key, version=2).metadata.state == "candidate"
