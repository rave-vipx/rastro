import json
import logging

import pytest

from rastro import __version__
from rastro.cli import main


def test_demo_is_executable_and_offline(capsys):
    assert main(["demo"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "success"
    assert result["records"][0]["data"]["price"] == 12.5


def test_verbose_logging_is_json_on_stderr(capsys):
    previous_level = logging.getLogger("rastro").level
    assert main(["--verbose", "demo"]) == 0
    captured = capsys.readouterr()
    event = json.loads(captured.err)
    assert event["event"] == "extraction_completed"
    assert event["job_id"] == json.loads(captured.out)["job_id"]
    assert "source_url" not in event
    assert logging.getLogger("rastro").level == previous_level


def test_doctor(capsys):
    assert main(["doctor"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["rastro"] == __version__
    assert "offline_html" in result["capabilities"]


def test_validate_recipe(tmp_path, capsys, recipe):
    path = tmp_path / "recipe.json"
    path.write_text(recipe.model_dump_json())
    assert main(["validate-recipe", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["strategy"] == "jsonld"


@pytest.mark.parametrize("content", [None, '{"code":"PRIVATE"}', "x" * 65_537])
def test_invalid_recipe_fails_safely(tmp_path, capsys, content):
    path = tmp_path / "recipe.json"
    if content is not None:
        path.write_text(content)
    assert main(["validate-recipe", str(path)]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["error"] == "invalid_input"
    assert "PRIVATE" not in captured.err


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_missing_command_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
