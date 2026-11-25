"""The crawl checkpoint: versioned JSON, private files, tampered files discarded, never pickle."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from rastro.application.crawl_checkpoint import (
    CheckpointStore,
    checkpoint_store,
    crawl_key,
    crawl_state,
    seen_key,
)
from rastro.domain.jobs import Job
from rastro.domain.spider import SpiderPolicy

UNBOUNDED = SpiderPolicy(mode="spider", links="a::attr(href)", max_pages="unbounded")
START = "https://shop.example/list"


def store(tmp_path) -> CheckpointStore:
    with crawl_state(tmp_path, "job-key"):
        found = checkpoint_store(START, UNBOUNDED)
    assert found is not None
    return found


def test_a_saved_checkpoint_is_private_versioned_json_and_loads_back(tmp_path):
    cp = store(tmp_path)
    secret_page = "https://shop.example/item?id=1&token=abc123"
    assert cp.save([(1, "https://shop.example/a"), (2, secret_page)], [seen_key(secret_page)])
    if os.name != "nt":  # Windows has no POSIX modes; the folder's access rules apply
        assert stat.S_IMODE(cp.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(cp.path.parent.stat().st_mode) == 0o700
    raw = json.loads(cp.path.read_text())
    assert raw["version"] == 1
    assert all(len(item) == 32 and "token" not in item for item in raw["seen"])
    loaded = cp.load()
    assert not loaded.discarded and loaded.checkpoint is not None
    first = loaded.checkpoint.frontier[0]
    assert (first.depth, first.url) == (1, "https://shop.example/a")
    assert loaded.checkpoint.seen == (seen_key(secret_page),)


@pytest.mark.parametrize(
    "content",
    [
        b"{not json",
        b'{"version": 2, "key": "x", "frontier": [], "seen": []}',
        b"\x80\x04\x95pickle",
        b'{"version": 1, "key": "' + b"0" * 64 + b'", "frontier": [], "seen": []}',
        b'{"version": 1, "frontier": [{"depth": 1, "url": "file:///etc/passwd"}], "seen": []}',
    ],
)
def test_checkpoint_tamper_rejected(tmp_path, content):
    cp = store(tmp_path)
    cp.save([(1, "https://shop.example/a")], [])
    cp.path.write_bytes(content)
    loaded = cp.load()
    assert loaded.discarded and loaded.checkpoint is None
    assert not cp.path.exists()


def test_a_symbolic_link_is_never_followed(tmp_path):
    cp = store(tmp_path)
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    cp.path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, cp.path)
    assert cp.load().discarded
    assert target.read_text() == "{}" and not cp.path.is_symlink()
    os.symlink(target, cp.path)
    assert not cp.save([(1, "https://shop.example/a")], [])
    assert target.read_text() == "{}"


def test_clear_reports_whether_a_checkpoint_existed(tmp_path):
    cp = store(tmp_path)
    assert cp.clear() is False
    cp.save([(1, "https://shop.example/a")], [])
    assert cp.clear() is True and not cp.path.exists()


def test_only_an_unbounded_crawl_inside_a_state_folder_gets_a_store(tmp_path):
    assert checkpoint_store(START, UNBOUNDED) is None
    bounded = SpiderPolicy(mode="spider", links="a::attr(href)")
    with crawl_state(tmp_path, "job-key"):
        assert checkpoint_store(START, bounded) is None
    with crawl_state(None, "job-key"):
        assert checkpoint_store(START, UNBOUNDED) is None


def test_the_key_ignores_the_time_budget_but_not_the_fields(tmp_path):
    def job(budget: int, field: str) -> Job:
        return Job.model_validate(
            {
                "format_version": 1,
                "sources": [{"url": START}],
                "fields": [{"name": field, "type": "string"}],
                "recipe": {"strategy": "css", "record_selector": "a", "fields": {field: "a::text"}},
                "acquisition": {"allowed_hosts": ["shop.example"]},
                "spider": {
                    "mode": "spider",
                    "links": "a::attr(href)",
                    "max_pages": "unbounded",
                    "time_budget_seconds": budget,
                },
            }
        )

    assert crawl_key(job(60, "name")) == crawl_key(job(600, "name"))
    assert crawl_key(job(60, "name")) != crawl_key(job(60, "title"))


def test_security_policy_describes_what_a_checkpoint_keeps():
    policy = (Path(__file__).parents[2] / "SECURITY.md").read_text(encoding="utf-8")
    paragraph = " ".join(policy.split("Unbounded crawl checkpoints", 1)[1].split("\n\n")[0].split())
    assert "frontier and the seen set" not in paragraph  # the seen set keeps no URLs
    assert "digest" in paragraph
