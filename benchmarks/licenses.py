"""Inventory installed runtime license metadata; this does not establish legal clearance."""

import argparse
import hashlib
import json
import tomllib
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def inventory() -> dict[str, object]:
    lock = ROOT / "uv.lock"
    packages = {package["name"]: package for package in tomllib.loads(lock.read_text())["package"]}
    root = packages["rastro-engine"]
    pending = [dependency["name"] for dependency in root["dependencies"]]
    pending.extend(
        dependency["name"]
        for group in root.get("optional-dependencies", {}).values()
        for dependency in group
    )
    seen = set()
    rows = []
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        package = packages[name]
        pending.extend(dependency["name"] for dependency in package.get("dependencies", []))
        installed = distribution(name)
        if installed.version != package["version"]:
            raise ValueError(f"Installed version differs from lock: {name}")
        license_text = installed.metadata.get("License", "")
        expression = installed.metadata.get("License-Expression") or license_text.split("\n")[0]
        files = [
            {
                "path": str(path).split(".dist-info/licenses/", 1)[1],
                "sha256": hashlib.sha256(
                    Path(str(installed.locate_file(path))).read_bytes()
                ).hexdigest(),
            }
            for path in installed.files or []
            if ".dist-info/licenses/" in str(path)
        ]
        rows.append(
            {
                "name": name,
                "version": installed.version,
                "declared_license": expression,
                "license_files": files,
            }
        )
    return {
        "scope": "locked runtime graph, including http and ollama extras; installed artifacts only",
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "packages": sorted(rows, key=lambda row: str(row["name"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(inventory(), indent=2) + "\n")


if __name__ == "__main__":
    main()
