"""Public job API: load, repair, promote, apply (REPAIR-002-006, REC-006, QUAL-006, DATA-001).

Synthetic job cards: the optional salary is absent in one of five cards, then the
title markup changes so the job file's selector breaks.
"""

import json
import tomllib
from datetime import UTC, datetime

import pytest

import rastro
from rastro.application.active_recipes import source_key
from rastro.cli import main

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

CARD = (
    '<article class="card" data-id="job-{n}"><h2 class="{cls}">Synthetic role {n}</h2>'
    "{salary}</article>"
)
JOB = """# Synthetic job board; this comment must survive recipe apply.
format_version = 1
recipe_memory = "recipes.sqlite"

[dataset]
name = "jobs"
path = "jobs.sqlite"
identity = ["job_id"]

[[sources]]
kind = "file"
path = "board.html"
url = "https://example.test/jobs"

[sources.recipe]
version = 1
strategy = "css"
record_selector = "article.card"
require_evidence = true

[sources.recipe.fields]
job_id = "::attr(data-id)"
title = "h2.title::text"
salary = "span.salary::text"

# Fields keep their order and comments.
[[fields]]
name = "job_id"
type = "string"

[[fields]]
name = "title"
type = "string"

[[fields]]
name = "salary"
type = "string"
required = false
"""


def board(title_class="title", wrap=False):
    cards = "".join(
        CARD.format(
            n=n,
            cls=title_class,
            salary="" if n == 3 else f'<span class="salary">{n}000</span>',
        )
        for n in range(1, 6)
    )
    body = f"<section>{cards}</section>" if wrap else cards
    return f"<!doctype html><html><body><main>{body}</main></body></html>"


@pytest.fixture
def job(tmp_path):
    (tmp_path / "board.html").write_text(board(), encoding="utf-8")
    path = tmp_path / "job.toml"
    path.write_text(JOB, encoding="utf-8")
    return path


def break_title(job):
    (job.parent / "board.html").write_text(board("new-title", wrap=True), encoding="utf-8")


def repaired(job):
    first = rastro.load_job_file(job)
    assert first.job.status == "success"
    assert first.sources[0].recipe_origin == "job"
    break_title(job)
    assert rastro.run_job_file(job).job.status == "failed"
    return rastro.repair_job_source(job, 1, auto_promote=True)


def history(job):
    loaded = rastro.load_job(job)
    repository = rastro.SQLiteRecipeRepository(job.parent / "recipes.sqlite")
    return repository, source_key(loaded, loaded.sources[0])


def test_optional_field_absent_in_one_card_does_not_block_promotion(job):
    """QUAL-006: default thresholds promote a correct repair with a legitimately empty salary."""
    report = repaired(job)
    (source,) = report.sources
    assert source.promoted_version == 2, source.model_dump_json(indent=2)
    assert source.comparisons[0].evaluation.indicators.completeness == 1.0
    assert report.exit_code == 0


def test_promoted_repair_is_used_by_run_and_load_until_applied(job):
    """REC-006: a newer serving recipe in memory wins over the job file's recipe."""
    repaired(job)
    result = rastro.run_job_file(job)
    assert result.job.status == "success"
    assert (result.recipes[0].recipe_origin, result.recipes[0].recipe_version) == ("memory", 2)
    loaded = rastro.load_job_file(job)
    assert (loaded.sources[0].recipe_origin, loaded.sources[0].recipe_version) == ("memory", 2)
    assert json.loads(loaded.model_dump_json())["sources"][0]["recipe_origin"] == "memory"


def test_apply_writes_serving_recipe_and_keeps_the_rest_of_the_file(job):
    """REC-006: apply writes the recipe back; text outside the recipe tables is unchanged."""
    repaired(job)
    before = job.read_text(encoding="utf-8")
    assert rastro.apply_job_recipe(job, 1) == job.resolve()
    after = job.read_text(encoding="utf-8")
    head, tail = before.split("[sources.recipe]")[0], before.split("# Fields keep")[1]
    assert after.startswith(head) and after.endswith(tail)
    applied = rastro.load_job(job).sources[0].recipe
    assert applied.version == 2 and applied.fields["title"] != "h2.title::text"
    result = rastro.run_job_file(job)
    assert result.job.status == "success"
    assert (result.recipes[0].recipe_origin, result.recipes[0].recipe_version) == ("job", 2)
    assert not list(job.parent.glob(".*.tmp"))
    rastro.apply_job_recipe(job, 1)
    assert job.read_text(encoding="utf-8") == after


def test_cli_apply_reports_change_and_refuses_symlinks(job, capsys):
    repaired(job)
    link = job.parent / "link.toml"
    link.symlink_to(job)
    assert main(["recipe", "apply", str(link), "--source", "1"]) == 2
    capsys.readouterr()
    assert main(["recipe", "apply", str(job), "--source", "1"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert (output["changed"], output["recipe_version"]) == (True, 2)
    assert main(["recipe", "apply", str(job), "--source", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["changed"] is False
    assert main(["recipe", "apply", str(job), "--source", "2"]) == 2


def test_json_job_apply_rewrites_only_the_recipe(job):
    data = tomllib.loads(job.read_text(encoding="utf-8"))
    path = job.with_suffix(".json")
    path.write_text(json.dumps(data), encoding="utf-8")
    assert rastro.load_job_file(path).job.status == "success"
    break_title(path)
    assert rastro.repair_job_source(path, 1, auto_promote=True).sources[0].promoted_version == 2
    rastro.apply_job_recipe(path, 1)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["sources"][0]["recipe"]["version"] == 2
    assert {k: v for k, v in written.items() if k != "sources"} == {
        k: v for k, v in data.items() if k != "sources"
    }


def quarantined(job):
    """A candidate that misses only a measured threshold stays a quarantined candidate."""
    job.write_text(JOB + "\n[repair]\nmin_records = 50\n", encoding="utf-8")
    report = repaired(job)
    evaluation = report.sources[0].comparisons[0].evaluation
    assert evaluation.decision == "quarantine"
    assert "insufficient_current_records" in evaluation.reasons
    assert report.exit_code == 3
    return report.sources[0].comparisons[0].candidate_version


def test_override_gates_promotes_quarantined_candidate_with_recorded_reason(job, capsys):
    """REPAIR-005: a person may override measured gates; the reason is audited."""
    version = quarantined(job)
    plain = rastro.promote_job_recipe(job, 1, version, reason="Looks right")
    assert plain.sources[0].promoted_version is None
    with pytest.raises(rastro.ConfigurationError):
        rastro.promote_job_recipe(job, 1, version, reason="  ", override_gates=True)
    command = ["recipe", "promote", str(job), "--version", str(version), "--override-gates"]
    assert main(command) == 2
    capsys.readouterr()
    assert main([*command, "--reason", "Checked five cards by hand"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["sources"][0]["promoted_version"] == version
    repository, key = history(job)
    events = repository.history(key).events
    assert events[-1].reason == "gates overridden: Checked five cards by hand"
    assert rastro.run_job_file(job).recipes[0].recipe_origin == "memory"


def test_override_never_activates_a_rejected_candidate(job):
    """REPAIR-005: failed validation or regressions cannot be overridden."""
    assert rastro.load_job_file(job).job.status == "success"
    repository, key = history(job)
    recipe = rastro.load_job(job).sources[0].recipe
    wrong = recipe.model_copy(
        update={"version": 2, "fields": {"job_id": "::attr(data-id)", "title": "main::text"}}
    )
    repository.save_candidate(key, wrong, selected_index=0, at=datetime.now(UTC))
    report = rastro.promote_job_recipe(job, 1, 2, reason="Force it", override_gates=True)
    assert report.sources[0].comparisons[0].evaluation.decision == "reject"
    assert report.sources[0].promoted_version is None
    assert repository.get(key).recipe.version == 1
