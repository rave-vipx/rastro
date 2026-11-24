"""`rastro shell JOB` opens an interactive Python shell on a job's parsed page (N39)."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from rastro import cli_shell
from rastro.application.live_page import LivePage
from rastro.cli import main

PAGE = (
    "<html><body><article><h2>Item A</h2></article><article><h2>Item B</h2></article></body></html>"
)
FILE_JOB = """format_version = 1
[[sources]]
kind = "file"
path = "page.html"
url = "https://shop.example/list"

[[fields]]
name = "name"
type = "string"

[recipe]
strategy = "css"
record_selector = "article"
[recipe.fields]
name = "h2::text"
"""


@pytest.fixture
def no_ipython(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "IPython", None)  # importing it raises ImportError


def scripted(monkeypatch: pytest.MonkeyPatch, *lines: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{line}\n" for line in lines)))


@pytest.mark.usefixtures("no_ipython")
def test_shell_opens_with_job_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "page.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "job.toml").write_text(FILE_JOB, encoding="utf-8")
    scripted(
        monkeypatch,
        "print('articles', len(page.css('article')))",
        "print('fields', [f.name for f in job.fields])",
        "print('url', url)",
    )
    assert main(["shell", str(tmp_path / "job.toml")]) == 0
    out = capsys.readouterr().out
    assert "articles 2" in out and "fields ['name']" in out
    assert "url https://shop.example/list" in out


@pytest.mark.usefixtures("no_ipython")
def test_shell_renders_a_url_source_through_the_live_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    job = FILE_JOB.replace('kind = "file"\npath = "page.html"\n', "").replace(
        "[[fields]]", '[acquisition]\nallowed_hosts = ["shop.example"]\n\n[[fields]]'
    )
    (tmp_path / "job.toml").write_text(job, encoding="utf-8")
    seen: list[int] = []

    def fake(job: object, index: int, **options: object) -> LivePage:
        seen.append(index)
        return LivePage("https://shop.example/list", PAGE)

    monkeypatch.setattr(cli_shell, "load_live_page", fake)
    scripted(monkeypatch, "print('titles', page.css('h2::text').getall())")
    assert main(["shell", str(tmp_path / "job.toml")]) == 0
    assert seen == [1]
    assert "titles ['Item A', 'Item B']" in capsys.readouterr().out


def test_shell_refuses_a_missing_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "page.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "job.toml").write_text(FILE_JOB, encoding="utf-8")
    assert main(["shell", str(tmp_path / "job.toml"), "--source", "3"]) != 0
    assert "source is out of range" in capsys.readouterr().err
