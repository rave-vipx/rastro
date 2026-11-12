"""REPAIR-002/SEC-004: OS directory aliases work without accepting job-owned symlinks."""

import pytest
from test_recipe_cli import setup_sample

from rastro.application.repair_context import RepairContext, safe_path
from rastro.cli import main
from rastro.domain.models import ConfigurationError

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


def test_job_load_and_repair_accept_symlinked_base_directory(tmp_path, capsys):
    folder, job = setup_sample(tmp_path)
    alias = tmp_path / "tmp-alias"
    alias.symlink_to(folder, target_is_directory=True)
    path = alias / job.name
    assert main(["job", "load", str(path)]) == 0
    capsys.readouterr()
    context = RepairContext.load(path, False)
    assert context.base == folder.resolve()
    assert main(["recipe", "compare", str(path), "--from-version", "1", "--to-version", "1"]) == 0
    capsys.readouterr()


def test_job_file_and_nested_input_symlinks_remain_refused(tmp_path):
    folder, job = setup_sample(tmp_path)
    alias = folder / "linked-job.toml"
    alias.symlink_to(job)
    with pytest.raises(ConfigurationError, match="symlinks"):
        RepairContext.load(alias, False)
    nested = folder / "nested"
    nested.symlink_to(folder, target_is_directory=True)
    with pytest.raises(ConfigurationError, match="symlinks"):
        safe_path("nested/job.toml", folder, False)
