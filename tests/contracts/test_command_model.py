import json
import sys
from pathlib import Path

import pytest

from rastro import ConfigurationError, InferencePolicy, ModelError, ModelRequest
from rastro.adapters.command_model import CommandModel, first_json_object, render_prompt

REQUEST = ModelRequest(system="Trusted instructions", content='{"untrusted_html":"<h1>hi</h1>"}')
POLICY = InferencePolicy(timeout_seconds=10)
PROPOSAL = '{"strategy":"css","record_selector":"li","fields":{"name":"h3::text"}}'


def fake_cli(tmp_path: Path, monkeypatch, name: str, body: str) -> str:
    """A fake model CLI on a temporary PATH; it is never run through a shell."""
    script = tmp_path / name
    script.write_text(f"#!{sys.executable}\nimport json, os, sys, time\n{body}\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}")
    return name


@pytest.mark.asyncio
async def test_prompt_goes_to_stdin_and_text_answer_is_extracted(tmp_path, monkeypatch):
    log = tmp_path / "stdin.txt"
    program = fake_cli(
        tmp_path,
        monkeypatch,
        "fake-llm",
        f"open({str(log)!r}, 'w').write(sys.stdin.read())\n"
        f"print('Here you go:\\n```json\\n' + {PROPOSAL!r} + '\\n```')",
    )
    model = CommandModel([program, "-p"], local=True, output="text")
    assert await model.generate(REQUEST, POLICY) == PROPOSAL
    prompt = log.read_text()
    assert prompt.startswith("Trusted instructions")
    assert "untrusted_html" in prompt and '"anyOf"' in prompt
    assert repr(model) == "CommandModel(program='fake-llm', output='text')"


@pytest.mark.asyncio
async def test_json_path_digs_into_a_wrapper_object(tmp_path, monkeypatch):
    wrapper = {"type": "result", "result": f"Sure.\n{PROPOSAL}", "usage": {"tokens": 3}}
    program = fake_cli(
        tmp_path,
        monkeypatch,
        "wrapped",
        f"sys.stdin.read(); print(json.dumps({wrapper!r}))",
    )
    model = CommandModel([program], allow_remote=True, json_path="result")
    assert json.loads(await model.generate(REQUEST, POLICY)) == json.loads(PROPOSAL)
    missing = CommandModel([program], allow_remote=True, json_path="answer.text")
    with pytest.raises(ModelError) as error:
        await missing.generate(REQUEST, POLICY)
    assert error.value.code == "invalid_model_response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "policy", "code"),
    [
        ("time.sleep(30)", InferencePolicy(timeout_seconds=0.5), "model_timeout"),
        ("print('x' * 5000)", InferencePolicy(max_response_bytes=1000), "model_response_limit"),
        ("sys.stderr.write('secret-token-123'); sys.exit(3)", POLICY, "model_unavailable"),
        ("print('no json here')", POLICY, "invalid_model_response"),
        ("print('[1, 2]')", POLICY, "invalid_model_response"),
    ],
)
async def test_failures_map_to_safe_categories(tmp_path, monkeypatch, body, policy, code):
    program = fake_cli(tmp_path, monkeypatch, "failing", body)
    with pytest.raises(ModelError) as error:
        await CommandModel([program], local=True).generate(REQUEST, policy)
    assert error.value.code == code
    assert "secret-token" not in str(error.value)


@pytest.mark.asyncio
async def test_missing_program_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ModelError) as error:
        await CommandModel(["not-installed"], local=True).generate(REQUEST, POLICY)
    assert error.value.code == "model_unavailable"


@pytest.mark.asyncio
async def test_only_allow_listed_environment_reaches_the_command(tmp_path, monkeypatch):
    program = fake_cli(
        tmp_path,
        monkeypatch,
        "env-echo",
        "sys.stdin.read(); print(json.dumps({'strategy': 'css', 'fields': "
        "{k: 'x' for k in sorted(os.environ) if k not in ('PWD', 'SHLVL', '_', "
        "'__CF_USER_TEXT_ENCODING', 'LC_CTYPE')}}))",
    )
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "never")
    monkeypatch.setenv("MY_CLI_TOKEN", "fine")
    model = CommandModel([program], local=True, env=["MY_CLI_TOKEN"])
    seen = json.loads(await model.generate(REQUEST, POLICY))["fields"]
    assert "MY_CLI_TOKEN" in seen and "PATH" in seen
    assert "AWS_SECRET_ACCESS_KEY" not in seen


@pytest.mark.parametrize(
    "options",
    [
        {},  # neither allow_remote nor local
        {"local": True, "timeout": 0},
        {"local": True, "output": "xml"},
        {"local": True, "json_path": "a..b"},
        {"local": True, "env": "PATH"},
        {"local": True, "env": ["BAD-NAME"]},
    ],
)
def test_configuration_is_validated(options):
    with pytest.raises(ConfigurationError):
        CommandModel(["llm"], **options)


@pytest.mark.parametrize("argv", [[], "llm -p", [""], ["llm", "a\0b"], ["x"] * 65])
def test_argv_must_be_a_bounded_list(argv):
    with pytest.raises(ConfigurationError):
        CommandModel(argv, local=True)


def test_prompt_rendering_and_json_extraction():
    prompt = render_prompt(REQUEST.model_copy(update={"response_schema": {"type": "object"}}))
    assert '{"type": "object"}' in prompt
    assert first_json_object('noise {bad json} then {"a": {"b": 1}} tail') == '{"a": {"b": 1}}'
    with pytest.raises(ValueError):
        first_json_object("[1] and nothing else")
