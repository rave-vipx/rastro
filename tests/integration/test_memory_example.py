import json
import subprocess
import sys
from pathlib import Path


def test_recipe_survives_a_real_process_restart(tmp_path):
    example = Path(__file__).parents[2] / "examples" / "memory.py"
    command = [sys.executable, str(example), "--database", str(tmp_path / "recipes.db")]
    first = json.loads(subprocess.check_output(command, text=True, timeout=20))
    second = json.loads(subprocess.check_output(command, text=True, timeout=20))
    assert first["status"] == second["status"] == "success"
    assert first["recipe_reused"] is False and second["recipe_reused"] is True
    assert first["records"] == second["records"]
