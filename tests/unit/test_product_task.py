"""DIFF-002: independent baseline persistence and honest offline measurement."""

import json
from pathlib import Path

import pytest

from benchmarks.product_task.baseline import load_source, read_records, sources
from benchmarks.product_task.measure import measure, score


def test_load_update_failure_and_repair(tmp_path: Path) -> None:
    database = tmp_path / "agenda.sqlite"
    configs = sources()
    for source in configs:
        assert load_source(database, source) == "success"
    before = read_records(database)
    assert len(before) == 7
    assert load_source(database, configs[0]) == "success"
    assert read_records(database) == before
    broken = configs[2].model_copy(
        update={"path": "benchmarks/product_task/fixtures/third-broken.html"}
    )
    assert load_source(database, broken) == "missing_data"
    assert read_records(database) == before
    repaired = broken.model_copy(
        update={"mapping": broken.mapping.model_copy(update={"record_selector": ".performance"})}
    )
    assert load_source(database, repaired) == "success"
    assert len(read_records(database)) == 7
    assert (
        next(row for row in read_records(database) if row["performance_id"] == "costa-1")["price"]
        == "280"
    )


@pytest.mark.parametrize("bad", ["", "<main></main>", "<div class='dance'></div>"])
def test_bad_source_never_deletes(tmp_path: Path, bad: str) -> None:
    database = tmp_path / "agenda.sqlite"
    source = sources()[2]
    assert load_source(database, source) == "success"
    before = read_records(database)
    assert load_source(database, source, html=bad) != "success"
    assert read_records(database) == before


def test_measurement_scores_wrong_accepted_records_and_missing_separately() -> None:
    expected = [{"performance_id": "one", "price": "10"}]
    assert score(expected, expected) == {"silent_errors": 0, "missing_records": 0}
    assert score([{**expected[0], "price": "9"}], expected)["silent_errors"] == 1
    assert score([], expected) == {"silent_errors": 0, "missing_records": 1}


@pytest.mark.usefixtures("offline_cli_loop")
def test_harness_reports_real_phases_and_unmeasured_human_time(tmp_path: Path) -> None:
    report = measure(tmp_path / "agenda.sqlite")
    rastro = report["rastro"]
    assert [p["records"] for p in rastro["phases"]] == [6, 7, 7, 7, 7]
    assert [p["silent_errors"] for p in rastro["phases"]] == [0] * 5
    assert [p["missing_records"] for p in rastro["phases"]] == [0] * 5
    assert rastro["phases"][3]["stale_records"] == 1
    assert rastro["phases"][3]["status"] == "missing_data"
    assert rastro["repair"]["decision"] == "promote"
    assert rastro["repair"]["generation_outcome"] == "no_candidates"
    assert rastro["repair"]["config_edits"] == 1
    assert rastro["repair"]["evaluation"]["historical_passed"] >= 1
    assert all(item["exit_code"] == 0 for item in rastro["phases"][0]["preflight"])
    assert rastro["network_requests"] == rastro["model_calls"] == 0
    assert rastro["repair"]["human_minutes"] is None
    baseline = report["baseline"]
    assert [phase["records"] for phase in baseline["phases"]] == [6, 7, 7, 7, 7]
    assert [phase["silent_errors"] for phase in baseline["phases"]] == [0, 0, 0, 0, 0]
    assert baseline["phases"][3]["status"] == "missing_data"
    assert baseline["phases"][3]["stale_records"] == 1
    assert baseline["network_requests"] == baseline["model_calls"] == 0
    assert baseline["repair"]["config_edits"] == 1
    assert baseline["repair"]["human_minutes"] is None
    assert baseline["site_specific_lines"] > 0
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("replacement", [">-1<", ">NaN<"])
def test_invalid_price_snapshot_preserves_database(tmp_path: Path, replacement: str) -> None:
    from benchmarks.product_task.baseline import ROOT

    source = sources()[2]
    database = tmp_path / "agenda.sqlite"
    assert load_source(database, source) == "success"
    before = read_records(database)
    bad = (ROOT / source.path).read_text().replace(">250<", replacement)
    assert load_source(database, source, html=bad) == "invalid_data"
    assert read_records(database) == before


def test_ambiguous_fields_and_duplicate_identity_are_rejected(tmp_path: Path) -> None:
    from benchmarks.product_task.baseline import ROOT

    source = sources()[2]
    database = tmp_path / "agenda.sqlite"
    assert load_source(database, source) == "success"
    before = read_records(database)
    html = (ROOT / source.path).read_text()
    article = html[html.index("<article") : html.index("</article>") + len("</article>")]
    duplicated = html.replace("</main>", article + "</main>")
    assert load_source(database, source, html=duplicated) == "ambiguous_identity"
    ambiguous = html.replace("<h2>", "<h2>Another title</h2><h2>")
    assert load_source(database, source, html=ambiguous) != "success"
    assert read_records(database) == before


def test_network_attempt_aborts_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    def attempt(*args: object, **kwargs: object) -> str:
        socket.getaddrinfo("network.example", 443)
        return "success"

    monkeypatch.setattr("benchmarks.product_task.measure.load_source", attempt)
    with pytest.raises(RuntimeError, match="attempted network"):
        measure(tmp_path / "agenda.sqlite")


def test_existing_database_is_not_overwritten(tmp_path: Path) -> None:
    database = tmp_path / "agenda.sqlite"
    database.write_text("keep me")
    with pytest.raises(ValueError, match="new database"):
        measure(database)
    assert database.read_text() == "keep me"


def test_partial_source_never_overwrites_previous_snapshot(tmp_path: Path) -> None:
    from benchmarks.product_task.baseline import ROOT

    source = sources()[0]
    database = tmp_path / "agenda.sqlite"
    assert load_source(database, source) == "success"
    before = read_records(database)
    html = (ROOT / source.path).read_text().replace(">450<", ">-1<", 1).replace(">600<", ">700<")
    assert load_source(database, source, html=html) == "partial_success"
    assert read_records(database) == before


def test_rastro_uses_identical_inputs_and_field_mappings() -> None:
    """DIFF-002: adapters receive the same source bytes, identity and selectors."""
    from benchmarks.product_task.measure import HERE

    job = json.loads((HERE / "rastro/job.json").read_text())
    assert job["dataset"]["identity"] == ["performance_id"]
    for baseline, rastro in zip(sources(), job["sources"], strict=True):
        assert rastro["path"] == Path(baseline.path).name
        assert rastro["url"] == baseline.url
        assert rastro["recipe"]["fields"] == baseline.mapping.fields
        assert rastro["recipe"]["strategy"] == baseline.mapping.strategy
        assert rastro["recipe"].get("record_selector") == baseline.mapping.record_selector
        assert rastro["recipe"].get("item_type") == baseline.mapping.item_type


def test_rastro_network_attempt_aborts_measurement(tmp_path, monkeypatch) -> None:
    """DIFF-002: a Rastro-side network attempt cannot be reported as zero."""
    import socket

    from benchmarks.product_task.rastro_path import run

    def attempt(*args):
        socket.getaddrinfo("network.example", 443)

    monkeypatch.setattr("benchmarks.product_task.rastro_path.command", attempt)
    with pytest.raises(RuntimeError, match="attempted network"):
        run(tmp_path / "rastro")
