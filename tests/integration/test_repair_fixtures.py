"""REPAIR-002/REC-006: every configured historical fixture participates or fails visibly."""

import json
from datetime import UTC, datetime

import pytest

from rastro.adapters.structured import JsonParser
from rastro.application.extraction import extract_document
from rastro.cli import main
from rastro.cli_repair_context import RepairContext
from rastro.domain.models import ConfigurationError, Limits, Source
from rastro.mocklab.fixtures import build_fixture

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


def setup_job(tmp_path, capsys):
    job = {
        "format_version": 1,
        "recipe_memory": "recipes.sqlite",
        "dataset": {"name": "events", "path": "dataset.sqlite", "identity": ["title"]},
        "fields": [{"name": "title", "type": "string"}],
        "sources": [
            {
                "kind": "file",
                "path": "events.json",
                "representation": "json",
                "url": "https://example.com/events?page=1",
                "recipe": {
                    "strategy": "json",
                    "record_path": "/rows",
                    "fields": {"title": "/title"},
                    "require_evidence": True,
                },
            }
        ],
        "repair": {"auto_promote": True, "fixtures": ["historical"]},
    }
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    (tmp_path / "events.json").write_text('{"rows":[{"title":"Dance"}]}', encoding="utf-8")
    assert main(["job", "load", str(path)]) == 0
    capsys.readouterr()
    return path, job


def write_history(context, source):
    _, _, recipe = context.source(1)
    body = '{"rows":[{"title":"Earlier dance"},{"title":"Another dance"}]}'
    result = extract_document(
        body,
        source=source,
        schema=context.schema,
        recipe=recipe,
        parser=JsonParser(),
        limits=Limits(),
    )
    fixture = build_fixture(
        body,
        source=source,
        schema=context.schema,
        recipe=recipe,
        result=result,
        synthetic_count=0,
    )
    fixture.write(context.base / "historical")


def test_query_source_fixture_blocks_promotion_despite_passing_retained_sample(tmp_path, capsys):
    path, _ = setup_job(tmp_path, capsys)
    context = RepairContext.load(path, False)
    source, key, recipe = context.source(1)
    write_history(context, source)
    candidate = recipe.model_copy(update={"record_path": "/rows/0", "version": 2})
    context.repository.save_candidate(key, candidate, selected_index=0, at=datetime.now(UTC))
    assert main(["recipe", "promote", str(path), "--version", "2"]) == 1
    report = json.loads(capsys.readouterr().out)
    evaluation = report["sources"][0]["comparisons"][0]["evaluation"]
    assert evaluation["current_status"] == "success"
    assert evaluation["historical_fixtures"] == 2
    assert evaluation["historical_passed"] == 1
    assert evaluation["decision"] == "reject"
    assert context.repository.get(key).recipe.version == 1


@pytest.mark.parametrize("case", ["unmatched", "ambiguous", "region"])
def test_configured_fixture_must_match_exactly_one_job_source(tmp_path, capsys, case):
    path, job = setup_job(tmp_path, capsys)
    context = RepairContext.load(path, False)
    source, _, recipe = context.source(1)
    if case == "unmatched":
        source = Source(url="https://example.com/wrong")
    elif case == "region":
        source = source.model_copy(update={"locale": "es-UY"})
    else:
        job["sources"].append({**job["sources"][0], "url": "https://example.com/events?page=2"})
        path.write_text(json.dumps(job), encoding="utf-8")
        context = RepairContext.load(path, False)
    write_history(context, source)
    with pytest.raises(ConfigurationError, match="exactly one job source"):
        context.fixtures(context.source(1)[0], recipe)


def test_fixture_for_another_job_source_is_valid_but_not_replayed_here(tmp_path, capsys):
    path, job = setup_job(tmp_path, capsys)
    job["sources"].append({**job["sources"][0], "url": "https://example.com/other"})
    path.write_text(json.dumps(job), encoding="utf-8")
    context = RepairContext.load(path, False)
    write_history(context, Source(url="https://example.com/other"))
    source, _, recipe = context.source(1)
    _, fixtures = context.fixtures(source, recipe)
    assert len(fixtures) == 1  # Only this source's retained success.
