"""Failed sources are logged at WARNING with index and host, never path or query (QUAL-005)."""

import json
import logging

from rastro.application.job_loader import load_job
from rastro.application.jobs import run_job
from tests.integration.test_open_drift_load import JOB


def test_failed_source_is_logged_at_warning_without_path_or_query(tmp_path, caplog):
    job = JOB.replace("https://shop.example/list", "https://shop.example/private/list?token=s3")
    (tmp_path / "job.toml").write_text(job, encoding="utf-8")
    (tmp_path / "page.html").write_text("<html><body>secret page</body></html>")
    with caplog.at_level(logging.WARNING, logger="rastro"):
        run_job(load_job(tmp_path / "job.toml"), base_dir=tmp_path)
    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    event = json.loads(record.getMessage())
    assert event == {
        "event": "source_failed",
        "source_index": 1,
        "host": "shop.example",
        "status": "missing_data",
        "reason": None,
    }
    assert "private" not in record.getMessage() and "token" not in record.getMessage()


def test_successful_runs_log_no_warning(tmp_path, caplog):
    from tests.integration.test_open_drift_load import page

    (tmp_path / "job.toml").write_text(JOB, encoding="utf-8")
    (tmp_path / "page.html").write_text(page(2), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="rastro"):
        run_job(load_job(tmp_path / "job.toml"), base_dir=tmp_path)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
