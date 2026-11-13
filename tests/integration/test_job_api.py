"""Public job API: load, repair, promote, apply (REPAIR-002-006, REC-006, QUAL-006, DATA-001).

Synthetic job cards: the optional salary is absent in one of five cards, then the
title markup changes so the job file's selector breaks.
"""

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path

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
    with pytest.raises(rastro.ConfigurationError, match="never a validation"):
        rastro.promote_job_recipe(job, 1, 2, reason="Force it", override_gates=True)
    assert repository.get(key).recipe.version == 1
    assert repository.get(key, version=2).metadata.state == "retired"
    assert repository.history(key).events[-1].reason == "Recipe CI: reject"


BLANK_TITLE = '<article class="card" data-id="job-6"><h2 class="new-title">  </h2></article>'


def test_repair_and_promote_agree_and_repeated_repairs_reuse_the_version(job, capsys):
    """REPAIR-002/005, REC-006: one evaluator for repair and promote; a rejection retires
    the version with an event, names the blocking record and field, and a second repair of
    the same definition allocates no new version."""
    assert rastro.load_job_file(job).job.status == "success"
    broken = board("new-title", wrap=True).replace("</section>", BLANK_TITLE + "</section>")
    (job.parent / "board.html").write_text(broken, encoding="utf-8")
    first = rastro.repair_job_source(job, 1, auto_promote=True)
    (comparison,) = first.sources[0].comparisons
    evaluation = comparison.evaluation
    assert evaluation.decision == "reject"
    assert evaluation.reasons == ("validation_or_regression_failed",)
    assert any(i.record_index == 5 and i.field == "title" for i in evaluation.blocking)
    repository, key = history(job)
    version = comparison.candidate_version
    assert repository.get(key, version=version).metadata.state == "retired"
    second = rastro.repair_job_source(job, 1, auto_promote=True)
    assert [c.candidate_version for c in second.sources[0].comparisons] == [version]
    assert [c.reused for c in second.sources[0].comparisons] == [True]
    assert first.sources[0].comparisons[0].reused is False
    assert repository.next_version(key) == version + 1
    assert main(["recipe", "promote", str(job), "--version", str(version), "--reason", "x"]) == 2
    assert "Only a stored candidate" in capsys.readouterr().err


def test_promote_rejects_like_repair_and_retires(job):
    """REPAIR-005: promote reaches repair's verdict for the same candidate and page."""
    assert rastro.load_job_file(job).job.status == "success"
    repository, key = history(job)
    recipe = rastro.load_job(job).sources[0].recipe
    candidate = recipe.model_copy(
        update={"version": 2, "fields": {**recipe.fields, "title": "h2.new-title::text"}}
    )
    repository.save_candidate(key, candidate, selected_index=0, at=datetime.now(UTC))
    broken = board("new-title").replace("</main>", BLANK_TITLE + "</main>")
    (job.parent / "board.html").write_text(broken, encoding="utf-8")
    report = rastro.promote_job_recipe(job, 1, 2, reason="x")
    evaluation = report.sources[0].comparisons[0].evaluation
    assert (evaluation.decision, evaluation.reasons) == (
        "reject",
        ("validation_or_regression_failed",),
    )
    assert repository.get(key, version=2).metadata.state == "retired"


def test_symlinked_recipe_memory_is_refused_by_run_load_and_preview(job, capsys):
    """SEC-008: recipe_memory is never opened through a symlink."""
    assert rastro.load_job_file(job).job.status == "success"
    real = job.parent / "real.sqlite"
    (job.parent / "recipes.sqlite").rename(real)
    (job.parent / "recipes.sqlite").symlink_to(real)
    with pytest.raises(rastro.ConfigurationError, match="symlink"):
        rastro.run_job_file(job)
    with pytest.raises(rastro.ConfigurationError, match="symlink"):
        rastro.load_job_file(job)
    assert main(["job", "preview", str(job)]) == 2
    assert "symlink" in capsys.readouterr().err


def test_pinned_version_reports_what_the_store_holds(job):
    """REC-006: a pinned recipe_version is reported only when the store has it."""
    from rastro.application.active_recipes import active_job

    assert rastro.load_job_file(job).job.status == "success"
    loaded = rastro.load_job(job)
    pinned = loaded.model_copy(
        update={
            "recipe_version": 1,
            "sources": tuple(s.model_copy(update={"recipe": None}) for s in loaded.sources),
        }
    )
    _, selections = active_job(pinned, base_dir=job.parent, allow_absolute_paths=False)
    assert (selections[0].recipe_origin, selections[0].recipe_version) == ("memory", 1)
    missing = pinned.model_copy(update={"recipe_version": 9})
    _, selections = active_job(missing, base_dir=job.parent, allow_absolute_paths=False)
    assert selections[0].recipe_version is None


def test_apply_refuses_a_symlinked_parent_directory(job, tmp_path, monkeypatch):
    """SEC-008: below the working directory, apply never follows a symlinked directory."""
    repaired(job)
    work = tmp_path / "work"
    work.mkdir()
    (work / "alias").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.chdir(work)
    before = job.read_bytes()
    with pytest.raises(rastro.ConfigurationError, match="symlink"):
        rastro.apply_job_recipe(Path("alias") / "job.toml", 1)
    assert job.read_bytes() == before


SHARED = """# Two boards share one job recipe.
format_version = 1
recipe_memory = "recipes.sqlite"

[dataset]
name = "jobs"
path = "jobs.sqlite"
identity = ["job_id"]

[recipe]
version = 1
strategy = "css"
record_selector = "article.card"
require_evidence = true

[recipe.fields]
job_id = "::attr(data-id)"
title = "h2.title::text"
salary = "span.salary::text"

[[sources]]
kind = "file"
path = "board.html"
url = "https://example.test/jobs"

[[sources]]
kind = "file"
path = "other.html"
url = "https://example.test/other"

# Fields follow.
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


def test_apply_gives_one_source_of_a_shared_recipe_its_own_table(tmp_path, capsys):
    """REC-006: apply writes [sources.recipe] for the repaired source; the others keep the
    shared job recipe."""
    (tmp_path / "board.html").write_text(board(), encoding="utf-8")
    (tmp_path / "other.html").write_text(board(), encoding="utf-8")
    job = tmp_path / "job.toml"
    job.write_text(SHARED, encoding="utf-8")
    assert rastro.load_job_file(job).job.status == "success"
    break_title(job)
    assert rastro.repair_job_source(job, 1, auto_promote=True).sources[0].promoted_version == 2
    rastro.apply_job_recipe(job, 1)
    text = job.read_text(encoding="utf-8")
    assert text.startswith(SHARED.split("[[sources]]")[0])
    assert text.endswith(SHARED[SHARED.index("# Fields follow.") :])
    applied = rastro.load_job(job)
    assert applied.sources[0].recipe.version == 2
    assert applied.sources[1].recipe is None
    assert applied.recipe.fields["title"] == "h2.title::text"
    result = rastro.run_job_file(job)
    assert [s.recipe_origin for s in result.recipes] == ["job", "job"]
    assert result.job.status == "success"


def test_recipe_show_and_compare_report_record_selector_changes(job, capsys):
    """REC-006: show prints a stored version read-only; compare lists changed settings."""
    assert rastro.load_job_file(job).job.status == "success"
    repository, key = history(job)
    recipe = rastro.load_job(job).sources[0].recipe
    moved = recipe.model_copy(update={"version": 2, "record_selector": "main article.card"})
    repository.save_candidate(key, moved, selected_index=0, at=datetime.now(UTC))
    capsys.readouterr()
    assert main(["recipe", "show", str(job), "--version", "2"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert (shown["version"], shown["state"]) == (2, "candidate")
    assert shown["recipe"]["record_selector"] == "main article.card"
    assert main(["recipe", "show", str(job)]) == 0
    assert json.loads(capsys.readouterr().out)["version"] == 1
    command = ["recipe", "compare", str(job), "--from-version", "1", "--to-version", "2"]
    assert main(command) == 0
    compared = json.loads(capsys.readouterr().out)
    assert compared["changed_fields"] == []
    assert compared["changed_settings"] == ["record_selector"]


def test_recipe_help_describes_every_subcommand(capsys):
    with pytest.raises(SystemExit):
        main(["recipe", "--help"])
    out = capsys.readouterr().out
    for name in ("repair", "promote", "rollback", "compare", "apply", "show", "prune"):
        assert name in out
    assert "Print a stored recipe version" in out


def test_container_rename_relocates_with_a_card_missing_its_optional_field(job):
    """REPAIR-001, SCR-002: rows that omit an optional child stay one homogeneous container."""
    assert rastro.load_job_file(job).job.status == "success"
    renamed = board().replace('class="card"', 'class="job-card"')
    (job.parent / "board.html").write_text(renamed, encoding="utf-8")
    report = rastro.repair_job_source(job, 1, auto_promote=True)
    (source,) = report.sources
    assert source.promoted_version == 2, source.model_dump_json(indent=2)


def stage_candidate(job, selector="h2.new-title::text"):
    repository, key = history(job)
    recipe = rastro.load_job(job).sources[0].recipe
    candidate = recipe.model_copy(
        update={"version": 2, "fields": {**recipe.fields, "title": selector}}
    )
    repository.save_candidate(key, candidate, selected_index=0, at=datetime.now(UTC))
    return repository, key


def test_promote_on_an_unusable_page_keeps_the_candidate(job):
    """REPAIR-005: a consent wall or empty shell never retires a candidate."""
    assert rastro.load_job_file(job).job.status == "success"
    repository, key = stage_candidate(job)
    (job.parent / "board.html").write_text(
        "<html><body><p>Accept cookies to continue</p></body></html>", encoding="utf-8"
    )
    report = rastro.promote_job_recipe(job, 1, 2, reason="x")
    assert report.sources[0].comparisons[0].evaluation.decision == "reject"
    assert report.sources[0].notes == ("page_not_usable",)
    assert report.sources[0].promoted_version is None
    assert repository.get(key, version=2).metadata.state == "candidate"
    with pytest.raises(rastro.ConfigurationError, match="candidate was kept"):
        rastro.promote_job_recipe(job, 1, 2, reason="x", override_gates=True)
    assert repository.get(key, version=2).metadata.state == "candidate"


def test_promote_reject_on_a_usable_page_reports_retirement(job):
    """REPAIR-005: the not-promoted report says the candidate was retired."""
    assert rastro.load_job_file(job).job.status == "success"
    repository, key = stage_candidate(job, selector="p.none::text")
    report = rastro.promote_job_recipe(job, 1, 2, reason="x")
    assert report.sources[0].notes == ("candidate_retired",)
    assert json.loads(report.model_dump_json())["sources"][0]["notes"] == ["candidate_retired"]
    assert repository.get(key, version=2).metadata.state == "retired"


def test_equal_reuse_prefers_the_newest_candidate(job):
    """REC-006: reuse picks the newest equal candidate, else the newest retired twin."""
    from rastro.application.recipe_repair import _equal_stored

    assert rastro.load_job_file(job).job.status == "success"
    repository, key = history(job)
    recipe = rastro.load_job(job).sources[0].recipe
    proposal = recipe.model_copy(update={"fields": {**recipe.fields, "title": "h3::text"}})
    now = datetime.now(UTC)
    for version in (2, 3, 4):
        repository.save_candidate(
            key, proposal.model_copy(update={"version": version}), selected_index=0, at=now
        )
    repository.transition(key, 4, rastro.RecipeState.RETIRED, reason="Rejected", at=now)
    assert _equal_stored(repository, key, proposal) == (3, "candidate")
    repository.transition(key, 3, rastro.RecipeState.RETIRED, reason="Rejected", at=now)
    repository.transition(key, 2, rastro.RecipeState.RETIRED, reason="Rejected", at=now)
    assert _equal_stored(repository, key, proposal) == (4, "retired")
